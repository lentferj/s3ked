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

#: (region, parameter, keygroup, value to write). The keygroup cases are the
#: point: a non-zero keygroup is what the log used to drop.
CASES = [
    ("program", "PRIORT", 0, 2),
    ("program", "PLAYLO", 0, 30),
    ("keygroup", "LONOTE", 0, 36),
    ("keygroup", "LONOTE", 3, 40),
    ("keygroup", "HINOTE", 5, 90),
]

ROUNDS = 3


async def settle(pilot, times=40):
    for _ in range(times):
        await pilot.pause()


async def main() -> int:
    bridge = b.S3kBridge.autodetect(channels=(0,))
    programs = bridge.program_list()
    if not programs:
        print("no programs resident -- load a volume first", flush=True)
        return 1

    groups = bridge.get_header_bytes("program", 0, 42, 1)[0]
    print(f"program 0 has {groups} keygroup(s); {len(programs)} resident\n",
          flush=True)
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
                original = bridge.get_parameter(name, 0, keygroup=keygroup)
                target = new if original != new else new + 1

                app._param_context = (region, 0, keygroup)
                app._write_param(param, original, str(target))
                await settle(pilot, 60)
                wrote = bridge.get_parameter(name, 0, keygroup=keygroup)

                await pilot.press("z")
                await settle(pilot, 60)
                restored = bridge.get_parameter(name, 0, keygroup=keygroup)

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
        region, name, keygroup = "keygroup", "LONOTE", min(2, groups - 1)
        param = p.lookup((region, name))
        before = bridge.get_parameter(name, 0, keygroup=keygroup)
        app._param_context = (region, 0, keygroup)
        for step, value in enumerate((41, 42, 43), 1):
            app._write_param(param, bridge.get_parameter(
                name, 0, keygroup=keygroup), str(value))
            await settle(pilot, 60)
        mid = bridge.get_parameter(name, 0, keygroup=keygroup)

        await pilot.press("Z")
        await settle(pilot, 120)
        after = bridge.get_parameter(name, 0, keygroup=keygroup)
        ok = mid == 43 and after == before
        print(f"    {name} kg{keygroup}: {before} -> 41,42,43 -> "
              f"read {mid} -> Z -> {after}  {'ok' if ok else 'FAIL'}",
              flush=True)
        if not ok:
            failures.append(
                f"Z: started {before}, reached {mid}, ended {after}")
        if app._undo:
            failures.append(f"Z left {len(app._undo)} entries in the log")

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
