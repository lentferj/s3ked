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

"""Is the LOAD page's *type* setting readable anywhere in the misc byte bank?

**READ-ONLY. This never writes.**

s3ked can trigger a load and cannot say what kind of load it will be. The
trigger register (bytes 6-9) is not it: value 1 acts and 0 and 2-7 store
cleanly and do nothing (§74). So the load does whatever the panel's LOAD page
is showing -- ENTIRE VOLUME, ALL PROGS + SAMPLES, one program -- and nothing
in this project can read or set that.

If a register for it exists, this is how §70 found the partition byte: watch
the whole bank while a person changes the setting at the panel, and see which
index moves. Rather than snapshot-change-snapshot, this sweeps continuously
and prints changes as they happen, so the operator can work at the panel and
read the answer off the terminal without switching between them.

Two indices are expected to move and are not the answer:

  byte[49]  the CURSOR VALUE -- the value of whichever field the panel's
            cursor is on (§70). It will track the load type while the cursor
            sits on that field, and stop meaning it the moment the cursor
            moves. That is a reading of where the operator is pointing, not
            of what the machine is set to, so it cannot be the register even
            though it will look like one.
  byte[91]  the main-menu page, if the operator leaves the LOAD page.

Anything else that tracks the setting across two different values IS the
answer. One value proves nothing -- a byte that happened to change at the
same time is indistinguishable from a byte that changed because of it, which
is the mistake §74 records.

HOW TO RUN IT

  1. Put the sampler on the LOAD page and select a volume.
  2. Start this. It prints a baseline and then watches.
  3. Move the cursor onto the load-type field. Change it. Wait for a line.
  4. Change it again, to a third setting.
  5. Ctrl-C. The summary lists every index that moved and how often.

Polling is safe here in a way it is not during a load: §71's wedge was RSTAT
every 8 s while 58.7 MB was moving, and §74's sweep polled a quiet bus for
several minutes with no trouble. Do not run this while a load is in flight.

IF NOTHING MOVES

The byte bank is only one of the miscellaneous banks -- the selector chooses
between byte, word, dword, smpte, signed smpte, name and 16-byte flag (§5).
This sweeps the byte bank because a setting with a handful of values belongs
there, but a null here does not mean the setting is unreachable; it means the
next thing to sweep is the word bank, by passing selector=2 in `_misc_byte`'s
place. A null result is worth recording either way: "swept 0-127 of the byte
bank across three settings, nothing tracked" is a finding, and re-running it
later costs nothing to avoid only if it was written down.
"""
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")
from s3k import bridge as b

#: How far up the byte bank to look. The highest index this project has a
#: meaning for is 91 (the main-menu page); reading past the end of the bank
#: is what the first sweep is for -- indices that error are dropped.
HIGHEST = 127

#: Seconds between sweeps. A full sweep is one round trip per index, so this
#: is a pause on top of an already unhurried pass.
GAP = 0.5

KNOWN = {
    0: "device type",
    2: "partition",
    4: "selection held",
    6: "load trigger", 7: "load trigger", 8: "load trigger", 9: "load trigger",
    11: "SCSI drive id",
    12: "SCSI local id",
    49: "CURSOR VALUE -- tracks the field the cursor is on, not a register",
    91: "main-menu page",
}


def sweep(bridge, indices):
    out = {}
    for index in indices:
        try:
            out[index] = bridge._misc_byte(index)
        except Exception:
            pass
    return out


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    bridge = b.S3kBridge.autodetect(channels=(0,))
    print(f"baseline sweep… (watching for {duration:.0f} s, "
          f"or until Ctrl-C)", flush=True)
    baseline = sweep(bridge, range(HIGHEST + 1))
    live = sorted(baseline)
    print(f"  {len(live)} readable indices, 0-{max(live)}\n", flush=True)
    print(f"  page is {bridge.MODES.get(baseline.get(91), '?')}; "
          f"change the load type at the panel now\n", flush=True)
    print("   time  index  from -> to   note", flush=True)
    print("  " + "-" * 62, flush=True)

    previous = dict(baseline)
    moved = {}
    started = time.time()
    try:
        while time.time() - started < duration:
            time.sleep(GAP)
            current = sweep(bridge, live)
            for index in live:
                if index not in current or current[index] == previous[index]:
                    continue
                moved.setdefault(index, []).append(current[index])
                print(f"  {time.time() - started:>5.0f}s  {index:>5}  "
                      f"{previous[index]:>4} -> {current[index]:<4}  "
                      f"{KNOWN.get(index, '<< UNKNOWN -- candidate')}",
                      flush=True)
                previous[index] = current[index]
    except KeyboardInterrupt:
        pass

    print("\n  index  distinct values seen  verdict", flush=True)
    print("  " + "-" * 62, flush=True)
    for index in sorted(moved):
        values = moved[index]
        note = KNOWN.get(index)
        if note is None:
            note = ("CANDIDATE -- tracked across "
                    f"{len(set(values))} value(s)"
                    if len(set(values)) > 1 else
                    "moved once only -- could be coincidence")
        print(f"  {index:>5}  {sorted(set(values))!s:<20}  {note}", flush=True)
    if not moved:
        print("  nothing moved at all -- was the setting actually changed?",
              flush=True)
    bridge.close()


if __name__ == "__main__":
    main()
