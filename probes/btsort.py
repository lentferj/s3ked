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

"""Does a `PRGNUM` write arriving over SysEx re-sort and re-flag by itself?

**This WRITES, and only to RAM.** One byte in one program header, restored
before it exits, including on Ctrl-C or on any error.

The specification says twice that after writing `PRGNUM`, "Miscellaneous
function BTSORT should be triggered to resort the list of programs into order
and to flag active programs" -- and never says which Data Index invokes
BTSORT, so s3ked cannot trigger it (§5). Whether that matters is a different
question, and an untested one: the machine may do it by itself for a write
that arrives over SysEx, in which case s3ked's `renumber_programs()` needs no
caveat at all, and if it does not, the caveat is "the list settles when you
touch the panel" (§91).

WHAT IT DOES

Takes the LAST program in the list and gives it the FIRST program's number,
manufacturing exactly the collision that four loaded volumes produce. Two
readings follow:

  over MIDI, by this script -- did RPLIST reorder? If the machine re-sorts,
      the last program moves and the name order changes. That half needs
      nobody.

  at the panel, by a person -- on SINGLE mode's SLCT page, does the `*`
      column follow? With the changed program's number selected, "now active"
      should count 2 rather than 1 if the flags were rebuilt.

The last program is chosen rather than the first because a re-sort has to
move it a long way to be visible, and because "did anything happen" is a much
weaker reading than "did this specific program move to the top".

The change is undone before this exits. It is a program number, not audio:
nothing is deleted and nothing is written to disc.
"""
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")
from s3k import bridge as b

PRGNUM = 15
SETTLE = 2.0


def read_state(bridge):
    names = bridge.program_list()
    numbers = [
        bridge.get_header_bytes("program", i, PRGNUM, 1)[0]
        for i in range(len(names))
    ]
    return names, numbers


def show(label, names, numbers):
    # names are from a commercial library and are never printed (CLAUDE.md);
    # their LENGTHS are enough to see a reorder, and carry nothing.
    print(f"  {label}", flush=True)
    print(f"    PRGNUM order : {numbers}", flush=True)
    print(f"    name lengths : {[len(n.strip()) for n in names]}", flush=True)


def main():
    bridge = b.S3kBridge.autodetect(channels=(0,))
    names, numbers = read_state(bridge)
    if len(names) < 3:
        print(f"only {len(names)} program(s) resident -- load a volume first; "
              f"a re-sort needs somewhere to move to", flush=True)
        return

    last = len(names) - 1
    original = numbers[last]
    target = numbers[0]
    if original == target:
        print("the last program already carries the first one's number; "
              "nothing to manufacture", flush=True)
        return

    print("=== before ===", flush=True)
    show("resident", names, numbers)
    print(f"\n  writing PRGNUM {target} onto program index {last} "
          f"(was {original}) -- collides with index 0\n", flush=True)

    restored = False
    try:
        bridge.set_header_bytes("program", last, PRGNUM, bytes([target]))
        time.sleep(SETTLE)
        after_names, after_numbers = read_state(bridge)

        print("=== after the write ===", flush=True)
        show("resident", after_names, after_numbers)

        reordered = [len(n.strip()) for n in after_names] != \
                    [len(n.strip()) for n in names]
        print(flush=True)
        if reordered:
            print("RPLIST REORDERED -- the machine re-sorted on its own, with "
                  "no BTSORT from us.", flush=True)
            print("  s3ked's renumber needs no ordering caveat.", flush=True)
        else:
            print("RPLIST UNCHANGED -- the machine did NOT re-sort.",
                  flush=True)
            print("  Note this does not settle the `*` flags, which are a "
                  "separate half of what", flush=True)
            print("  BTSORT is documented to do. Read the panel now:",
                  flush=True)
        print(flush=True)
        print("  AT THE PANEL, on SINGLE mode's SLCT page:", flush=True)
        print(f"    - select PROGRAM NUMBER {target + 1} "
              f"(the panel shows the byte 1-based)", flush=True)
        print("    - how many does it say are 'now active'? 2 means the flags "
              "were rebuilt;", flush=True)
        print("      1 means they were not.", flush=True)
        print("    - and has the list visibly reordered on screen?",
              flush=True)
        print("\n  Look now. Restoring in 60 s.", flush=True)
        time.sleep(60)
    finally:
        try:
            bridge.set_header_bytes("program", last, PRGNUM, bytes([original]))
            check = bridge.get_header_bytes("program", last, PRGNUM, 1)[0]
            restored = check == original
        except Exception as exc:          # noqa: BLE001 -- report, never mask
            print(f"\n  RESTORE FAILED: {exc}", flush=True)
        print(f"\n  restored program {last} to PRGNUM {original}: {restored}",
              flush=True)
        bridge.close()


if __name__ == "__main__":
    main()
