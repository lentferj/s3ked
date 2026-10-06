# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.

"""The suite must not write the application's config into the checkout.

`s3k.config.DEFAULT_CONFIG_PATH` is the relative string `"config.toml"`.
That is the right default for the application -- it is a disposable
per-checkout cache of which port answered last and which boards are fitted,
gitignored -- and the wrong thing for a test, which would drop the file into
whatever directory pytest was started from.

Being gitignored is what makes this worth a test rather than a habit: the
file never appears in `git status`, so the only evidence is somebody
noticing it sitting next to the source.

Both checks below were this file's own AST walk, copied into each project in
the family. They are now one call each into `vinsynlib.devchecks`.

The vacuity guard matters more here than anywhere else in this project.
`devchecks.config_saves_without_path` looks for calls of the form
`config.save_*`, where `config` is a bare name -- so a suite that imports
the settings module as `bridge_mod` and calls `bridge_mod.save_*` is
INVISIBLE to it. That is not hypothetical: rxved's suite was passing this
check vacuously for exactly that reason until the guard caught it. The
guard is what turns "none found" from silence into a failure.
"""

import ast
import os

from s3k import config as config_mod
from vinsynlib import devchecks

#: The two packages this project owns. `vinsynlib` appears in the
#: sibling-import check below, where the library itself is allowed.
OWN = ("s3k", "s3ked")


def test_every_config_save_in_the_suite_names_its_file() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    offenders = devchecks.config_saves_without_path(here)
    assert not offenders, (
        f"{offenders} save to config.DEFAULT_CONFIG_PATH, which is relative "
        f"to the working directory. Pass a tmp_path."
    )


def test_the_check_has_something_to_check() -> None:
    """Guard against the search passing because it matched nothing.

    Counts every `config.save_*` in the suite, offending or not:
    `devchecks.config_saves_without_path` returns only the offenders, so
    "none" from it means nothing at all on its own.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    saves = 0
    for _name, tree in devchecks.iter_test_sources(here):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("save_")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
            ):
                saves += 1
    assert saves, "no config.save_* calls found; the check is vacuous"


def test_this_project_imports_no_sibling() -> None:
    """A sibling project's package is not installed beside this one.

    This module carries the history: s3ked's settings store was ported from
    eosed's, then from k2kremote's, and each step is a chance to import the
    source project's package by name rather than this one's. It has happened
    in this family -- emorphed had a function importing `nano.config`, which
    is nanosyned's package and is not installed beside it, and it raised
    ImportError the moment `--config` was used. The code read correctly in the
    project it was copied from, which is why review did not catch it.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    offenders = []
    for pkg in OWN:
        directory = os.path.join(root, pkg)
        for name in os.listdir(directory):
            if name.endswith(".py"):
                offenders += devchecks.foreign_imports(
                    os.path.join(directory, name), own=(*OWN, "vinsynlib")
                )
    assert not offenders, offenders


def test_the_settings_store_is_the_shared_one() -> None:
    """s3k.config is a binding, not a copy.

    Stated as a test rather than left to review: the failure this guards
    against is someone re-adding a private TOML reader beside the shared
    one, which is invisible -- the code works, the tests pass, and the nine
    copies start drifting apart again from a project that looks consistent.
    """
    from vinsynlib.config import Settings

    assert isinstance(config_mod.settings, Settings)
    assert config_mod.settings.app_name == "s3ked"
    # The board keys are this project's own and are declared in one table, so
    # the reader and the writer cannot disagree about a board's name.
    assert set(config_mod._BOARDS) == {"IB304F", "EB16"}