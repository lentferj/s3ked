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
"""The handoff index checker, tested against the corruption it was written for."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_handoff",
    Path(__file__).resolve().parent.parent / "tools" / "check_handoff.py")
check_handoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_handoff)

CLEAN = """# Handoff

| pass | find with | what it says |
|---|---|---|
| ninth | `## NINTH PASS` | something |
| tenth | `## TENTH PASS` | something else |

---

## TENTH PASS — something else

body

---

## NINTH PASS — something

body
"""


def test_a_clean_file_passes(tmp_path):
    f = tmp_path / "h.md"
    f.write_text(CLEAN)
    assert check_handoff.check(f) == []


def test_a_heading_swallowed_into_a_table_row_is_caught(tmp_path):
    """The real corruption: a prepend landed inside the index table.

    The file still parsed as Markdown and still contained every finding, so
    nothing failed and nothing errored -- the damage was visible only to
    someone reading the index.
    """
    broken = CLEAN.replace(
        "| tenth | `## TENTH PASS` | something else |",
        "| tenth | `## THIRTY-EIGHTH PASS, 2026-08-12 — a whole disk.\n\nbody\n")
    f = tmp_path / "h.md"
    f.write_text(broken)

    problems = check_handoff.check(f)
    assert any("swallowed a full heading" in p for p in problems), problems


def test_a_row_that_became_a_heading_is_caught(tmp_path):
    """The other half of the same swap."""
    broken = CLEAN.replace(
        "## TENTH PASS — something else",
        "## TENTH PASS` | something else |")
    f = tmp_path / "h.md"
    f.write_text(broken)

    problems = check_handoff.check(f)
    assert any("contains a table pipe" in p for p in problems), problems


def test_an_indexed_pass_with_no_section_is_caught(tmp_path):
    f = tmp_path / "h.md"
    f.write_text(CLEAN.replace("## TENTH PASS — something else", "## GONE"))
    assert any("but no section has it" in p for p in check_handoff.check(f))


def test_a_section_missing_from_the_index_is_caught(tmp_path):
    """A pass nobody can find is the failure the index exists to prevent."""
    f = tmp_path / "h.md"
    f.write_text(CLEAN.replace("| tenth | `## TENTH PASS` | something else |\n", ""))
    assert any("not in the index" in p for p in check_handoff.check(f))


def test_the_live_handoff_is_intact():
    """Runs against the real file when it exists; skipped in a clean clone.

    The handoff is gitignored, so this is a check on this machine's copy
    rather than on repository content.
    """
    import pytest

    if not check_handoff.DEFAULT.exists():
        pytest.skip("no handoff on this machine")
    assert check_handoff.check(check_handoff.DEFAULT) == []
