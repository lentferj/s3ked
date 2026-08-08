# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the transcribed parameter tables.

These cannot tell whether an offset is *right* -- only hardware can, and none
has been available. What they can do is catch the mistakes transcription
actually makes: a transposed digit that lands one field on top of another, a
span that runs off the end of a header, a name entered twice.
"""

import pytest

from s3k import messages as m
from s3k import params as p


# --- structural invariants: the real point of this file ---------------------


@pytest.mark.parametrize("region", p.REGIONS)
def test_no_two_parameters_overlap(region):
    """The single most valuable check here.

    Two fields claiming the same byte means at least one offset is wrong, and
    on hardware that means writing one parameter corrupts another.
    """
    occupied = {}
    for param in p.region_params(region):
        for byte in range(param.offset, param.end):
            assert byte not in occupied, (
                f"{region} byte {byte} claimed by both "
                f"{occupied[byte]} and {param.name}"
            )
            occupied[byte] = param.name


@pytest.mark.parametrize("region", p.REGIONS)
def test_every_span_fits_the_header(region):
    for param in p.region_params(region):
        assert param.offset >= 0
        assert param.end <= p.HEADER_SIZE, (
            f"{param.name} runs to {param.end}, past the "
            f"{p.HEADER_SIZE}-byte header"
        )


@pytest.mark.parametrize("region", p.REGIONS)
def test_names_are_unique_within_a_region(region):
    names = [x.name for x in p.region_params(region)]
    assert len(names) == len(set(names))


def test_keys_and_entries_agree():
    # A collision here would silently drop a parameter from every lookup.
    assert len(p.PARAMETERS) == len(p._PARAMS)
    assert len(p.PARAMETERS_BY_NAME) == len(p._PARAMS)


def test_expected_entry_counts():
    # Pinned so a regenerated table cannot quietly lose rows.
    assert len(p.region_params("program")) == 84
    assert len(p.region_params("keygroup")) == 130
    assert len(p.region_params("sample")) == 35


def test_sizes_are_positive():
    for param in p._PARAMS:
        assert param.size > 0


def test_ranges_are_ordered():
    for param in p._PARAMS:
        if param.kind == "num":
            assert param.minimum <= param.maximum, param.name


def test_every_group_is_dotted_under_its_region():
    for param in p._PARAMS:
        assert param.group == param.region or param.group.startswith(
            param.region + "."
        ), f"{param.name} has group {param.group} in region {param.region}"


def test_enumerated_values_lie_within_the_range():
    for param in p._PARAMS:
        if not param.values:
            continue
        for value in param.values:
            assert param.minimum <= value <= param.maximum, (
                f"{param.name} enumerates {value}, outside "
                f"{param.minimum}..{param.maximum}"
            )


# --- lookup -----------------------------------------------------------------


def test_lookup_by_region_and_offset():
    assert p.lookup(("program", 3)).name == "PRNAME"


def test_lookup_by_region_and_name():
    assert p.lookup(("program", "PRNAME")).offset == 3


def test_lookup_by_bare_name_when_unambiguous():
    assert p.lookup("PRIORT").region == "program"


def test_lookup_is_case_insensitive():
    assert p.lookup("priort") is p.lookup("PRIORT")


def test_lookup_refuses_ambiguous_bare_name():
    # RESERVED exists in two regions; guessing would read the wrong header.
    with pytest.raises(KeyError, match="ambiguous"):
        p.lookup("RESERVED")


def test_lookup_ambiguous_name_resolves_with_region():
    assert p.lookup("RESERVED", "program").region == "program"


def test_lookup_unknown_name():
    with pytest.raises(KeyError):
        p.lookup("NOSUCHPARAM")


def test_lookup_unknown_offset():
    with pytest.raises(KeyError):
        p.lookup(("program", 9999))


def test_region_params_rejects_unknown_region():
    with pytest.raises(KeyError):
        p.region_params("nope")


# --- groups -----------------------------------------------------------------


def test_group_prefix_matching_collects_a_branch():
    zones = p.group_params("keygroup.zone")
    assert len(zones) == 56
    assert {x.group for x in zones} == {
        f"keygroup.zone.{n}" for n in (1, 2, 3, 4)
    }


def test_group_prefix_does_not_leak_across_siblings():
    # "keygroup.filter" must not swallow "keygroup.filter2" -- they are
    # different sections of the machine, not a numbered series.
    names = {x.group for x in p.group_params("keygroup.filter")}
    assert names == {"keygroup.filter"}


def test_group_params_for_a_whole_region():
    assert len(p.group_params("keygroup")) == len(p.region_params("keygroup"))


def test_groups_listing():
    assert "program.mods" in p.groups("program")
    assert all(g.startswith("sample") for g in p.groups("sample"))


# --- field codec ------------------------------------------------------------


def test_text_field_round_trip():
    param = p.lookup(("program", "PRNAME"))
    assert p.decode_field(param, p.encode_field(param, "BASS 1")) == "BASS 1"


def test_numeric_field_round_trip():
    param = p.lookup("PRIORT")
    assert p.decode_field(param, p.encode_field(param, 2)) == 2


def test_multibyte_field_is_little_endian():
    param = p.lookup(("program", "KGRP1@"))  # 2 bytes
    assert p.encode_field(param, 0x0102) == b"\x02\x01"


def test_negative_values_store_as_twos_complement():
    param = p.lookup("PANPOS")
    assert p.encode_field(param, -10) == bytes([246])


def test_negative_round_trips_through_describe():
    param = p.lookup("PANPOS")
    raw = p.decode_field(param, p.encode_field(param, -10))
    assert p.describe_value(param, raw) == "-10"


def test_decode_field_rejects_wrong_width():
    with pytest.raises(ValueError):
        p.decode_field(p.lookup("PRIORT"), b"\x00\x00")


def test_encode_field_rejects_overflow():
    with pytest.raises(ValueError):
        p.encode_field(p.lookup("PRIORT"), 300)


# --- presentation -----------------------------------------------------------


def test_describe_value_never_raises_across_every_range():
    """The standing guarantee: a display helper must not take a pane down.

    Ranges are capped because a few fields are 4 bytes wide and walking
    2**32 values proves nothing the first few thousand do not.
    """
    for param in p._PARAMS:
        if param.kind == "text":
            assert isinstance(p.describe_value(param, "ABC"), str)
            continue
        low = param.minimum
        high = min(param.maximum, param.minimum + 512)
        for value in range(low, high + 1):
            assert isinstance(p.describe_value(param, value), str)


def test_describe_value_survives_out_of_range_and_junk():
    param = p.lookup("PRIORT")
    assert isinstance(p.describe_value(param, 999), str)
    assert isinstance(p.describe_value(param, None), str)
    assert isinstance(p.describe_value(param, "not a number"), str)


def test_describe_value_uses_the_enumeration():
    assert p.describe_value(p.lookup("PRIORT"), 2) == "high"


def test_describe_value_uses_sentinels_outside_the_span():
    # 255 = OMNI sits outside PMCHAN's 0..15 documented span; widening the
    # maximum to admit it is deliberate.
    assert p.describe_value(p.lookup("PMCHAN"), 255) == "OMNI"


def test_describe_value_appends_units():
    param = p.lookup(("program", "TEMPER"))
    assert param.unit == "cents"
    assert p.describe_value(param, 10).endswith("cents")


def test_note_valued_parameters_show_a_note_name():
    assert p.describe_value(p.lookup(("program", "PLAYLO")), 21) == "21 (A1)"


def test_note_name_anchors_to_the_specs_low_end():
    # The source says "21 to 127 represents A1 to G8", which cannot be
    # self-consistent; we anchor to the low end. See the note in params.py.
    assert p.note_name(21) == "A1"


def test_writability_flags():
    assert not p.lookup(("program", "GROUPS")).writable  # read-only in the spec
    assert not p.lookup(("program", "KGRP1@")).writable  # internal address
    assert p.lookup("PRIORT").writable


def test_transcription_notes_are_preserved():
    """Prose the range parser could not reduce must survive, not vanish."""
    param = p.lookup(("program", "OUTPUT"))
    assert param.notes and "255" in param.notes


def test_superseded_definition_is_recorded_not_silently_dropped():
    # The keygroup header documents offsets 161/162 twice. The later
    # definition wins, and the earlier name is named in the notes.
    param = p.lookup(("keygroup", 161))
    assert param.name == "KFXCHAN"
    assert "PFXCHAN" in (param.notes or "")
    assert "UNVERIFIED" in (param.notes or "")


def test_text_fields_use_the_device_character_set():
    param = p.lookup(("sample", "SHNAME"))
    assert param.kind == "text"
    assert p.encode_field(param, "A")[0] == m.AKAI_CHARSET.index("A")
