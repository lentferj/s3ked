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

"""Cross-check every offset in `params.py` against BOTH primary documents.

**No hardware.** §8 did this by hand for twelve fields of the multi part and
found them all agreeing. This does it mechanically for the whole table, and
against two independently written sources rather than one.

The two documents state offsets in completely different ways, which is what
makes the check worth having:

* the **S1000** source is a run of assembler declarations — `DB` one byte,
  `DW` two, `DW ?,?` four — so every offset is **implicit**, accumulated by
  walking the block. A single missing or extra declaration shifts everything
  after it, which is the error a human reader is least likely to catch.
* the **S2800/S3000XL** source states `Offset: N bytes` **explicitly** per
  parameter, so its errors are local rather than cascading.

Two sources with opposite failure modes agreeing on an offset is much
stronger evidence than either alone. Where they disagree, that is a finding
in itself — §120 is the first known case.

THE CONTROL: the parser must reproduce offsets already known from hardware.
`SNAME1` at `0x22`, `GROUPS` at `0x2a`, `SPTYPE` at `0x13` and `SLNGTH` at
`0x1a` were all confirmed against the machine. If the walk does not land on
those, it is mis-parsing and nothing else it says can be trusted.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import params as p

DOCS = Path("/home/lentferj/temp/akai_s3000xl_docs")

#: Where each block's declarations begin in the S1000 source, and which
#: `params.py` region they correspond to.
S1000_BLOCKS = (
    ("Program Common Header Block", "program"),
    ("Keygroup Block", "keygroup"),
    ("Sample Header Block", "sample"),
)

#: Offsets confirmed against the machine itself. The parser must reproduce
#: these or it is not parsing.
CONTROL = {("program", "PRNAME"): 0x03, ("program", "GROUPS"): 0x2a,
           ("keygroup", "SNAME1"): 0x22, ("keygroup", "FILFRQ"): 0x07,
           ("sample", "SPTYPE"): 0x13, ("sample", "SLNGTH"): 0x1a}

WIDTH = {"DB": 1, "DW": 2}

#: The documents' spelling against `params.py`'s.
ALIAS = {("keygroup", "SNAME"): "SNAME1", ("keygroup", "LOVEL"): "LOVEL1",
         ("keygroup", "HIVEL"): "HIVEL1", ("keygroup", "VTUNO"): "VTUNO1",
         ("keygroup", "VLOUD"): "VLOUD1", ("keygroup", "VFREQ"): "VFREQ1",
         ("keygroup", "VPANO"): "VPANO1", ("keygroup", "ZPLAY"): "ZPLAY1",
         ("sample", "LOOPAT"): "LOOPAT1", ("sample", "LLNGTH"): "LLNGTH1",
         ("sample", "LDWELL"): "LDWELL1"}


def size_of(kind: str, operand: str) -> int:
    """Bytes a declaration occupies. Unknown forms return 0 and are reported."""
    operand = operand.strip()
    unit = WIDTH[kind]
    m = re.match(r"^(\d+)\s+DUP\(", operand)
    if m:
        return int(m.group(1)) * unit
    m = re.match(r"^([A-Z]+)\s*\*\s*(\d+)\s+DUP\(", operand)
    if m:                                     # e.g. LBYTES*7 DUP(0)
        return None, m.group(1), int(m.group(2))
    if re.match(r"^[A-Z]+\s+DUP\(", operand):  # e.g. DUBYTES DUP(?)
        return None, operand.split()[0], 1
    return operand.count(",") * unit + unit


def parse_s1000(path: Path):
    """name -> offset, per block, by walking the declarations."""
    lines = path.read_text(errors="replace").splitlines()
    starts = []
    for i, line in enumerate(lines):
        for marker, region in S1000_BLOCKS:
            if line.strip().startswith(marker):
                starts.append((i, region))
    out, unknown = {}, []
    for n, (start, region) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        offset, table, symbols = 0, {}, {}
        for line in lines[start:end]:
            body = line.split(";", 1)[0].rstrip()
            if not body.strip():
                continue
            # Names may contain '@' -- KGRP1@, NXTKG@ -- which \w excludes.
            # Missing those dropped a two-byte DW from the program and
            # keygroup blocks and shifted every later offset by 2. The
            # sample block has no such name, which is why it alone passed
            # the control. Caught by the control, not by reading the code.
            m = re.match(r"^([\w@]+)?\s*\b(DB|DW)\b\s*(.*)$", body)
            if not m:
                m2 = re.match(r"^(\w+)\s+EQU\s+\$-(\w+)", body)
                if m2 and m2.group(2) in table:
                    symbols[m2.group(1)] = offset - table[m2.group(2)]
                continue
            name, kind, operand = m.group(1), m.group(2), m.group(3)
            got = size_of(kind, operand)
            if isinstance(got, tuple):
                _, sym, mult = got
                if sym not in symbols:
                    unknown.append((region, body.strip()))
                    continue
                got = symbols[sym] * mult
            if name:
                # The S1000 source names zone 1's fields without the "1"
                # that params.py uses, then covers zones 2-4 with a single
                # ZBYTES*3 declaration.
                # BOTH spellings. `ZBYTES EQU $-SNAME` and `LBYTES EQU
                # $-LOOPAT` resolve against the DOCUMENT's name, so aliasing
                # it away made those symbols unresolvable -- which silently
                # dropped 72 bytes of zones 2-4 and 84 of loops 2-8, and
                # showed up as 19 fields disagreeing by a constant delta.
                # A constant delta across unrelated fields is a block-size
                # bug, not a transcription error.
                table.setdefault(name, offset)
                alias = ALIAS.get((region, name))
                if alias:
                    table.setdefault(alias, offset)
            offset += got
        out[region] = table
    return out, unknown


def parse_s2800(path: Path):
    """name -> offset, from explicit `Offset: N bytes` statements."""
    text = path.read_text(errors="replace")
    out = {}
    for m in re.finditer(r"Parameter:\s*(\w+)\s*\n+\s*Offset:\s*(\d+)\s*bytes",
                         text):
        out.setdefault(m.group(1), int(m.group(2)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", default=str(DOCS))
    args = ap.parse_args(argv)
    docs = Path(args.docs)

    s1000, unknown = parse_s1000(docs / "s1000_sysex.txt")
    s2800 = parse_s2800(docs / "s2800_sysex.txt")
    print(f"  S1000: " + ", ".join(f"{r} {len(t)} fields"
                                   for r, t in s1000.items()))
    print(f"  S2800: {len(s2800)} fields with explicit offsets")
    if unknown:
        print(f"  declarations not understood: {len(unknown)}")
        for region, body in unknown[:4]:
            print(f"    {region}: {body}")

    print("\n  === CONTROL: offsets confirmed on hardware ===")
    ok = True
    for (region, name), want in sorted(CONTROL.items()):
        got = s1000.get(region, {}).get(name)
        good = got == want
        ok = ok and good
        print(f"    {region:<9} {name:<8} hardware {want:#05x}  "
              f"S1000 walk {got if got is None else f'{got:#05x}'}  "
              f"{'ok' if good else 'MISMATCH'}")
    if not ok:
        print("\n  control failed -- the parser is wrong, not the table.")
        return 1

    print("\n  === params.py against the S1000 walk ===")
    disagree, missing = [], []
    for region, table in s1000.items():
        for name, off in sorted(table.items(), key=lambda kv: kv[1]):
            try:
                f = p.lookup((region, name))
            except Exception:
                missing.append((region, name, off))
                continue
            if f.offset != off:
                disagree.append((region, name, f.offset, off))
    print(f"    agree: {sum(len(t) for t in s1000.values()) - len(disagree) - len(missing)}")
    print(f"    DISAGREE ({len(disagree)}):")
    for region, name, ours, theirs in disagree:
        print(f"      {region:<9} {name:<10} params.py {ours:#05x}  "
              f"S1000 {theirs:#05x}   delta {theirs-ours:+d}")
    print(f"    in the document, absent from params.py ({len(missing)}):")
    for region, name, off in missing[:12]:
        print(f"      {region:<9} {name:<10} {off:#05x}")

    print("\n  === params.py against the S2800 explicit offsets ===")
    d2, n2 = [], 0
    for region in ("program", "keygroup", "sample", "multi", "multipart"):
        for f in p.region_params(region):
            if f.name in s2800:
                n2 += 1
                if s2800[f.name] != f.offset:
                    d2.append((region, f.name, f.offset, s2800[f.name]))
    print(f"    compared {n2}, DISAGREE ({len(d2)}):")
    for region, name, ours, theirs in d2[:20]:
        print(f"      {region:<9} {name:<10} params.py {ours:#05x}  "
              f"S2800 {theirs:#05x}   delta {theirs-ours:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
