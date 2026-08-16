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

"""Does `renumber_after_load` give a second volume a contiguous range? (§107)

**Clears memory and loads two volumes, then writes PRGNUM. RAM only.**

THE REPORT

"if volume 1 has P#1 #2 #3, and volume 2 has P#1 #2 #3, I would expect all
of Vol2 to become #4 #5 #6 - in the same order they are in vol2 - it seems
that doesn't work."

PREDICTIONS, written before the run

1. After loading volume A and renumbering, its programs hold 0..N-1.
2. After loading volume B, the list is INTERLEAVED and in program-number
   order -- §107, already measured. The snapshot is what makes the arrivals
   identifiable, since position no longer says which volume a program is in.
3. `renumber_after_load` leaves the incumbents on 0..N-1 unchanged and gives
   the arrivals a CONTIGUOUS block N..M-1, in the order they sit in the
   list, which is volume B's own order.
4. `RPLIST` order is UNCHANGED by the renumber, and the numbers no longer
   ascend with position. §92 measured that a SysEx PRGNUM write does not
   trigger the sort, so this is expected, not a fault -- it is the price of
   giving each volume a range of its own.

Prediction 4 is the one that could embarrass this fix, so it is checked
explicitly rather than left implicit.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")

from s3k import bridge as b

FIRST_VOLUME = 0
SECOND_VOLUME = 1
LOAD_TYPE = 1           # ALL PROGS+SAMPLES
SETTLE = 30.0


def patient(fn, *a, tries=40, gap=3.0, **kw):
    last = None
    for _ in range(tries):
        try:
            return fn(*a, timeout=8.0, **kw)
        except Exception as exc:
            last = exc
            time.sleep(gap)
    raise last


def load(bridge, volume):
    bridge.select_volume(volume)
    time.sleep(0.8)
    bridge.trigger_load(LOAD_TYPE)
    time.sleep(SETTLE)


def main() -> int:
    bridge = b.S3kBridge.autodetect(channels=(0,))
    print(f"source: {bridge.load_source()}\n", flush=True)
    failures = []

    patient(bridge.clear_memory)
    time.sleep(2.5)
    load(bridge, FIRST_VOLUME)
    patient(bridge.renumber_programs)
    before = patient(bridge.resident_pairs)
    incumbents = len(before)
    print(f"  volume A settled: {incumbents} programs, "
          f"PRGNUM {[n for _, n in before]}", flush=True)
    if [n for _, n in before] != list(range(incumbents)):
        failures.append("volume A did not settle on 0..N-1")

    load(bridge, SECOND_VOLUME)
    after = patient(bridge.resident_pairs)
    print(f"  after volume B  : {len(after)} programs", flush=True)
    print(f"    PRGNUM {[n for _, n in after]}", flush=True)
    if [n for _, n in after] != sorted(n for _, n in after):
        failures.append("the list is not in program-number order (§107)")

    order_before = [name for name, _ in after]
    result = patient(bridge.renumber_after_load, before)
    print(f"\n  {result}", flush=True)
    if result.get("unmatched"):
        failures.append(f"snapshot did not match: {result}")

    final = patient(bridge.resident_pairs)
    numbers = {name: number for name, number in final}
    arrivals = [name for name, _ in after if name not in dict(before)]

    # incumbents keep the front of the range, in their order
    kept = [numbers[name] for name, _ in before]
    print(f"\n  incumbents now: {kept}", flush=True)
    if kept != list(range(incumbents)):
        failures.append(f"incumbents moved: {kept}")

    got = [numbers[name] for name in arrivals]
    want = list(range(incumbents, incumbents + len(arrivals)))
    print(f"  arrivals now  : {got}", flush=True)
    print(f"  wanted        : {want}", flush=True)
    if got != want:
        failures.append(f"arrivals are not contiguous in order: {got}")

    if len(set(numbers.values())) != len(final):
        failures.append("two programs share a number")

    # prediction 4: the ORDER is untouched, the numbers no longer ascend
    if [name for name, _ in final] != order_before:
        failures.append("the renumber changed RPLIST order")
    ascending = [n for _, n in final] == sorted(n for _, n in final)
    print(f"\n  RPLIST order unchanged: "
          f"{[name for name, _ in final] == order_before}", flush=True)
    print(f"  numbers ascend with position: {ascending} "
          f"(expected False -- §92, the sort is BTSORT's missing half)",
          flush=True)

    bridge.close()
    print(flush=True)
    if failures:
        print(f"{len(failures)} FAILURE(S):", flush=True)
        for line in failures:
            print(f"  {line}", flush=True)
        return 1
    print(f"  volume B's {len(arrivals)} programs hold "
          f"{want[0] + 1}-{want[-1] + 1} on the panel, in volume order.",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
