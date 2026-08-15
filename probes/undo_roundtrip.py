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

"""read -> write -> read back -> undo -> read back, against a real machine.

**WRITES. RAM only** -- header parameters of resident programs, every one
restored by the undo under test and checked afterwards.

WHY THIS EXISTS

s3ked's undo was tested only against `DemoBridge`, and a fake cannot fail the
way this path failed: `_after_write` logged `keygroup=0` unconditionally while
the write itself went to the right keygroup, so an undo on keygroup 3 put the
old value into keygroup 0. Synthetic tests passed throughout, because the
demo's keygroup 0 accepted the write and nothing compared it to the machine.

The sibling eosed reached the same conclusion from the other direction. Its
§18 confirmed the bridge write path with 3340 comparisons and explicitly did
NOT cover "the TUI-level paths -- undo, nudge, history". Its HW_CHECKLIST
item C7 is *"Undo restores scope ... the important one -- a scope bug here is
a silent wrong-target write"*, still unticked. eosed named the failure class;
s3ked shipped it.

WHAT IT DRIVES

The **application**, not the bridge -- `S3kedApp` headless over a real
`S3kBridge`, using the same `_write_param` and `action_undo` a keypress
reaches. Testing the bridge alone would have missed this bug entirely, since
the bridge was never wrong.

Every value is verified by reading the machine back, never by trusting what
the app believes.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/home/lentferj/git-repos/s3ked")

from s3k import bridge as b
from s3k import params as p
from s3ked.app import S3kedApp

#: (region, parameter, keygroup, value to write). The NON-ZERO keygroup cases
#: are the point: that is what the undo log used to drop, and what a fake
#: bridge cannot fail on because its keygroup 0 accepts the write happily.
CASES = [
    ("program", "PRIORT", 0, 2),
    ("program", "PLAYLO", 0, 30),
    ("keygroup", "LONOTE", 0, 36),
    ("keygroup", "LONOTE", 3, 40),
    ("keygroup", "HINOTE", 10, 90),
]

ROUNDS = 3


async def settle(pilot, times=40):
    for _ in range(times):
        await pilot.pause()


def read(app, bridge, name, index, keygroup=0):
    """Read a value while holding the app's OWN bridge lock.

    The probe and the application share one MIDI port. Reading it directly
    while an app worker is mid-exchange interleaves two request/response
    pairs on the same wire, and the loser gets a timeout -- which happened
    here, at a point where a worker started by `_load_program` had not
    finished. Taking the same lock every app call takes makes the probe a
    well-behaved second user rather than a race.
    """
    with app._bridge_lock:
        return bridge.get_parameter(name, index, keygroup=keygroup)


async def main() -> int:
    bridge = b.S3kBridge.autodetect(channels=(0,))
    programs = bridge.program_list()
    if not programs:
        print("no programs resident -- load a volume first", flush=True)
        return 1

    # The program with the MOST keygroups, so the non-zero keygroup cases can
    # actually run. Picking program 0 blindly meant they were skipped, and a
    # skipped case is not a passing one.
    counts = [bridge.get_header_bytes("program", i, 42, 1)[0]
              for i in range(len(programs))]
    program = counts.index(max(counts))
    groups = counts[program]
    print(f"{len(programs)} programs resident; using program {program}, "
          f"which has {groups} keygroup(s)\n", flush=True)
    cases = [c for c in CASES if c[0] == "program" or c[2] < groups]
    if len(cases) != len(CASES):
        print(f"  skipping {len(CASES) - len(cases)} case(s) needing more "
              f"keygroups than this program has", flush=True)

    app = S3kedApp(bridge, allow_write=True)
    failures = []

    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot, 60)

        print("  case                          orig  wrote  readback  "
              "after undo  verdict", flush=True)
        print("  " + "-" * 76, flush=True)

        for region, name, keygroup, new in cases:
            param = p.lookup((region, name))
            for round_number in range(1, ROUNDS + 1):
                original = read(app, bridge, name, program, keygroup)
                target = new if original != new else new + 1

                app._param_context = (region, program, keygroup)
                app._write_param(param, original, str(target))
                await settle(pilot, 60)
                wrote = read(app, bridge, name, program, keygroup)

                await pilot.press("z")
                await settle(pilot, 60)
                restored = read(app, bridge, name, program, keygroup)

                ok = wrote == target and restored == original
                label = f"{region} {name} kg{keygroup} #{round_number}"
                print(f"  {label:<28} {original:>5} {target:>6} "
                      f"{wrote:>9} {restored:>11}  "
                      f"{'ok' if ok else 'FAIL'}", flush=True)
                if not ok:
                    failures.append(
                        f"{label}: wrote {target} read {wrote}, "
                        f"undo gave {restored} want {original}")

        # --- Z, over several edits at once --------------------------------
        print("\n  undo-all over three edits:", flush=True)
        region, name, keygroup = "keygroup", "LONOTE", min(4, groups - 1)
        param = p.lookup((region, name))
        before = read(app, bridge, name, program, keygroup)
        app._param_context = (region, program, keygroup)
        for step, value in enumerate((41, 42, 43), 1):
            app._write_param(param, read(app, bridge, name, program, keygroup), str(value))
            await settle(pilot, 60)
        mid = read(app, bridge, name, program, keygroup)

        await pilot.press("Z")
        await settle(pilot, 150)
        after = read(app, bridge, name, program, keygroup)
        ok = mid == 43 and after == before
        print(f"    {name} kg{keygroup}: {before} -> 41,42,43 -> "
              f"read {mid} -> Z -> {after}  {'ok' if ok else 'FAIL'}",
              flush=True)
        if not ok:
            failures.append(
                f"Z: started {before}, reached {mid}, ended {after}")
        if app._undo:
            failures.append(f"Z left {len(app._undo)} entries in the log")

        # --- nudge: does a run collapse, and does one z put it all back? --
        print("\n  nudge run (+ x4) on hardware:", flush=True)
        from textual.widgets import DataTable

        app._param_context = ("program", program, 0)
        app._load_program(program)
        await settle(pilot, 80)
        table = app.query_one("#parameters", DataTable)
        row = next(i for i, x in enumerate(app._param_rows)
                   if x.name == "PLAYLO")
        table.move_cursor(row=row)
        await settle(pilot, 20)

        start = read(app, bridge, "PLAYLO", program)
        logged_before = len(app._undo)
        for _ in range(4):
            await pilot.press("plus")
            await settle(pilot, 60)
        stepped = read(app, bridge, "PLAYLO", program)
        entries = len(app._undo) - logged_before
        still_on = app._param_rows[table.cursor_row].name

        await pilot.press("z")
        await settle(pilot, 80)
        back = read(app, bridge, "PLAYLO", program)

        ok = (stepped == start + 4 and entries == 1 and back == start
              and still_on == "PLAYLO")
        print(f"    PLAYLO: {start} -> +4 -> {stepped}; log grew by "
              f"{entries}; cursor on {still_on}; after z {back}  "
              f"{'ok' if ok else 'FAIL'}", flush=True)
        if stepped != start + 4:
            failures.append(f"nudge: 4 taps moved {start}->{stepped}")
        if entries != 1:
            failures.append(f"nudge run logged {entries} entries, want 1")
        if still_on != "PLAYLO":
            failures.append(f"cursor moved to {still_on} during the run")
        if back != start:
            failures.append(f"one z after a run gave {back}, want {start}")

    bridge.close()
    print(flush=True)
    if failures:
        print(f"{len(failures)} FAILURE(S):", flush=True)
        for line in failures:
            print(f"  {line}", flush=True)
        return 1
    print(f"all {len(cases) * ROUNDS + 1} round trips exact, "
          f"read back off the machine every time.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
