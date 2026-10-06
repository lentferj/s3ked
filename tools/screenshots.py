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

from textual.widgets import DataTable  # noqa: E402

from s3ked.app import S3kedApp  # noqa: E402
from s3ked.demo import DemoBridge  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
SIZE = (100, 30)

#: name -> (keys, allow_write, parameter to select, text the image must
#: contain, seconds to wait for it, where the shot is exercised)
#: name -> (keys to press, write gate, parameter to select, text that MUST
#: appear in the result, ...). The text field is the point: an SVG carries its
#: own text, so a screenshot captioned with something it does not show fails
#: the run instead of shipping.
#:
#: `settle` is a TIMEOUT in seconds, not a frame count: a pause is as long as
#: the event loop's mood, so forty of them wait differently on a loaded box
#: than on an idle one. The disk browser reads volumes and a directory in a
#: worker, and a screenshot taken before that lands shows an empty pane --
#: which is exactly the picture the README must not carry. Polling for the
#: text with a deadline waits exactly as long as needed and no longer.
#:
#: `covered` says where the shot is exercised, because a picture nothing
#: checks rots: README means the image is embedded there, test names the
#: test. A shot with neither is marked UNCOVERED on purpose rather than left
#: to imply coverage it does not have.
SHOTS = {
    # README + test_each_documented_screen_actually_renders[catalog]
    "catalog": ((), False, None, "Programs", 2.0, "README, test_app"),
    # UNCOVERED: generated for the docs folder but embedded nowhere and
    # asserted by no test. Kept because it is the only picture of the
    # keygroup-follow view, and deleting it would silently drop a file the
    # folder otherwise carries.
    "parameters": (("down", "down", "tab"), False, None, "Parameters", 2.0, "UNCOVERED"),
    # README + test_each_documented_screen_actually_renders[write-gate]
    "write-gate": (("w",), False, None, "write ARMED", 2.0, "README, test_app"),
    # README + test_each_documented_screen_actually_renders[edit]
    "edit": (("w", "e"), False, "PRIORT", "range:", 2.0, "README, test_app"),
    # README + test_each_documented_screen_actually_renders[master]
    "master": (("m",), True, None, "Destructive operations", 2.0, "README, test_app"),
    # The disk browser now owns the right-hand column rather than a quarter of
    # the left one, so this is `d` plus time for the worker to answer.
    # NOT the "in the selected volume" divider: it sits below all 34 demo
    # volumes and is off-screen. Asserting text that is present in the DOM but
    # scrolled out of view is a check that passes for the wrong reason.
    # README (disk.svg).
    "disk": (("d",), False, None, "HARD-:A", 15.0, "README"),
    # The load screen: eight types, add-vs-clear, renumber. `l` from inside
    # the browser is the one that offers it. README (load.svg).
    "load": (("d", "l"), True, None, "clear first", 15.0, "README"),
    # the check that finds programs whose samples never arrived; the demo
    # carries one dangling reference on purpose so this has something to show.
    # README (integrity.svg).
    "integrity": (("i",), False, None, "silent zone", 2.0, "README"),
    # every resident sample, not just the selected program's.
    # README (all-samples.svg).
    "all-samples": (("a",), False, None, "All samples", 5.0, "README"),
}


def _text(svg: Path) -> str:
    raw = svg.read_text(encoding="utf-8")
    return raw.replace("&#160;", " ").replace("&quot;", '"')


async def shoot(
    name: str, keys, allow_write: bool, param, must_contain: str, settle: float = 2.0
) -> None:
    import time

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
        out = OUT / f"{name}.svg"
        deadline = time.monotonic() + settle
        while True:
            await pilot.pause()
            app.save_screenshot(str(out))
            if must_contain in _text(out) or time.monotonic() >= deadline:
                return


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bad = []
    for name, (keys, allow_write, param, must_contain, settle, _covered) in SHOTS.items():
        await shoot(name, keys, allow_write, param, must_contain, settle)
        if must_contain not in _text(OUT / f"{name}.svg"):
            bad.append(f"{name}.svg does not contain {must_contain!r}")
            print(f"  {name}.svg MISMATCH")
        else:
            print(f"  {name}.svg ok")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"\n{len(SHOTS)} screenshots in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
