#!/usr/bin/env python3
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
"""Check the handoff file's index still matches its sections.

The handoff to mpc2emu is written by prepending each pass at a text anchor.
On 2026-08-13 one of those prepends landed **inside the index table** instead
of at a section boundary: a pass heading became the second cell of a table
row, and the index rows it displaced were pushed down into the middle of the
document, where the first of them became a malformed heading.

Nothing detected it. The file still parsed as Markdown, every byte of every
finding was still present, and the edit reported success. The damage was only
visible to someone reading the index specifically -- and mpc2emu found it,
not me, because their watcher noticed the file had changed while its newest
heading had not.

That is the same shape as most of what this project has found the hard way:
**the operation succeeds and the artefact is wrong.** A write that reports
success is not evidence the thing written is correct, and the check has to
look at the artefact.

So this looks at the artefact. Run it after any edit to the handoff:

    python3 tools/check_handoff.py [path]

Exit status is 0 when the index and the sections agree.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / ".claude" / "handoff-mpc2emu.md"

#: An index row: | ninth | `## NINTH PASS` | what it says |
ROW = re.compile(r"^\| *([a-z-]+) *\| *`(## [A-Z-]+ PASS)` *\|(.*)\|\s*$", re.M)
#: A section heading, which must start a line and carry no table pipe.
HEADING = re.compile(r"^(## [A-Z-]+ PASS)\b(.*)$", re.M)


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    rows = ROW.findall(text)
    anchors = [anchor for _name, anchor, _says in rows]
    headings = [(h, rest) for h, rest in HEADING.findall(text)]
    heading_names = [h for h, _rest in headings]

    # A heading that still carries a table pipe is a swallowed row, and a row
    # whose anchor cell contains a comma or a dash-clause is a swallowed
    # heading. Both are what the 2026-08-13 corruption looked like.
    for h, rest in headings:
        if "|" in rest:
            problems.append(f"heading {h!r} contains a table pipe: {rest[:60]!r}")
    for line in text.splitlines():
        if line.startswith("|") and re.search(r"`## [A-Z-]+ PASS,", line):
            problems.append(f"table row swallowed a full heading: {line[:70]!r}")

    for anchor in anchors:
        if anchor not in heading_names:
            problems.append(f"index lists {anchor!r} but no section has it")
    for name in heading_names:
        if name not in anchors:
            problems.append(f"section {name!r} is not in the index")

    for what, seq in (("index row", anchors), ("heading", heading_names)):
        for name, n in Counter(seq).items():
            if n > 1:
                problems.append(f"{what} {name!r} appears {n} times")

    if not rows:
        problems.append("no index rows found at all -- the table is gone")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not path.exists():
        print(f"no handoff at {path}", file=sys.stderr)
        return 2
    problems = check(path)
    rows = len(ROW.findall(path.read_text(encoding="utf-8")))
    if problems:
        print(f"{path.name}: {len(problems)} problem(s)", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"{path.name}: index and sections agree ({rows} passes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
