# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the name-field probe.

Decoding names was proven on the first hardware session; encoding was not,
and this probe is what settles it. What has to hold here is that it really
does exercise every character, and that it never writes a name into a field
that is a *reference* rather than a label.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes"))

import names as nm                                         # noqa: E402

from s3k import messages as m                              # noqa: E402


def test_coverage_uses_every_character_exactly_once():
    chunks = nm.coverage_names()
    assert "".join(chunks) == m.AKAI_CHARSET
    assert len(set("".join(chunks))) == len(m.AKAI_CHARSET)


def test_no_chunk_exceeds_the_name_field():
    for chunk in nm.coverage_names():
        assert len(chunk) <= m.NAME_LENGTH


def test_every_chunk_encodes():
    """If a chunk cannot be encoded, the probe could never write it."""
    for chunk in nm.coverage_names():
        assert len(m.encode_name(chunk)) == m.NAME_LENGTH


def test_a_chunk_round_trips_through_the_codec():
    for chunk in nm.coverage_names():
        assert m.decode_name(m.encode_name(chunk)) == chunk.rstrip()


def test_dry_run_completes():
    assert nm.main(["--dry-run", "--allow-write", "--program", "0"]) == 0
