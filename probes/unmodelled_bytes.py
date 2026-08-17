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

"""Which bytes does the parameter table not name, and do any of them VARY?

**No hardware.** §113 measured the machine's blocks at 192 bytes and found
`params.py` naming 115 of the program block's and 141 of the sample block's.
The rest are not absent from the machine — they are outside the
transcription. This asks which of them carry information.

* an unmodelled byte that is **constant** across a corpus is padding, a
  structural marker, or a field nobody ever changes — uninteresting
* an unmodelled byte that **varies** is an unnamed parameter, and it is
  where the next hardware session should point

It also runs the check backwards, which is the part that can embarrass the
table: a **modelled** byte that never varies anywhere is a candidate for
being mis-transcribed, misplaced, or invented.

THE NEGATIVE CONTROL, and it is not optional. Fields known to vary --
`PRNAME`, `GROUPS`, `LONOTE`, `SNAME1` -- MUST show variance. If they come
back constant the analysis is broken, not the table, and nothing else it
says can be believed. §119 produced three large confident findings that were
all the checker; the control is what would have caught them sooner.

Third-party blocks ONLY. Our own writers zero-fill by design, so including
them would make everything look constant -- the same round-trip mistake
§119 was careful about.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import messages as m
from s3k import params as p

BLOCK = 192

OURS = ("s3ked_pooltest", "cd0build", "akai_resave_selftest", "akai_out_krz",
        "akai_out_e4b", "vinsamlib_akai_smoke", "akai_cal", "s3ked-logs",
        "akai_e2e", "smokevol", "rsprobe", "rsprob2", "ours.p3", "ours2.p3",
        "theirs.p3", "_resaved")

#: Must show variance or the analysis is not measuring anything.
CONTROLS = {"program": ("PRNAME", "GROUPS", "PRGNUM"),
            "keygroup": ("LONOTE", "HINOTE", "SNAME1"),
            "sample": ("SHNAME", "SPITCH", "SLNGTH")}


def theirs(path: Path) -> bool:
    return not any(k in str(path).lower() for k in OURS)


def sane(block: bytes) -> bool:
    if len(block) < BLOCK or block[0] not in (1, 2, 3):
        return False
    try:
        name = m.decode_name(block[3:15])
    except Exception:
        return False
    return all(32 <= ord(c) < 127 for c in name)


def modelled(region: str):
    """offset -> field name, for every byte any declared field covers."""
    owned = {}
    for f in p.region_params(region):
        for off in range(f.offset, f.offset + f.size):
            owned.setdefault(off, f.name)
    return owned


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="*",
                    default=["/home/lentferj/temp", "/home/lentferj/git-repos"])
    args = ap.parse_args(argv)

    blocks = defaultdict(list)
    for root in args.roots:
        base = Path(root)
        if not base.exists():
            continue
        for glob, kinds in (("*.P3", ("program", "keygroup")),
                            ("*.S3", ("sample",))):
            for path in base.rglob(glob):
                if not theirs(path):
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if len(data) < BLOCK:
                    continue
                if kinds[0] == "program":
                    if len(data) % BLOCK or not sane(data[:BLOCK]):
                        continue
                    blocks["program"].append(data[:BLOCK])
                    for i in range(1, len(data) // BLOCK):
                        b = data[i * BLOCK:(i + 1) * BLOCK]
                        if sane(b):
                            blocks["keygroup"].append(b)
                else:
                    if sane(data[:BLOCK]):
                        blocks["sample"].append(data[:BLOCK])

    for region in ("program", "keygroup", "sample"):
        bl = blocks[region]
        print(f"\n  === {region}: {len(bl)} third-party blocks ===", flush=True)
        if len(bl) < 8:
            print("    too few to say anything", flush=True)
            continue
        owned = modelled(region)
        distinct = [len({b[off] for b in bl}) for off in range(BLOCK)]

        # THE CONTROL FIRST. If these are flat, stop reading the rest.
        print("    control -- fields that MUST vary:", flush=True)
        ok = True
        for name in CONTROLS[region]:
            try:
                f = p.lookup((region, name))
            except Exception:
                continue
            v = max(distinct[f.offset:f.offset + f.size])
            if v < 2:
                ok = False
            print(f"      {name:<8} offset {f.offset:#05x}  distinct values "
                  f"{v}  {'ok' if v > 1 else 'FLAT -- analysis is broken'}",
                  flush=True)
        if not ok:
            print("    control failed; not reporting the rest", flush=True)
            continue

        unm_var = [(off, distinct[off]) for off in range(BLOCK)
                   if off not in owned and distinct[off] > 2]
        unm_flat = [off for off in range(BLOCK)
                    if off not in owned and distinct[off] == 1]
        mod_flat = [(off, owned[off]) for off in range(BLOCK)
                    if off in owned and distinct[off] == 1]

        print(f"    modelled {len(owned)}/{BLOCK} bytes", flush=True)
        print(f"    UNMODELLED and VARYING ({len(unm_var)}) "
              f"-- candidates for unnamed parameters:", flush=True)
        for off, n in unm_var:
            vals = sorted({b[off] for b in bl})
            show = vals[:6] + (["..."] if len(vals) > 6 else [])
            print(f"      {off:#05x}  {n:>3} distinct   {show}", flush=True)
        print(f"    unmodelled and constant: {len(unm_flat)} bytes", flush=True)
        print(f"    MODELLED but never varying ({len(mod_flat)}): "
              f"{sorted({n for _, n in mod_flat})}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
