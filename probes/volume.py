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

"""Is the selected VOLUME readable anywhere in the miscellaneous data?

**READ-ONLY. This never writes.** In particular it never touches bytes 6-9,
which perform a load when written (§94).

§72 concluded the volume is not settable, and §70 identified `byte[49]` as
the panel's cursor value rather than a volume register -- it reads 3 while the
LOAD page shows volume 3 only because the cursor happens to be sitting on that
field. Writing it moves nothing.

Both of those conclusions were reached by sweeping the **byte bank**, and the
byte bank is one of seven. `RMISCDATA` selects the type with its selector
byte: 1 byte, 2 word, 3 dword, 4 smpte, 5 signed smpte, 6 name, 7 16-byte
flag (§5). The word bank is known to be real -- the program and sample counts
for the selected volume live at `word[0]` and `word[1]` -- and **it has never
been swept**. A volume index is a small number and would be at home there; a
volume NAME would be at home in bank 6.

So this sweeps three banks at once and watches them while a person steps the
volume at the panel, which is the method that found the partition byte (§70)
and the load type (§93). It also dumps the name bank directly, because the
answer might simply be sitting there readable: the panel is showing a volume
called something, and if any name index returns that string, that is the
register.

WHY THIS IS WORTH RE-RUNNING NOW

The last sweep of this kind desynchronised -- alternate passes returned each
index's neighbour's value, so everything above index ~15 was noise. That was
a late reply from a previous exchange being handed back as the answer to the
current one, and it is fixed: replies are now matched to the request that
asked for them (§95). A sweep run today can be trusted where that one could
not.

HOW TO RUN IT

  1. Nothing else may hold the MIDI port -- quit the TUI with `q` first.
  2. Put the sampler on the LOAD page with a volume selected.
  3. Start this. It prints a baseline, then watches.
  4. Step the volume at the panel. Wait for a line. Step it again, to a
     THIRD volume -- one change proves nothing, which is the mistake §74
     made.
  5. It stops on its own, or on Ctrl-C.

An index that tracks across two different volumes is the answer. `byte[49]`
will track too, and is not the answer -- it is the cursor, and it stops
meaning anything the moment the cursor moves off the field.
"""
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")
from s3k import bridge as b, messages as m

#: (selector, label, how many bytes one entry holds, how many to sweep).
#: Widths are the spec's own for each bank. The counts are guesses at where
#: the useful range ends and are deliberately generous.
BANKS = (
    (1, "byte", 1, 128),
    (2, "word", 2, 64),
    (3, "dword", 4, 32),
)

NAME_BANK = 6
NAME_INDICES = 32
GAP = 0.6

KNOWN = {
    (1, 0): "device type",
    (1, 2): "partition",
    (1, 4): "selection held",
    (1, 6): "LOAD TYPE -- never written by this probe",
    (1, 7): "load type (mirror)",
    (1, 8): "load type (mirror)",
    (1, 9): "load type (mirror)",
    (1, 11): "SCSI drive id",
    (1, 12): "SCSI local id",
    (1, 49): "CURSOR VALUE -- tracks the cursor, not a register",
    (1, 91): "main-menu page",
    (2, 0): "programs in the selected volume",
    (2, 1): "samples in the selected volume",
    (2, 6): "total directory entries",
}


def read_raw(bridge, selector, index, count):
    frame = m.HeaderRequest(
        command=m.Command.RMISCDATA, index=index, selector=selector,
        offset=0, count=count, exclusive_channel=bridge.exclusive_channel,
    ).encode()
    reply = bridge.send_and_receive(frame, timeout=1.5)
    _c, command, _p = m.parse_frame(reply)
    if command == m.Command.REPLY:
        return None
    return bytes(m.HeaderData.decode(reply).data)


def read_value(bridge, selector, index, width):
    raw = read_raw(bridge, selector, index, width)
    if raw is None or len(raw) < width:
        return None
    return int.from_bytes(raw[:width], "little")


def sweep(bridge, live):
    out = {}
    for key in live:
        selector, index, width = key
        try:
            value = read_value(bridge, selector, index, width)
        except Exception:
            continue
        if value is not None:
            out[key] = value
    return out


def dump_names(bridge):
    print("\n=== name bank (selector 6) ===", flush=True)
    print("  Looking for the volume the panel is showing. A name index that "
          "returns it", flush=True)
    print("  IS the register, and one that can be written would let a volume "
          "be chosen", flush=True)
    print("  by name rather than by number.\n", flush=True)
    found = False
    for index in range(NAME_INDICES):
        try:
            raw = read_raw(bridge, NAME_BANK, index, 12)
        except Exception:
            continue
        if not raw:
            continue
        text = m.decode_name(list(raw[:12]))
        if text.strip() and set(text.strip()) != {"0"}:
            print(f"   name[{index:>3}] = {text!r}", flush=True)
            found = True
    if not found:
        print("   nothing readable in the name bank", flush=True)


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    bridge = b.S3kBridge.autodetect(channels=(0,))

    dump_names(bridge)

    live = []
    for selector, label, width, count in BANKS:
        for index in range(count):
            live.append((selector, index, width))

    print(f"\n=== baseline sweep of {len(live)} entries across "
          f"{len(BANKS)} banks ===", flush=True)
    baseline = sweep(bridge, live)
    live = [key for key in live if key in baseline]
    per_bank = {}
    for selector, index, _w in live:
        per_bank[selector] = per_bank.get(selector, 0) + 1
    for selector, label, _w, _c in BANKS:
        print(f"   {label:>6} bank: {per_bank.get(selector, 0)} readable",
              flush=True)

    print(f"\n  STEP THE VOLUME AT THE PANEL NOW -- twice, to two different "
          f"volumes.", flush=True)
    print(f"  Watching for {duration:.0f}s.\n", flush=True)
    print("   time   bank  index   from -> to      note", flush=True)
    print("  " + "-" * 68, flush=True)

    labels = {selector: label for selector, label, _w, _c in BANKS}
    previous = dict(baseline)
    moved = {}
    started = time.time()
    try:
        while time.time() - started < duration:
            time.sleep(GAP)
            current = sweep(bridge, live)
            for key in live:
                if key not in current or current[key] == previous[key]:
                    continue
                selector, index, _w = key
                moved.setdefault(key, []).append(current[key])
                note = KNOWN.get((selector, index), "<< UNKNOWN -- candidate")
                print(f"  {time.time() - started:>5.0f}s  "
                      f"{labels[selector]:>6}  {index:>5}   "
                      f"{previous[key]:>5} -> {current[key]:<7} {note}",
                      flush=True)
                previous[key] = current[key]
    except KeyboardInterrupt:
        pass

    print(f"\n  stale replies skipped: {bridge.stale_replies}", flush=True)
    print("\n   bank  index  values seen           verdict", flush=True)
    print("  " + "-" * 68, flush=True)
    for key in sorted(moved):
        selector, index, _w = key
        values = sorted(set(moved[key]))
        note = KNOWN.get((selector, index))
        if note is None:
            note = ("CANDIDATE -- tracked across "
                    f"{len(values)} values" if len(values) > 1
                    else "moved once -- could be coincidence")
        print(f"  {labels[selector]:>6}  {index:>5}  {str(values):<20}  {note}",
              flush=True)
    if not moved:
        print("  nothing moved at all -- was the volume actually changed?",
              flush=True)
    bridge.close()


if __name__ == "__main__":
    main()
