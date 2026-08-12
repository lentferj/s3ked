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
    size = p.region_size(region)
    for param in p.region_params(region):
        assert param.offset >= 0
        assert param.end <= size, (
            f"{param.name} runs to {param.end}, past the {size}-byte {region}"
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
    # 85 not 84 since PRIDENT was added from hardware -- the source document
    # starts the program header at offset 1 and omits the block identifier.
    assert len(p.region_params("program")) == 85
    assert len(p.region_params("keygroup")) == 130
    assert len(p.region_params("sample")) == 35
    assert len(p.region_params("multi")) == 6
    assert len(p.region_params("multipart")) == 13


def test_multi_part_offsets_mirror_the_program_header():
    """The strongest cross-check available without hardware.

    The multi part structure and the program header are documented in two
    separately transcribed Akai documents, and every field they share lands on
    the same offset in both. See RESOLUTION_NOTES §8.
    """
    shared = [x.name for x in p.region_params("multipart")]
    checked = 0
    for name in shared:
        try:
            program = p.lookup(("program", name))
        except KeyError:
            continue
        assert p.lookup(("multipart", name)).offset == program.offset, name
        checked += 1
    assert checked == 12, f"expected 12 shared fields, compared {checked}"


def test_multi_regions_are_marked_as_xl_only():
    for region in ("multi", "multipart"):
        for param in p.region_params(region):
            assert param.models == "S2000/S3000XL/S3200XL", param.name


def test_xl_only_program_fields_are_marked():
    # Introduced under the spec's own "S2000/S3000XL/S3200XL Parameters"
    # heading; they do not exist on a plain S2800/S3000/S3200.
    for name in ("PFXCHAN", "PFXSLEV"):
        assert p.lookup(("program", name)).models == "S2000/S3000XL/S3200XL"


def test_section_headings_did_not_leak_into_descriptions():
    """The transcription's section headings must not end up as field prose."""
    for param in p._PARAMS:
        assert "S2000/S3000XL/S3200XL Parameters" not in (param.desc or "")
        assert "Common Parameters" not in (param.desc or "")
        assert "Accessing" not in (param.desc or "")


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


def test_bare_name_prefers_the_primary_structures_over_multi():
    """A multi part reuses a dozen program-header field names.

    Those are not a collision to refuse -- they are the same field on a
    different structure -- so a bare name resolves to the primary one and the
    multi regions are addressed explicitly.
    """
    assert p.lookup("PANPOS").region == "program"
    assert p.lookup(("multipart", "PANPOS")).region == "multipart"


def test_bare_name_still_resolves_a_multi_only_field():
    assert p.lookup("PTUNOCM").region == "multipart"
    assert p.lookup("MULTINAME").region == "multi"


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
    assert raw == -10
    # PANPOS also carries a measured scale, so the display appends what the
    # value means; what matters here is that the sign survived the trip.
    assert p.describe_value(param, raw).startswith("-10")


def test_decode_field_rejects_wrong_width():
    with pytest.raises(ValueError):
        p.decode_field(p.lookup("PRIORT"), b"\x00\x00")


def test_encode_field_rejects_overflow():
    with pytest.raises(ValueError):
        p.encode_field(p.lookup("PRIORT"), 300)


def test_encode_field_rejects_a_value_the_field_does_not_accept():
    """Fitting in the field's WIDTH is not the same as being in its RANGE.

    MODVFILT1 is -50..+50 in a single byte. 60 fits the byte perfectly and
    encodes without complaint, so it reached the machine and modulated the
    filter the wrong way for an entire calibration run. The check used to
    apply only to display-offset fields, on the reasoning that those wrap
    into two's complement and store nonsense; the others fail more quietly.
    """
    with pytest.raises(ValueError, match="outside"):
        p.encode_field(p.lookup(("keygroup", "MODVFILT1")), 60)
    with pytest.raises(ValueError, match="outside"):
        p.encode_field(p.lookup(("keygroup", "MODVFILT1")), -60)
    assert p.encode_field(p.lookup(("keygroup", "MODVFILT1")), 50) == bytes([50])


def test_a_tuning_field_can_express_a_semitone():
    """The regression the range guard introduced, pinned so it cannot return.

    Six two-byte tuning fields were declared 0..50, which is the document's
    display range in SEMITONES transcribed as a raw range. One raw unit is
    1/256 of a semitone, so 0..50 caps the field at 19.53 cents and makes an
    ordinary one-semitone detune impossible. Tightening encode_field turned a
    dormant table error into a refusal to write legal values -- exactly the
    "over-tight check is the worse bug" case.
    """
    for region, name in (("keygroup", "KGTUNO"), ("program", "PTUNO"),
                         ("keygroup", "VTUNO1"), ("keygroup", "VTUNO2"),
                         ("keygroup", "VTUNO3"), ("keygroup", "VTUNO4")):
        param = p.lookup((region, name))
        assert param.size == 2
        assert param.minimum == -12800 and param.maximum == 12800
        p.encode_field(param, 256)        # one semitone up
        p.encode_field(param, -256)       # one semitone down
        p.encode_field(param, 5120)       # +20 semitones, measured
    assert p.encode_field(p.lookup(("keygroup", "KGTUNO")), 256) == b"\x00\x01"
    assert p.encode_field(p.lookup(("keygroup", "KGTUNO")), -256) == b"\x00\xff"


def test_no_multi_byte_field_declares_a_single_byte_range():
    """A 2-byte field whose maximum fits in one byte is the tell.

    That mismatch is what a display range transcribed as a value range looks
    like, and it hid in six fields until a range check made it bite. TEMPER is
    exempt: it is genuinely twelve independent signed bytes, one per semitone,
    and its -50..50 is per element.
    """
    for param in p.PARAMETERS.values():
        if getattr(param, "kind", "") == "text" or param.size < 2:
            continue
        if param.name == "TEMPER":
            continue
        assert param.maximum > 255, (
            f"{param.name} is {param.size} bytes but declares a range that "
            f"fits in one -- check whether that is the document's DISPLAY "
            f"range")


def test_encode_field_still_accepts_every_legal_value_of_every_field():
    """The guard must not reject anything the table itself declares legal.

    Both ends of every numeric field, plus every enumerated value -- an
    over-tight range check would be a worse bug than the one it replaced,
    and it would show up as fields that cannot be written at all.
    """
    for (region, name), param in p.PARAMETERS.items():
        if getattr(param, "kind", "") == "text" or not param.writable:
            continue
        for value in (param.minimum, param.maximum):
            p.encode_field(param, value + param.display_offset)
        for key in (param.values or {}):
            if isinstance(key, int):
                p.encode_field(param, key)


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
    assert p.describe_value(p.lookup(("program", "PLAYLO")), 21) == "21 (A-1)"
    assert p.describe_value(p.lookup(("multipart", "PLAYHI")), 127) == "127 (G8)"


def test_note_name_matches_the_front_panel():
    """Settled from three sources; see RESOLUTION_NOTES §4.

    The S2800 document's "A1 to G8" drops a minus sign. The S2000/S3000XL
    document writes "A-1 to G8", and the owner's manual shows the panel
    rendering keyspans C_0 .. G_8. All three agree on offset -2.
    """
    assert p.note_name(21) == "A-1"
    assert p.note_name(24) == "C0"
    assert p.note_name(60) == "C3"
    assert p.note_name(127) == "G8"


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


# --- display offset: stored byte vs the number on the panel -----------------
# Confirmed on hardware 2026-08-10: the panel shows polyphony 32 where the
# byte holds 31 (RESOLUTION_NOTES §11 Finding H).


def test_polyph_renders_the_number_the_panel_shows():
    """Through the real path: raw byte -> decode_field -> describe_value.

    describe_value takes a *display* value, because decode_field has already
    applied the offset. Asserting against a raw byte here would encode the
    asymmetry the offset exists to remove.
    """
    param = p.lookup(("program", "POLYPH"))
    assert param.display_offset == 1
    assert p.describe_value(param, p.decode_field(param, bytes([31]))) == "32"
    assert p.describe_value(param, p.decode_field(param, bytes([0]))) == "1"


def test_polyph_accepts_the_number_the_panel_shows():
    param = p.lookup(("program", "POLYPH"))
    assert p.encode_field(param, 32) == bytes([31])
    assert p.encode_field(param, 1) == bytes([0])


@pytest.mark.parametrize("value", [0, 33, -1])
def test_polyph_rejects_values_outside_the_displayed_range(value):
    """Without the guard, 0 would wrap to 255 rather than being refused."""
    param = p.lookup(("program", "POLYPH"))
    with pytest.raises(ValueError):
        p.encode_field(param, value)


def test_display_offset_round_trips():
    param = p.lookup(("program", "POLYPH"))
    for shown in range(1, 33):
        stored = p.encode_field(param, shown)
        assert p.describe_value(param, p.decode_field(param, stored)) == str(shown)


def test_every_other_parameter_is_unaffected():
    """Exactly one field has a stored-vs-displayed mapping; keep it that way."""
    offsets = {x.name for x in p._PARAMS if x.display_offset}
    assert offsets == {"POLYPH"}


def test_decode_and_encode_are_inverses_for_every_parameter():
    """A read-modify-write must be stable.

    An asymmetric decode/encode pair does not merely display the wrong
    number -- it walks the stored value on every round trip, which a
    verification sweep performs hundreds of times.
    """
    for param in p._PARAMS:
        if param.kind == "text":
            continue
        low = param.minimum + param.display_offset
        high = param.maximum + param.display_offset
        for pattern in (0x00, 0x01, 0x7F, 0x80, 0xCE, 0xFF):
            raw = bytes([pattern] * param.size)
            value = p.decode_field(param, raw)
            if not low <= value <= high:
                continue  # not a byte this field can legally hold
            assert p.encode_field(param, value) == raw, f"{param.name} {raw.hex()}"


def test_encode_then_decode_survives_a_negative_value():
    """The other direction, which is where the sign extension was missing.

    `encode_field` has always two's-complemented a negative; `decode_field`
    read it back unsigned, so -50 became 206 and a caller checking the
    parameter's own range saw every signed field as out of bounds.
    """
    for param in p._PARAMS:
        if param.kind == "text" or param.minimum >= 0:
            continue
        for value in (param.minimum, -1, 0, param.maximum):
            assert p.decode_field(param, p.encode_field(param, value)) == value, (
                f"{param.name} {value}"
            )


def test_decoded_values_lie_inside_the_declared_range():
    """A decoded value must be comparable against the range it came with."""
    param = p.lookup(("program", "PANPOS"))
    assert param.minimum < 0
    decoded = p.decode_field(param, p.encode_field(param, param.minimum))
    assert param.minimum <= decoded <= param.maximum


def test_the_machine_managed_sample_fields_are_read_only():
    """Four fields the S3000XL acknowledges a write to and then ignores.

    Confirmed on hardware 2026-08-10 (RESOLUTION_NOTES §12): each returned
    REPLY/ok and kept its own value. SLOOPS was probed separately against 0,
    2, 3 and 4 and held 1 throughout, so it is not a minimum-of-1 constraint.
    Their own descriptions said "internal use" all along.
    """
    for name in ("SLOOPS", "SALOOP", "SHLOOP", "SSPARE"):
        param = p.lookup(("sample", name))
        assert param.readonly, name
        assert not param.writable, name


def test_modulation_sources_are_named_not_bare_numbers():
    """A field whose meaning lives in a separate table gets misread.

    These read as bare integers until 2026-08-10, and `0` was reported to a
    sibling project as a live routing when it means "no source" -- the slot
    is off. RESOLUTION_NOTES §15.
    """
    assert p.MOD_SOURCES[0] == "no source"
    wired = [x for x in p._PARAMS if x.values is p.MOD_SOURCES]
    assert len(wired) == 16, [x.name for x in wired]
    for param in wired:
        assert p.describe_value(param, 0) == "no source", param.name
        assert p.describe_value(param, 8) == "LFO2", param.name


def test_the_optional_filter_board_fields_are_marked_as_optional():
    """Filter 2, TONE and ENV3 need hardware not every machine has.

    The S3200 carries the second LSI as standard; an S3000XL needs the
    optional IB304F board, and without it the machine answers "2nd filter
    board IB304F not fitted!" at the panel. The fields exist in the header
    either way, so nothing on the wire distinguishes them -- which is exactly
    why the table has to say so. RESOLUTION_NOTES §19.
    """
    expected = {"FLT2GAIN", "FLT2MODE", "FLT2Q", "TONEFREQ", "TONESLOP",
                "FIL2FR", "K_FRQ2"} | {f"ENV3{s}" for s in
                                       ("R1", "L1", "R2", "L2", "R3", "L3",
                                        "R4", "L4")}
    marked = {x.name for x in p._PARAMS if x.models and "IB304F" in x.models}
    assert marked == expected, marked ^ expected
