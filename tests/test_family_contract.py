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

"""s3ked keeps the contract the other projects in the family keep.

Every check below is the same one, run against this program rather than
written out again here. The reasons are in docs/UX-SPEC.md section 5 and in
the library: what drifted was never a bug anyone chose, it was a copy that
nobody had a second copy to compare against.

**s3ked is an EDITOR, not a browser, and the checks say so.** It has no
favourites store -- there is nothing in an editor to favourite -- so it is
not asked for `f`, `F`, `t` or `n`, and offering them would be a key that
does nothing. It sends no program change, so it has no channel, and it has
no "select on the unit" concept at all. Those three are `False` here and in
s3ked's sibling eosed, and the point of passing them explicitly is that a
future key added to a browser tier should not silently be demanded of an
editor.
"""

from textual.binding import Binding

from s3k.terms import TERMS
from s3ked.app import PRESS_NAMES, S3kedApp, legend_blocks
from s3ked.app import build_parser as build_app_parser
from s3ked.cli import build_parser as build_cli_parser
from vinsynlib import conformance, keys, spec


def test_the_editor_flags_match_the_family() -> None:
    # --channel is deliberately not here: an S1000 is not selected by program
    # change at all, so there is no channel to send one on, and a flag
    # accepted and then ignored is worse than no flag. `forbidden` is how
    # that is stated rather than merely left out.
    problems = conformance.check_flags(
        build_app_parser(),
        required=("port", "exclusive-channel", "demo", "timeout", "config", "allow-write"),
        forbidden=("channel", "favorites", "scan"),
    )
    assert not problems, "\n".join(problems)


def test_the_shared_editor_keys_are_bound() -> None:
    assert not conformance.check_bindings(
        S3kedApp, tier="editor", favourites=False, channel=False, select=False
    )


def test_the_legend_is_generated_from_the_bindings() -> None:
    """Not checked against the shared browser legend, and the reason matters.

    `conformance.check_legend` compares a legend against
    `keys.CANONICAL_LEGEND`, which is the *browser* legend: it expects `f
    favourite` and `/ search`, neither of which exists in an editor. There is
    nothing to check it against here, and inventing an editor legend in the
    library to check against would be a second list to keep in step.

    What can be checked -- and is, below -- is that the two places this
    project builds its legend agree. `legend_blocks` is the single builder
    used by both `compose` and `_refresh_key_hints`, and this asserts it
    still says what `vinsynlib.keys.legend_from_bindings` says. If the
    library's builder gains a rule, the two cannot drift apart from it.
    """
    assert legend_blocks(S3kedApp.BINDINGS) == keys.legend_from_bindings(
        S3kedApp.BINDINGS, press_names=PRESS_NAMES
    )


def test_no_favourites_key_is_offered() -> None:
    """The absence is the point, so it is asserted rather than assumed.

    s3ked is an editor and has no favourites database. docs/UX-SPEC.md
    section 1 says the keys and flags for favouriting are "absent rather
    than present and broken" -- which cannot be checked by looking for what
    is there, because nothing is.
    """
    bound = {
        b.key
        for b in S3kedApp.BINDINGS
        # App.BINDINGS admits plain tuples as well as Binding objects; every
        # entry in this class is a Binding, and a tuple has no `.key`.
        if isinstance(b, Binding)
    }
    for key in ("f", "F", "t", "n"):
        assert key not in bound, (
            f"{key} is bound in an editor with no favourites store; the spec "
            f"says the keys are absent rather than present and broken"
        )


def test_the_vocabulary_is_declared() -> None:
    assert not conformance.check_terms(TERMS)


def test_both_front_ends_agree_on_the_shared_flags() -> None:
    """`s3ked` and `s3kcli` are two front doors to one program.

    Scoped to the family's canonical flags: s3ked's own options need not be
    on both. Both parsers are built by `vinsynlib.cli.add_common_arguments`,
    so what this catches is the two call sites being passed different words
    -- which is a real thing to get wrong, and got wrong once already when
    `--allow-write` was on the TUI's parser without help text.
    """
    app_dests = {a.dest for a in build_app_parser()._actions}
    cli_dests = {a.dest for a in build_cli_parser()._actions}
    canonical = {f.name.replace("-", "_") for f in spec.CANONICAL_FLAGS}
    missing = sorted(canonical & app_dests - cli_dests)
    assert not missing, f"s3kcli no longer offers {missing}"
