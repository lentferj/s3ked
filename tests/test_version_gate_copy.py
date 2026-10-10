# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors

"""The version gate's copy of ``_release_parts`` still matches the library's.

``s3ked/entry.py`` carries its own copy of that helper on purpose: a version
gate must not import the library it is checking, because a library too old to
contain the gate would raise -- which is the failure the gate exists to
prevent. The duplication is deliberate and must not be "fixed" by sharing it.

What it needs is a check that the copies stay identical, and that is what
this module is. Without it a drift lands in *one* tool, so the symptom is
"s3ked is broken with this vinsynlib and the other nine work" -- a support
ticket about one program rather than a red build anyone can see.
"""

import os

from vinsynlib import devchecks

HERE = os.path.dirname(os.path.abspath(__file__))


def test_the_version_gate_copy_has_not_drifted() -> None:
    entry = os.path.join(HERE, os.pardir, "s3ked", "entry.py")
    assert os.path.exists(entry), f"{entry} is not where this test looks"
    assert not devchecks.release_parts_drift(entry), (
        f"{entry}'s copy of _release_parts no longer matches the one in the "
        f"installed vinsynlib. A project's copy must stay identical to the "
        f"library's; update this one, or the library's first and then this "
        f"one."
    )
