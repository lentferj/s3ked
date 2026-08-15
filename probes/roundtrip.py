#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The method -- write every parameter, read it back, switch away and return,
# read again -- is ported from the sibling eosed project's first full write
# test against an E4XT Ultra (its RESOLUTION_NOTES §18), GPL-2.0-or-later.
# The parameter offsets, sizes and ranges swept are transcribed as data from
# Akai's own S2800/S3000/S3200 document; see LICENSE and §1.
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

"""Write every safe parameter, read it back, go away, come back, read again.

    probes/roundtrip.py --dry-run
    probes/roundtrip.py --allow-write --out roundtrip.json

**This writes to the machine.** It needs ``--allow-write``; without it the
whole sweep runs as a rehearsal that reads and plans but sends nothing.

Per parameter, five steps -- the shape eosed used on the E4XT (§18 there):

1. read the original value;
2. write **A**, read back;
3. write **B**, read back;
4. write **A** again, read back  -- the switch back and forth;
5. restore the original, read back.

**Why steps 3-4 are not redundant.** On this family an out-of-range read
returns the previous read's buffer rather than an error (§11 Finding A), so a
single write-then-read proves very little: the value coming back may be an
echo of what we just sent through a buffer that never reached the header.
Writing a *second, different* value and seeing the read follow it, then
returning to the first and seeing it follow back, is what distinguishes a
stored value from an echo.

The same reasoning drives ``--interleave`` (on by default): between the write
and the read-back, a *different* structure is read. That evicts whatever the
transfer buffer held, so the read-back has to come from the header itself.
eosed's equivalent was selecting away to another preset and returning, which
tested `PRESET_SELECT` scoping; here it tests the buffer.

**What is deliberately not swept**, and why -- see :data:`SKIP_NAMES` and the
size rule in :func:`sweepable`:

* deletes and whole-structure writes: never, in any mode. ``PDATA``/``KDATA``
  can destroy a program the caller never named (§1), and ``DELP``/``DELK``/
  ``DELS`` take no confirmation.
* anything ``params.py`` marks read-only or ``kind="address"``: block
  addresses and absolute memory locations the machine keeps its own
  bookkeeping in.
* every field wider than two bytes. In the sample header those are all
  memory-layout descriptors -- ``SLNGTH``, ``SSTART``, ``SMPEND``,
  ``LOOPAT*``, ``LLNGTH*``, ``SLXY*`` -- and a wrong one points the machine at
  audio that is not there. It also catches ``RESERVED`` and ``TEMPER``, which
  are not scalars and have no meaningful "next value".
* ``GROUPS``: declares how many keygroups the program has. Raising it makes
  the machine walk keygroup pointers that were never allocated.
* ``PRGNUM``: the program's own number, which reorders the program list; §5
  says renumbering is a panel operation.

Restoration is attempted for every parameter the sweep touched, including
after an abort, and the restore is verified by reading it back.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import bridge as b  # noqa: E402
from s3k import messages as m  # noqa: E402
from s3k import params as p  # noqa: E402

#: Fields excluded by name regardless of anything else. See the module
#: docstring for why each one is here.
SKIP_NAMES = frozenset({"GROUPS", "PRGNUM", "RESERVED"})

#: Widest field this sweep will write. Everything above it in these headers
#: is a memory-layout descriptor or a packed table, not a scalar parameter.
MAX_WIDTH = 2

#: Doubled from the module default. `SEND_GAP` is a guess that has never been
#: walked down against hardware (§6), and a write sweep is the wrong place to
#: discover the floor -- a dropped write is silent.
WRITE_GAP = 0.1


def sweepable(param: p.Parameter, *, include_names: bool) -> Tuple[bool, str]:
    """Whether this parameter may be written, and why not when it may not."""
    if not param.writable:
        return False, "read-only" if param.readonly else "internal address"
    if param.name in SKIP_NAMES:
        return False, "structurally unsafe (see SKIP_NAMES)"
    if param.kind == "text":
        return (include_names, "" if include_names else "name field (--include-names)")
    if param.size > MAX_WIDTH:
        return False, f"{param.size} bytes wide -- not a scalar"
    return True, ""


def two_values(param: p.Parameter, original: int) -> Optional[Tuple[int, int]]:
    """Pick two in-range values to toggle between, both unlike *original*.

    Values are in display space, the same space ``get_parameter`` returns and
    ``set_parameter`` accepts. Returns None for a range with nothing to say --
    a field whose minimum equals its maximum cannot demonstrate anything.
    """
    low = param.minimum + param.display_offset
    high = param.maximum + param.display_offset
    if low >= high:
        return None

    # Endpoints are the most informative for a narrow field: they exercise
    # the full byte pattern and catch a width or signedness error. For a wide
    # range, stay modest -- writing four billion into a two-byte field says
    # nothing the endpoints of a small one do not.
    span = high - low
    candidates = [low, high] if span <= 255 else [low, low + 1, low + 100]
    candidates += [original + 1, original - 1, (low + high) // 2]

    picked = []
    for value in candidates:
        if low <= value <= high and value != original and value not in picked:
            picked.append(value)
        if len(picked) == 2:
            return picked[0], picked[1]
    return None


@dataclass
class Result:
    region: str
    index: int
    keygroup: int
    name: str
    offset: int
    original: object = None
    steps: List[dict] = field(default_factory=list)
    restored: Optional[bool] = None

    @property
    def ok(self) -> bool:
        return all(s["ok"] for s in self.steps) and self.restored is not False

    def failures(self) -> List[dict]:
        return [s for s in self.steps if not s["ok"]]


@dataclass
class Report:
    results: List[Result] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    writes: int = 0
    reads: int = 0
    seconds: float = 0.0
    aborted: Optional[str] = None
    unrestored: List[str] = field(default_factory=list)
    leaked: List[dict] = field(default_factory=list)
    witnessed: int = 0


def snapshot(bridge, structures, *, timeout: float) -> Dict[str, bytes]:
    """Read whole headers verbatim, to be re-read and diffed afterwards.

    The point is bytes, not decoded fields: a leak into a span the parameter
    table does not describe is exactly the kind this would otherwise miss.
    """
    out: Dict[str, bytes] = {}
    for region, index, keygroup in structures:
        extent = max(x.end for x in p.region_params(region))
        try:
            out[f"{region}:{index}:{keygroup}"] = bridge.get_header_bytes(
                region, index, 0, extent,
                selector=keygroup if region == "keygroup" else 0,
                timeout=timeout,
            )
        except Exception:
            continue
    return out


def diff_snapshots(before: Dict[str, bytes], after: Dict[str, bytes]) -> List[dict]:
    """Every byte that changed in a structure nothing was supposed to touch."""
    findings = []
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            findings.append({"structure": key, "error": "could not re-read"})
            continue
        region = key.split(":")[0]
        changed = [i for i in range(min(len(old), len(new))) if old[i] != new[i]]
        if not changed:
            continue
        named = []
        for offset in changed:
            hit = [x.name for x in p.region_params(region)
                   if x.offset <= offset < x.end]
            named.append(f"{offset}({hit[0] if hit else 'undescribed'})")
        findings.append({
            "structure": key,
            "changed_bytes": changed[:64],
            "count": len(changed),
            "fields": named[:32],
        })
    return findings


class Sweeper:
    def __init__(self, bridge, *, allow_write: bool, interleave: bool,
                 timeout: float, verbose: bool = False):
        self.bridge = bridge
        self.allow_write = allow_write
        self.interleave = interleave
        self.timeout = timeout
        self.verbose = verbose
        self.report = Report()
        self._decoy: Optional[Tuple[str, int, int]] = None

    # -- primitives ---------------------------------------------------------

    def set_decoy(self, target: Optional[Tuple[str, int, int]]) -> None:
        """A structure to read between a write and its read-back."""
        self._decoy = target

    def _evict(self) -> None:
        """Read something else, so a read-back cannot be a buffer echo."""
        if not (self.interleave and self._decoy):
            return
        region, index, keygroup = self._decoy
        try:
            self.bridge.get_header_bytes(
                region, index, 0, 4,
                selector=keygroup if region == "keygroup" else 0,
                timeout=self.timeout,
            )
            self.report.reads += 1
        except Exception:
            pass  # the eviction failing is not a result about the parameter

    def read(self, param, index, keygroup):
        value = self.bridge.get_parameter(
            param, index, keygroup=keygroup, timeout=self.timeout
        )
        self.report.reads += 1
        return value

    def write(self, param, index, keygroup, value) -> None:
        if not self.allow_write:
            return
        self.bridge.set_parameter(
            param, index, value, keygroup=keygroup,
            postpone=m.Postpone.NONE,  # let the machine redraw and recalculate
            confirm=True,              # wait for REPLY; §11 says this is the
            timeout=self.timeout,      # only sound confirmation there is
        )
        self.report.writes += 1

    # -- one parameter ------------------------------------------------------

    def sweep_one(self, param, index, keygroup) -> Optional[Result]:
        result = Result(param.region, index, keygroup, param.name, param.offset)
        original = self.read(param, index, keygroup)
        result.original = original

        if param.kind == "text":
            values = ("SWEEPTEST01", "SWEEPTEST02")
        else:
            chosen = two_values(param, int(original))
            if chosen is None:
                return None
            values = chosen

        a, bb = values
        touched = False
        try:
            for label, want in (("write A", a), ("write B", bb), ("back to A", a)):
                self.write(param, index, keygroup, want)
                touched = True
                self._evict()
                got = self.read(param, index, keygroup)
                ok = (got == want) if self.allow_write else True
                result.steps.append(
                    {"step": label, "wrote": want, "read": got, "ok": ok}
                )
                if not ok:
                    break
        finally:
            if touched and self.allow_write:
                try:
                    self.write(param, index, keygroup, original)
                    self._evict()
                    result.restored = self.read(param, index, keygroup) == original
                except Exception:
                    result.restored = False
                if result.restored is False:
                    self.report.unrestored.append(
                        f"{param.region} {index} {param.name} (wanted {original!r})"
                    )
        return result

    # -- the whole sweep ----------------------------------------------------

    def run(self, targets, *, include_names: bool, stop_after: int) -> Report:
        started = time.time()
        failures = 0

        # Prefer a structure nothing is sweeping, so the eviction read can
        # never be confused with the parameter under test. A resident sample
        # the sweep does not touch is ideal; failing that, any other target.
        spare = None
        try:
            residents = self.bridge.sample_list()
            swept = {t[1] for t in targets if t[0] == "sample"}
            free = [i for i in range(len(residents)) if i not in swept]
            if free:
                spare = ("sample", free[-1], 0)
        except Exception:
            pass

        for region, index, keygroup in targets:
            others = [t for t in targets if t != (region, index, keygroup)]
            self.set_decoy(spare or (others[0] if others else None))

            for param in p.region_params(region):
                allowed, why = sweepable(param, include_names=include_names)
                if not allowed:
                    self.report.skipped[f"{region}.{param.name}"] = why
                    continue
                try:
                    result = self.sweep_one(param, index, keygroup)
                except Exception as exc:
                    result = Result(region, index, keygroup, param.name, param.offset)
                    result.steps.append(
                        {"step": "exception", "wrote": None, "read": None,
                         "ok": False, "error": str(exc)}
                    )
                if result is None:
                    self.report.skipped[f"{region}.{param.name}"] = "range has one value"
                    continue

                self.report.results.append(result)
                if not result.ok:
                    failures += 1
                    if self.verbose:
                        print(f"  MISMATCH {region} {param.name}: "
                              f"{result.failures()}", file=sys.stderr)
                    if stop_after and failures >= stop_after:
                        self.report.aborted = (
                            f"stopped after {failures} failing parameters"
                        )
                        self.report.seconds = time.time() - started
                        return self.report

        self.report.seconds = time.time() - started
        return self.report


# --- reporting --------------------------------------------------------------


def summarise(report: Report, allow_write: bool) -> None:
    total = len(report.results)
    good = [r for r in report.results if r.ok]
    bad = [r for r in report.results if not r.ok]

    print()
    if not allow_write:
        print("REHEARSAL -- nothing was written.")
    print(f"{total} parameters swept, {report.writes} writes, "
          f"{report.reads} reads, {report.seconds:.1f}s")
    print(f"  round-tripped exactly : {len(good)}")
    print(f"  mismatched            : {len(bad)}")
    print(f"  skipped               : {len(report.skipped)}")

    if report.witnessed:
        print(f"  untouched structures watched : {report.witnessed}"
              f"{' -- all unchanged' if not report.leaked else ''}")

    if report.unrestored:
        print()
        print("!!! NOT RESTORED -- these still hold a swept value:")
        for item in report.unrestored:
            print(f"    {item}")

    if report.leaked:
        print()
        print("!!! LEAKED -- structures nothing addressed came back different:")
        for item in report.leaked:
            if "error" in item:
                print(f"    {item['structure']}: {item['error']}")
            else:
                print(f"    {item['structure']}: {item['count']} bytes changed")
                print(f"      {', '.join(item['fields'])}")

    if bad:
        print()
        print("--- mismatches ---")
        for r in bad:
            for step in r.failures():
                if "error" in step:
                    print(f"  {r.region} {r.name} (offset {r.offset}): "
                          f"{step['error']}")
                else:
                    print(f"  {r.region} {r.name} (offset {r.offset}) "
                          f"{step['step']}: wrote {step['wrote']!r}, "
                          f"read {step['read']!r}")

    if report.aborted:
        print()
        print(f"ABORTED: {report.aborted}")

    reasons: Dict[str, int] = {}
    for why in report.skipped.values():
        reasons[why] = reasons.get(why, 0) + 1
    if reasons:
        print()
        print("--- skipped, by reason ---")
        for why, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>4}  {why}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--allow-write", action="store_true",
                    help="actually write; without it this is a rehearsal")
    ap.add_argument("--dry-run", action="store_true",
                    help="run against the demo sampler; opens no MIDI port")
    ap.add_argument("--port", help="MIDI port name (default: autodetect)")
    ap.add_argument("--exclusive-channel", type=int, default=0)
    ap.add_argument("--program", type=int, default=0, help="program to sweep")
    ap.add_argument("--keygroup", default="0",
                    help="keygroup(s) to sweep, comma-separated (default 0)")
    ap.add_argument("--sample", type=int, default=0, help="sample header to sweep")
    ap.add_argument("--regions", default="program,keygroup,sample",
                    help="comma-separated subset to sweep")
    ap.add_argument("--include-names", action="store_true",
                    help="also sweep text/name fields")
    ap.add_argument("--no-interleave", action="store_true",
                    help="skip the decoy read between write and read-back")
    ap.add_argument("--gap", type=float, default=WRITE_GAP,
                    help=f"seconds between sends (default {WRITE_GAP})")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--stop-after", type=int, default=5,
                    help="abort after this many failing parameters (0 = never)")
    ap.add_argument("--witness", action="store_true",
                    help="snapshot every structure NOT being swept, and diff "
                         "it afterwards -- catches writes leaking across items")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", help="write the full report as JSON here")
    args = ap.parse_args(argv)

    if args.dry_run:
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
    elif args.port:
        bridge = b.S3kBridge.standard(
            args.port, exclusive_channel=args.exclusive_channel,
            gap=args.gap, write_gap=args.gap,
        )
    else:
        bridge = b.S3kBridge.autodetect(
            channels=(args.exclusive_channel,), gap=args.gap, write_gap=args.gap,
            on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        )

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    targets = []
    for region in regions:
        if region == "program":
            targets.append(("program", args.program, 0))
        elif region == "keygroup":
            for kg in [int(x) for x in str(args.keygroup).split(",") if x.strip()]:
                targets.append(("keygroup", args.program, kg))
        elif region == "sample":
            targets.append(("sample", args.sample, 0))
        else:
            raise SystemExit(f"cannot sweep region {region!r}")

    sweeper = Sweeper(
        bridge,
        allow_write=args.allow_write,
        interleave=not args.no_interleave,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    witnesses = []
    if args.witness:
        # Everything resident that the sweep is not aiming at. If a write
        # lands somewhere it was never addressed to, this is what sees it.
        try:
            programs = range(len(bridge.program_list()))
            samples = range(len(bridge.sample_list()))
        except Exception:
            programs, samples = range(1), range(1)
        for i in programs:
            candidates = [("program", i, 0)]
            candidates += [("keygroup", i, kg) for kg in range(4)]
            for candidate in candidates:
                if candidate not in targets:
                    witnesses.append(candidate)
        witnesses += [("sample", i, 0) for i in samples
                      if ("sample", i, 0) not in targets]

    if args.allow_write:
        print(f"WRITING to {bridge.description}")
        print(f"  targets: {targets}")
        print(f"  gap {args.gap}s, restore-after-each, "
              f"{'interleaved' if not args.no_interleave else 'not interleaved'}")
        if witnesses:
            print(f"  witnessing {len(witnesses)} untouched structures")
    try:
        before = snapshot(bridge, witnesses, timeout=args.timeout) if witnesses else {}
        report = sweeper.run(
            targets, include_names=args.include_names, stop_after=args.stop_after
        )
        if before:
            after = snapshot(bridge, list(witnesses), timeout=args.timeout)
            report.witnessed = len(before)
            report.leaked = diff_snapshots(before, after)
    finally:
        if hasattr(bridge, "close"):
            bridge.close()

    summarise(report, args.allow_write)

    if args.out:
        payload = json.dumps({
            "results": [vars(r) for r in report.results],
            "skipped": report.skipped,
            "writes": report.writes,
            "reads": report.reads,
            "seconds": round(report.seconds, 2),
            "aborted": report.aborted,
            "unrestored": report.unrestored,
        }, indent=2, default=str)
        Path(args.out).write_text(payload, encoding="utf-8")
        print()
        print(f"full report written to {args.out}")

    return 1 if (report.unrestored or report.aborted or report.leaked) else 0


if __name__ == "__main__":
    raise SystemExit(main())
