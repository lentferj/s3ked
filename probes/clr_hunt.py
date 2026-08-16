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

"""Does any value in the load register fire the panel's CLR? (§105)

**WRITES UNKNOWN VALUES TO A REGISTER THAT IS KNOWN TO ACT. RAM only, but
this is the probe most likely to crash the machine** -- writing an
out-of-range value crashed this S3000XL twice (§85, §90), and those were a
register that merely selects a page. Somebody must be at the power switch.

WHY IT IS WORTH RUNNING

§75 says CLR cannot be fired remotely. That rests on §74's sweep, which §94
proved could detect nothing: it ran against an already-resident volume, where
a CLR value would clear memory and reload the same volume and net **zero
words** -- indistinguishable from inert. §74 also records in its own words
that "values above 7 are untested", and the eight known load types occupy 0-7
(§93), so a CLR trigger would live exactly where nobody looked.

And the method that found every other register cannot find this one: it works
by watching a SETTING the panel writes, and CLR is an ACTION that leaves
nothing behind.

THE STATE, WHICH IS THE WHOLE EXPERIMENT

A LARGE volume resident and a much SMALLER one selected, so the three
outcomes are far apart -- the rule §73 wrote and §74 broke:

    inert             free memory unchanged
    a load            free memory falls by the small volume
    a CLR-then-load   free memory JUMPS, memory holding only the small volume

Anything that moves memory re-establishes the state before the next value, so
every reading is taken from the same starting point.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")

from s3k import bridge as b, messages as m

BIG = (2, 0)            # (drive, volume) -- 28.8 MB
SMALL = (2, 2)          # 0.05 MB
LOAD_ALL = 1            # ALL PROGS+SAMPLES
#: The selected volume is deliberately tiny, so any load or
#: clear-then-load it triggers finishes quickly. 22 s was the
#: first batch's caution; the machine proved willing.
SETTLE = 10.0
MB = 1024 * 1024


def mb(words):
    return words * 2 / MB


def patient(bridge, fn, *args, tries=10, gap=2.5):
    for _ in range(tries):
        try:
            return fn(*args)
        except Exception:
            time.sleep(gap)
    raise RuntimeError("the machine stopped answering")


def free_words(bridge):
    return patient(bridge, lambda: bridge.status().free_words)


def establish(bridge):
    """Large volume resident, small volume selected."""
    patient(bridge, bridge.clear_memory)
    drive, volume = BIG
    patient(bridge, bridge.select_drive, drive)
    patient(bridge, bridge.select_volume, volume)
    time.sleep(1.0)
    bridge.trigger_load(LOAD_ALL)
    time.sleep(SETTLE * 2)
    drive, volume = SMALL
    patient(bridge, bridge.select_drive, drive)
    patient(bridge, bridge.select_volume, volume)
    time.sleep(1.0)
    return free_words(bridge)


def main() -> int:
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    bridge = b.S3kBridge.autodetect(channels=(0,))
    print(f"establishing: volume {BIG} resident, volume {SMALL} selected",
          flush=True)
    baseline = establish(bridge)
    resident = patient(bridge, bridge.program_list)
    print(f"  free {mb(baseline):.2f} MB, {len(resident)} program(s) resident",
          flush=True)
    print(f"\n  PREDICTIONS from here:", flush=True)
    print(f"    inert            free stays ~{mb(baseline):.2f} MB", flush=True)
    print(f"    a load           free falls a little", flush=True)
    print(f"    a CLR-then-load  free JUMPS to ~30 MB", flush=True)
    print(f"\n  value   free MB   programs   verdict", flush=True)
    print("  " + "-" * 56, flush=True)

    findings = []
    for value in range(first, last + 1):
        frame = m.HeaderData(
            command=m.Command.MISCDATA, index=6, selector=1, offset=0,
            data=bytes([value]), exclusive_channel=bridge.exclusive_channel,
        ).encode()
        bridge._drain()
        bridge._send(frame, write=True)
        time.sleep(SETTLE)

        try:
            now = free_words(bridge)
            programs = len(patient(bridge, bridge.program_list))
        except RuntimeError:
            print(f"  {value:>5}   *** THE MACHINE STOPPED ANSWERING ***",
                  flush=True)
            print("  power-cycle it; this value is the one that did it",
                  flush=True)
            return 2

        moved = now - baseline
        if abs(moved) < 2000:
            verdict = "inert"
        elif moved < 0:
            verdict = f"LOADED ({mb(-moved):.2f} MB taken)"
        else:
            verdict = f"*** CLEARED ({mb(moved):.2f} MB returned) ***"
        print(f"  {value:>5}   {mb(now):>6.2f}   {programs:>8}   {verdict}",
              flush=True)

        if verdict != "inert":
            findings.append((value, verdict))
            baseline = establish(bridge)
            print(f"          state re-established, free {mb(baseline):.2f} MB",
                  flush=True)

    print(flush=True)
    if findings:
        for value, verdict in findings:
            print(f"  value {value}: {verdict}", flush=True)
    else:
        print(f"  values {first}-{last} are all inert.", flush=True)
    bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
