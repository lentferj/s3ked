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

"""Regenerate the README screenshots from ``--demo``, headlessly.

    .venv/bin/python tools/screenshots.py

Two jobs in one, and the second is the reason this lives in the repo rather
than in someone's scratch directory.

**The pictures.** A terminal editor with no screenshots asks a stranger to
install it before they can see what it is.

**Driving the app.** Until this existed, nothing had ever exercised s3ked's
key handling end to end -- the suite tests widgets and handlers, and a human
had never run the TUI at all. Writing it immediately caught three wrong
assumptions, and the app was right in every one:

  - starting with ``allow_write=True`` and pressing ``w`` turns the gate OFF,
    so the first "write armed" screenshot read "write locked";
  - focus starts on the programs table, so ``down`` moves the program
    selection while the parameters cursor stays on row 0;
  - row 0 is ``PRIDENT``, a block address, and the editor refuses to edit it.

Each of those is correct behaviour that a screenshot script assumed away. The
value was not in the fixes; it was that something finally pressed the keys.

Every image is verified after writing: the SVG carries its own text, so the
script greps for what each screen must contain and fails loudly rather than
shipping a picture captioned with something it does not show.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from textual.widgets import DataTable                       # noqa: E402

from s3ked.app import S3kedApp                              # noqa: E402
from s3ked.demo import DemoBridge                           # noqa: E402

OUT = ROOT / "docs" / "screenshots"
SIZE = (100, 30)

#: name -> (keys, allow_write, parameter to select, text the image must contain)
SHOTS = {
    "catalog": ((), False, None, "Programs"),
    "parameters": (("down", "down", "tab"), False, None, "Parameters"),
    "write-gate": (("w",), False, None, "write ARMED"),
    "edit": (("w", "e"), False, "PRIORT", "range:"),
    "master": (("m",), True, None, "Destructive operations"),
    "disk": (("d",), False, None, "volume"),
}


def _text(svg: Path) -> str:
    raw = svg.read_text()
    return raw.replace("&#160;", " ").replace("&quot;", '"')


async def shoot(name: str, keys, allow_write: bool, param) -> None:
    app = S3kedApp(DemoBridge(), allow_write=allow_write)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        if param is not None:
            # Selecting by NAME rather than by arrow keys: the arrows move
            # whichever table has focus, and which one that is depends on the
            # layout. A row index would silently point somewhere else the next
            # time the table gains a column.
            table = app.query_one("#parameters", DataTable)
            names = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
            table.move_cursor(row=names.index(param))
            table.focus()
            await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await pilot.pause()
        app.save_screenshot(str(OUT / f"{name}.svg"))


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bad = []
    for name, (keys, allow_write, param, must_contain) in SHOTS.items():
        await shoot(name, keys, allow_write, param)
        if must_contain not in _text(OUT / f"{name}.svg"):
            bad.append(f"{name}.svg does not contain {must_contain!r}")
        print(f"  {name}.svg", "ok" if not bad or bad[-1].split()[0] != f"{name}.svg"
              else "MISMATCH")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"\n{len(SHOTS)} screenshots in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
