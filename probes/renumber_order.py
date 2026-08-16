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

"""Where does a load put new programs in `RPLIST`? (§107)

**Clears memory and loads two volumes. RAM only. Writes no `PRGNUM` until
the very last step, and reads the list before it does.** Nobody need be at
the panel, but the panel must not be TOUCHED while it runs -- see below.

THE QUESTION

`renumber_programs()` assigns position *i* the number *i*, in `RPLIST`
order. Whether that produces what a person expects depends entirely on where
a load puts new programs, which the function's own docstring says is "not
established". Reported from live use: loading two three-program volumes and
renumbering gives volume 2 the numbers 2, 4, 6 rather than 4, 5, 6.

THE EVIDENCE SO FAR, AND WHY IT IS NOT ENOUGH

mpc2emu measured six resident volumes, 30 programs, and mapped each position
to its source volume: 14 runs where grouping would give 6, with two volumes
alternating strictly for as far as the shorter one reaches. That is
interleaving by program number, and it is a clean reading.

But it was taken **after** a renumber, and two different histories produce
it:

  (a) the LOADER inserts each program in program-number order, or
  (b) the list was SORTED by program number at some point before the
      renumber -- by the panel being touched, or by the load itself.

§92 rules out one specific thing only: that a **SysEx `PRGNUM` write**
triggers a re-sort. Its discriminator was designed to be visible -- fifteen
programs, the LAST given the FIRST's number, so a sort would have moved it
the whole length of the list -- and `RPLIST` came back unchanged. That is a
real negative and it holds. It says nothing about what the LOAD does, and
nothing about what the PANEL does.

WHAT THIS PROBE DOES DIFFERENTLY

Reads `RPLIST` **before any `PRGNUM` is written**, so no transform sits
between the load and the reading. That is the whole point; everything else
here is bookkeeping.

The panel must not be touched between the loads and the first read, or (b)
becomes possible again through a door this probe cannot close.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")

from s3k import bridge as b

#: Two volumes with SEVERAL programs each -- the population §92's six-volume
#: run did not have. One program per volume makes grouping and interleaving
#: identical, which is why that run could not have answered this.
FIRST = (2, 0)
SECOND = (2, 1)
LOAD_ALL = 1
SETTLE = 12.0


def numbers(bridge):
    return bridge.program_numbers()


def load(bridge, drive, volume):
    bridge.select_drive(drive)
    bridge.select_volume(volume)
    time.sleep(1.0)
    bridge.trigger_load(LOAD_ALL)
    time.sleep(SETTLE)


def main() -> int:
    bridge = b.S3kBridge.autodetect(channels=(0,))
    print("clearing, then loading two multi-program volumes\n", flush=True)
    bridge.clear_memory()
    time.sleep(2.0)

    load(bridge, *FIRST)
    first_names = bridge.program_list()
    first_numbers = numbers(bridge)
    print(f"  volume A: {len(first_names)} programs, "
          f"PRGNUM {first_numbers}", flush=True)

    load(bridge, *SECOND)
    both = bridge.program_list()
    both_numbers = numbers(bridge)
    print(f"  after volume B: {len(both)} programs, "
          f"PRGNUM {both_numbers}", flush=True)

    # Which positions are volume A's? Names are the only handle, and they can
    # repeat -- so this counts occurrences rather than testing membership.
    remaining = list(first_names)
    origin = []
    for name in both:
        if name in remaining:
            remaining.remove(name)
            origin.append("A")
        else:
            origin.append("B")
    print(f"\n  origin by position: {' '.join(origin)}", flush=True)

    runs = 1 + sum(1 for i in range(1, len(origin))
                   if origin[i] != origin[i - 1])
    print(f"  {runs} run(s); GROUPED would give 2", flush=True)
    if runs == 2 and origin[0] == "A":
        verdict = "GROUPED -- the load appends, renumber_programs is correct"
    elif runs == 2:
        verdict = "GROUPED but volume B is FIRST -- the load prepends"
    else:
        verdict = "INTERLEAVED -- renumbering in list order cannot give B a "\
                  "contiguous range"
    print(f"  VERDICT: {verdict}", flush=True)

    print("\n  (no PRGNUM has been written at this point)", flush=True)
    print(f"  ordered by number already? "
          f"{both_numbers == sorted(both_numbers)}", flush=True)
    bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
