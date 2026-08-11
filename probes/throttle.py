#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.

"""Find the real SysEx send gap for this family (RESOLUTION_NOTES §6).

    probes/throttle.py --dry-run
    probes/throttle.py --reads
    probes/throttle.py --reads --writes --allow-write --program 1

`SEND_GAP` is 0.05 s and has never been anything but a guess. k2kremote's
reverse-engineered 120 ms is a Kurzweil finding and does not transfer.

**Small frames, not whole headers.** §6 proposed looping a whole-header read.
That is a fine load test but it cannot find a floor: at 31250 baud a byte
costs 320 us, so a 192-byte header comes back as ~400 nibbled wire bytes,
about 128 ms, and any gap below that is invisible underneath the transfer.
This probe uses single-parameter frames -- roughly 12 bytes out, 14 back --
where the gap is the dominant term and a floor can actually show.

Three stages, in increasing order of risk:

1. **reads** -- request/reply, paced by the reply itself. Safe: a failure is a
   timeout or a garbled frame, and nothing on the device changes.
2. **acknowledged writes** -- each waits for `REPLY`, so the device paces us.
3. **fire-and-forget bursts** -- N writes with no reply waited for, which is
   what `write_gap` actually exists to protect. This is the one that can
   overrun an input buffer, and the failure is silent (§12): the burst is
   verified afterwards by reading back.

Between every level the device is pinged at a known-safe gap. If it stops
answering, the level just tried is what broke it -- and that ping is how a
hang gets detected before the next, faster level makes it worse.

**Watch the front panel while this runs.** A vintage sampler can garble its
display or wedge under a MIDI flood long before it starts dropping frames,
and nothing on this side can see that happen.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import bridge as b  # noqa: E402
from s3k import messages as m  # noqa: E402
from s3k import params as p  # noqa: E402

#: Gaps to walk down, in seconds. Starts at the current guess.
LADDER = (0.050, 0.025, 0.010, 0.005, 0.002, 0.001, 0.000)

#: Gap used for the between-levels health check. Deliberately generous.
SAFE_GAP = 0.10


@dataclass
class Level:
    gap: float
    stage: str
    attempts: int = 0
    failures: int = 0
    seconds: float = 0.0
    detail: str = ""
    healthy_after: Optional[bool] = None

    @property
    def rate(self) -> float:
        return self.attempts / self.seconds if self.seconds else 0.0

    @property
    def ok(self) -> bool:
        return self.failures == 0 and self.healthy_after is not False

    def line(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        health = "" if self.healthy_after is None else (
            "" if self.healthy_after else "  device unresponsive afterwards"
        )
        return (
            f"  [{mark}] {self.stage:<22} gap {self.gap * 1000:6.1f} ms  "
            f"{self.attempts - self.failures}/{self.attempts} ok  "
            f"{self.rate:5.1f}/s{health}"
            + (f"  -- {self.detail}" if self.detail else "")
        )


@dataclass
class Report:
    levels: List[Level] = field(default_factory=list)
    floors: dict = field(default_factory=dict)
    aborted: Optional[str] = None


class Throttle:
    def __init__(self, bridge, *, program: int, timeout: float, count: int):
        self.bridge = bridge
        self.program = program
        self.timeout = timeout
        self.count = count
        self.report = Report()

    # -- helpers ------------------------------------------------------------

    def _set_gaps(self, gap: float, write_gap: Optional[float] = None) -> None:
        out = self.bridge.out
        out._gap = gap
        out._write_gap = gap if write_gap is None else write_gap
        out._owed = 0.0

    def healthy(self) -> bool:
        """Can the device still answer at a generous gap?"""
        self._set_gaps(SAFE_GAP)
        time.sleep(0.3)
        for _ in range(3):
            try:
                self.bridge.status(timeout=max(self.timeout, 2.0))
                return True
            except Exception:
                time.sleep(0.5)
        return False

    # -- stages -------------------------------------------------------------

    def stage_reads(self, gap: float, param, expect) -> Level:
        level = Level(gap, "single-param read")
        self._set_gaps(gap)
        started = time.time()
        for _ in range(self.count):
            level.attempts += 1
            try:
                got = self.bridge.get_parameter(
                    param, self.program, timeout=self.timeout
                )
                if got != expect:
                    level.failures += 1
                    if not level.detail:
                        level.detail = f"read {got!r}, expected {expect!r}"
            except Exception as exc:
                level.failures += 1
                if not level.detail:
                    level.detail = f"{type(exc).__name__}: {exc}"
        level.seconds = time.time() - started
        return level

    def stage_acked_writes(self, gap: float, param, values) -> Level:
        level = Level(gap, "acknowledged write")
        self._set_gaps(gap, gap)
        started = time.time()
        for i in range(self.count):
            want = values[i % len(values)]
            level.attempts += 1
            try:
                self.bridge.set_parameter(
                    param, self.program, want, confirm=True, timeout=self.timeout
                )
            except Exception as exc:
                level.failures += 1
                if not level.detail:
                    level.detail = f"{type(exc).__name__}: {exc}"
        level.seconds = time.time() - started
        return level

    def _drain_replies(self, expect: int, quiet: float = 1.0,
                       limit: float = 60.0) -> tuple:
        """Collect acknowledgements until the device goes quiet.

        The count is the measurement: the device answers every write with a
        REPLY, so N sent against N returned is direct evidence that nothing
        was dropped -- far better than inferring it from the final value,
        which only ever reveals a lost *last* write.
        """
        seen = errors = 0
        started = time.time()
        last = time.time()
        while time.time() - started < limit:
            message = self.bridge.inp.get_message()
            if message is None:
                if time.time() - last > quiet and seen >= expect:
                    break
                if time.time() - last > quiet * 4:
                    break  # gone quiet without answering everything
                time.sleep(0.002)
                continue
            last = time.time()
            data = bytes(message[0])
            if not data or data[0] != m.SOX:
                continue
            try:
                _channel, command, payload = m.parse_frame(data)
            except ValueError:
                errors += 1
                continue
            if command == m.Command.REPLY:
                seen += 1
                if payload and payload[0]:
                    errors += 1
        return seen, errors, time.time() - started

    def stage_burst(self, gap: float, param, values) -> Level:
        """Fire-and-forget: no reply waited for. The dangerous one."""
        level = Level(gap, "fire-and-forget burst")
        self._set_gaps(gap, gap)
        self.bridge._drain()
        started = time.time()
        last = None
        try:
            for i in range(self.count):
                last = values[i % len(values)]
                level.attempts += 1
                self.bridge.set_parameter(
                    param, self.program, last, confirm=False, timeout=self.timeout
                )
        except Exception as exc:
            level.failures += 1
            level.detail = f"{type(exc).__name__}: {exc}"
        level.seconds = time.time() - started

        # Every write is acknowledged, so N sent must produce N replies.
        acks, bad, drain_s = self._drain_replies(level.attempts)
        lost = level.attempts - acks
        if lost > 0:
            level.failures += lost
            level.detail = f"{lost} of {level.attempts} writes never acknowledged"
        if bad:
            level.failures += bad
            level.detail = (level.detail + "; " if level.detail else "") + \
                f"{bad} error replies"

        self._set_gaps(SAFE_GAP)
        try:
            got = self.bridge.get_parameter(param, self.program, timeout=2.0)
            if got != last:
                level.failures += 1
                level.detail = (level.detail + "; " if level.detail else "") + \
                    f"final value is {got!r}, not {last!r}"
        except Exception as exc:
            level.failures += 1
            level.detail = (level.detail + "; " if level.detail else "") + \
                f"verify failed: {exc}"

        level.detail = (level.detail + "  " if level.detail else "") + \
            f"({acks} acks, drained in {drain_s:.1f}s)"
        return level

    # -- the walk -----------------------------------------------------------

    def walk(self, stage: str, runner, *args) -> Optional[float]:
        """Descend the ladder until something fails. Returns the last good gap."""
        good = None
        for gap in LADDER:
            level = runner(gap, *args)
            level.healthy_after = self.healthy()
            self.report.levels.append(level)
            print(level.line(), flush=True)
            if not level.ok:
                break
            good = gap
        self.report.floors[stage] = good
        return good


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--exclusive-channel", type=int, default=0)
    ap.add_argument("--program", type=int, default=1,
                    help="scratch program for the write stages (default 1)")
    ap.add_argument("--reads", action="store_true", help="run the read walk")
    ap.add_argument("--writes", action="store_true",
                    help="run both write walks (needs --allow-write)")
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--count", type=int, default=40,
                    help="exchanges per level (default 40)")
    ap.add_argument("--timeout", type=float, default=1.5)
    ap.add_argument("--ladder", default=None,
                    help="comma-separated gaps in ms, instead of the default walk")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    if not (args.reads or args.writes):
        args.reads = True

    if args.dry_run:
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
        if not hasattr(bridge, "out"):
            class _Out:
                _gap = _write_gap = _owed = 0.0
            bridge.out = _Out()
    elif args.port:
        bridge = b.S3kBridge.standard(
            args.port, exclusive_channel=args.exclusive_channel)
    else:
        bridge = b.S3kBridge.autodetect(
            channels=(args.exclusive_channel,),
            on_try=lambda name: print(f"  probing {name}...", file=sys.stderr))

    if args.ladder:
        global LADDER
        LADDER = tuple(float(x) / 1000 for x in args.ladder.split(","))

    probe = Throttle(bridge, program=args.program, timeout=args.timeout,
                     count=args.count)
    param = p.lookup(("program", "PRIORT"))     # 0..3, harmless, restorable
    original = bridge.get_parameter(param, args.program)
    print(f"scratch: program {args.program}, PRIORT (currently "
          f"{p.describe_value(param, original)})")
    print("WATCH THE FRONT PANEL -- a flood can garble a display long before "
          "it drops a frame.")
    print()

    try:
        if args.reads:
            print("reads (safe: nothing on the device changes)")
            probe.walk("read", probe.stage_reads, param, original)
            print()

        if args.writes:
            if not args.allow_write:
                print("write walks skipped: pass --allow-write to run them")
            else:
                values = [v for v in (0, 1, 2, 3) if v != original] + [original]
                print("acknowledged writes (each waits for REPLY)")
                probe.walk("write_acked", probe.stage_acked_writes, param, values)
                print()
                print("fire-and-forget bursts (no reply waited for)")
                probe.walk("write_burst", probe.stage_burst, param, values)
                print()
    finally:
        try:
            probe._set_gaps(SAFE_GAP)
            time.sleep(0.3)
            if args.allow_write:
                bridge.set_parameter(param, args.program, original, timeout=2.0)
            print(f"restored PRIORT -> "
                  f"{p.describe_value(param, bridge.get_parameter(param, args.program))}")
        except Exception as exc:
            print(f"!!! could not restore PRIORT to {original!r}: {exc}")
        if hasattr(bridge, "close"):
            bridge.close()

    print()
    print("--- floors (last gap with no failure) ---")
    for stage, gap in probe.report.floors.items():
        if gap is None:
            print(f"  {stage:<12} failed even at {LADDER[0] * 1000:.0f} ms")
        else:
            print(f"  {stage:<12} {gap * 1000:.0f} ms")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "levels": [vars(x) for x in probe.report.levels],
            "floors": probe.report.floors,
        }, indent=2, default=str))
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
