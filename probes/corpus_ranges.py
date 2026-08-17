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

"""What values do REAL files actually carry, against what `params.py` declares?

**No hardware.** Reads `.P3` and `.S3` files off disk and measures the
distribution of every field the parameter table declares a range for.

Two classes of finding, both real:

* a declared range the corpus **exceeds** — the table is too narrow, and a
  count with an *n* on it is better evidence than one machine reading.
  `K_FREQ`'s documented 0..12 against a machine acting on 22 and beyond
  (§108) is exactly this shape.
* a declared range **nothing in the corpus approaches** — which is where an
  invented or misplaced parameter hides.

**PROVENANCE IS SEPARATED AND IT IS THE POINT.** Files this project or its
siblings generated only say what our own writers emit; measuring the table
against them and calling it validation is the round-trip mistake — a check
that cannot fail because both halves share a convention. Third-party
material is the only thing that tests the transcription. The two are
counted apart and never pooled.

Blocks are 192 bytes (§113): a `.P3` is a common block followed by one per
keygroup; an `.S3` opens with one sample header.

No file, program or sample NAME is printed. Names in this corpus may come
from commercial libraries and the shape of the data is the whole finding.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import params as p

BLOCK = 192

#: Path fragments marking a file as OUR OWN output rather than third-party.
#: Conservative on purpose: anything unrecognised counts as "unknown
#: provenance" and is reported separately rather than silently trusted.
# CORRECTED 2026-08-17: this list said "vinsamlib_akai_smoke" and so
# let vinsamlib_akai_write_* and its temp directories through as
# third-party. They are a LOCAL tool's output. 74 of the 74 blocks
# that carried anything interesting were that tool, and the pattern
# read as a format fact was one writer's habit -- §120.
OURS = ("s3ked_pooltest", "cd0build", "akai_resave_selftest", "akai_out_krz",
        "akai_out_e4b", "vinsamlib", "akai_cal", "s3ked-logs",
        "akai_e2e", "smokevol")


#: Files built to contain ARBITRARY bytes on purpose. mpc2emu's resave
#: probes stamp every undocumented byte with its own offset (or offset^0x55),
#: so they violate almost every declared range by construction and would
#: swamp the result. Excluding them is not tidying the data -- including them
#: would be measuring the probe rather than the corpus.
#:
#: They were found rather than assumed: every field reported exactly 20
#: generated and 16 unknown violations, and an identical count across
#: unrelated parameters is a handful of odd blocks, not a range finding.
STAMPED = ("rsprobe", "rsprob2", "resave_selftest", "ours.p3", "ours2.p3",
           "ours_resaved", "ours2_resaved", "theirs.p3")


def is_stamped(path: Path) -> bool:
    return any(m in str(path).lower() for m in STAMPED)


def provenance(path: Path) -> str:
    low = str(path).lower()
    return "generated" if any(m in low for m in OURS) else "unknown"


def looks_like_header(block: bytes) -> bool:
    """Is this actually one of the machine's structures?

    **Load-bearing, and it was added after it changed an answer.** Without
    it, `SPITCH` appeared to run below its declared minimum in 357 of 537
    sample files — a large, confident-looking finding. Of those 537 only
    182 parse as sample headers at all; among the sane ones the count is
    **2**. The rest are files whose first 192 bytes are not a header, and
    they were quietly voting on every field.

    The name at 0x03 must decode to printable characters through the
    device's charset, and byte 0 is the block identifier.
    """
    if len(block) < BLOCK or block[0] not in (1, 2, 3):
        return False
    try:
        from s3k import messages as m
        name = m.decode_name(block[3:15])
    except Exception:
        return False
    return all(32 <= ord(c) < 127 for c in name)


def blocks_of(path: Path, region: str):
    """Yield the 192-byte blocks a file of this kind contains."""
    data = path.read_bytes()
    if region == "program":
        if len(data) < BLOCK or len(data) % BLOCK:
            return
        if not looks_like_header(data[:BLOCK]):
            return
        yield "program", data[:BLOCK]
        for i in range(1, len(data) // BLOCK):
            block = data[i * BLOCK:(i + 1) * BLOCK]
            if looks_like_header(block):
                yield "keygroup", block
    else:
        if len(data) < BLOCK or not looks_like_header(data[:BLOCK]):
            return
        yield "sample", data[:BLOCK]


def collect(paths, region_of):
    seen = defaultdict(lambda: defaultdict(int))   # (region,name) -> value -> n
    files = defaultdict(int)
    for path in paths:
        if is_stamped(path):
            continue
        prov = provenance(path)
        try:
            got = list(blocks_of(path, region_of(path)))
        except OSError:
            continue
        if not got:
            continue
        files[prov] += 1
        for region, block in got:
            for field in p.region_params(region):
                if field.kind != "num" or field.size > 2:
                    continue
                off, size = field.offset, field.size
                if off + size > len(block):
                    continue
                raw = int.from_bytes(block[off:off + size], "little")
                # SIGNED fields of EITHER width. Handling only size 1 made
                # KGTUNO and the VTUNO pair look wildly out of range -- a
                # two-byte -12 reads as 65524 -- and reported 184 of 293
                # blocks as violations. Three false positives out of four
                # findings, all from the decoder rather than the data.
                if field.minimum < 0 and raw >= 1 << (8 * size - 1):
                    raw -= 1 << (8 * size)
                seen[(region, field.name, prov)][raw] += 1
    return seen, files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="*", default=["/home/lentferj/temp",
                                                 "/home/lentferj/git-repos"])
    ap.add_argument("--limit", type=int, default=4000)
    args = ap.parse_args(argv)

    progs, samps = [], []
    for root in args.roots:
        base = Path(root)
        if not base.exists():
            continue
        for pat, bucket in ((("*.P3", "*.p3"), progs), (("*.S3", "*.s3"), samps)):
            for glob in pat:
                bucket.extend(list(base.rglob(glob))[:args.limit])
    print(f"  {len(progs)} program files, {len(samps)} sample files\n", flush=True)

    seen, files = collect(progs, lambda _: "program")
    s2, f2 = collect(samps, lambda _: "sample")
    for k, v in s2.items():
        for val, n in v.items():
            seen[k][val] += n
    for k, v in f2.items():
        files[k] += v
    print(f"  by provenance: " + ", ".join(f"{k} {v}" for k, v in files.items()))
    print("  (generated = written by this project or a sibling; measuring the\n"
          "   table against those alone proves only that our writers agree\n"
          "   with our table)\n", flush=True)

    over, unused = [], []
    for (region, name, prov), counts in sorted(seen.items()):
        field = p.lookup((region, name))
        lo, hi = field.minimum, field.maximum
        if lo is None or hi is None or hi <= lo:
            continue
        vals = sorted(counts)
        n = sum(counts.values())
        below = sum(c for v, c in counts.items() if v < lo)
        above = sum(c for v, c in counts.items() if v > hi)
        if below or above:
            over.append((region, name, prov, lo, hi, min(vals), max(vals),
                         below + above, n))
        elif max(vals) * 4 < hi and hi > 8:
            unused.append((region, name, prov, lo, hi, max(vals), n))

    print(f"  === DECLARED RANGE EXCEEDED ({len(over)}) ===", flush=True)
    print(f"  {'region':<9} {'field':<10} {'prov':<10} declared      seen"
          f"          out/n", flush=True)
    for region, name, prov, lo, hi, mn, mx, bad, n in over:
        print(f"  {region:<9} {name:<10} {prov:<10} {lo:>5}..{hi:<6} "
              f"{mn:>6}..{mx:<7} {bad:>5}/{n}", flush=True)

    print(f"\n  === DECLARED RANGE BARELY USED ({len(unused)}) ===", flush=True)
    print("  (a top nothing approaches is where an invented parameter hides)",
          flush=True)
    for region, name, prov, lo, hi, mx, n in unused[:25]:
        print(f"  {region:<9} {name:<10} {prov:<10} {lo:>5}..{hi:<6} "
              f"max seen {mx:<6} n={n}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
