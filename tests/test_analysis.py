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
"""The cross-reference, and the two traps it exists to avoid."""
from s3k import analysis as a
from s3k import messages as m
from s3k import params as p

ZONE_OFFSETS = [p.lookup(("keygroup", f)).offset for f in a.ZONE_FIELDS]
GROUPS = p.lookup(("program", "GROUPS"))


class FakeBank:
    """A bank described as {program name: [[zone names per keygroup], ...]}.

    ``None`` in a zone means nothing assigned, and is stored as twelve
    SPACES, which is what the machine actually does -- measured on a loaded
    program, where an unused zone reads [10]*12.

    This fixture used to store twelve ZEROES, which no machine ever produces.
    Nine tests passed against it and the first real bank produced 182
    references to ''. A fixture is a claim about the device, and this one was
    wrong.
    """

    def __init__(self, bank, samples):
        self.bank = bank
        self.samples = samples
        self.reads = 0

    def program_list(self, *, timeout=None):
        return list(self.bank)

    def sample_list(self, *, timeout=None):
        return list(self.samples)

    def get_parameter(self, param, index, *, timeout=None, **kw):
        assert param is GROUPS
        return len(list(self.bank.values())[index])

    def get_header_bytes(self, region, index, offset, size, *, selector=0,
                         timeout=None):
        """Name, then LOVEL, then HIVEL -- contiguous, as on the machine.

        A zone entry is either a name, or ``(name, lo, hi)`` when the test
        cares about the velocity pair. Default is 0..127, which is what
        every zone on every bank this project has loaded actually reads.
        """
        self.reads += 1
        assert region == "keygroup"
        keygroup = list(self.bank.values())[index][selector]
        zone = ZONE_OFFSETS.index(offset)
        entry = keygroup[zone] if zone < len(keygroup) else None
        lo, hi = 0, 127
        if isinstance(entry, tuple):
            entry, lo, hi = entry
        name = m.encode_name(" " if entry is None else entry)
        return bytes(list(name) + [lo, hi])[:size]


def test_a_dangling_reference_is_a_name_no_resident_sample_carries():
    bank = {"KIT": [["KICK", "SNARE", None, None]],
            "PAD": [["MISSING PAD", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK", "SNARE"]))

    dangling = audit.dangling()
    assert [r.sample for r in dangling] == ["MISSING PAD"]
    assert dangling[0].program == 1
    assert dangling[0].keygroup == 0 and dangling[0].zone == 1
    assert "DANGLING" in audit.summary()


def test_an_unassigned_zone_holds_spaces_which_is_what_the_machine_stores():
    """Measured: an unused zone reads [10]*12 and decodes to blank.

    Counting those as references reported 182 dangling samples named '' on a
    real two-program bank.
    """
    assert m.encode_name(" ") == [10] * m.NAME_LENGTH

    bank = {"KIT": [["KICK", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK"]))

    assert len(audit.references) == 1, "only the assigned zone counts"
    assert [r.sample for r in audit.references] == ["KICK"]
    assert audit.dangling() == []


def test_an_unwritten_zone_of_zero_bytes_is_also_unassigned():
    """Zeros decode to '000000000000', not to blank, so this needs its own
    check -- a blank test alone would call it a reference to that name."""
    assert m.decode_name([0] * m.NAME_LENGTH) == "0" * m.NAME_LENGTH

    class Zeroed(FakeBank):
        def get_header_bytes(self, region, index, offset, size, *,
                             selector=0, timeout=None):
            got = super().get_header_bytes(region, index, offset, size,
                                           selector=selector, timeout=timeout)
            if not bytes(got[:m.NAME_LENGTH]).strip(bytes([10])):
                return bytes(m.NAME_LENGTH) + bytes(got[m.NAME_LENGTH:])
            return got

    audit = a.collect(Zeroed({"KIT": [["KICK", None, None, None]]}, ["KICK"]))
    assert [r.sample for r in audit.references] == ["KICK"]


def test_a_sample_named_all_zeroes_cannot_be_told_from_an_empty_zone():
    """The trap has a second half, and it defeats the raw-bytes check.

    encode_name("000000000000") is twelve zero bytes -- exactly what an
    unassigned zone holds. Reading raw bytes rather than decoded text does
    NOT separate them, which this module assumed until this test.

    So the convention is "zeros mean empty", and the pathological case is
    reported rather than silently swallowed.
    """
    odd = "0" * m.NAME_LENGTH
    assert not any(m.encode_name(odd)), "the collision this test is about"

    bank = {"ODD": [[odd, None, None, None]]}
    audit = a.collect(FakeBank(bank, [odd]))

    assert audit.references == [], "zeros are read as empty, by convention"
    assert audit.dangling() == [], "and so nothing is reported missing"
    assert audit.indistinguishable == [odd]
    assert "lower bound" in audit.summary()
    assert audit.usage(odd) == [], "a lower bound, and here it is zero"


def test_usage_answers_who_uses_this_sample():
    bank = {"A": [["KICK", None, None, None], ["KICK", "HAT", None, None]],
            "B": [["HAT", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK", "HAT"]))

    kick = audit.usage("KICK")
    assert len(kick) == 2
    assert {(r.program, r.keygroup) for r in kick} == {(0, 0), (0, 1)}
    assert len(audit.usage("HAT")) == 2
    assert audit.usage("NOBODY") == []


def test_orphans_are_samples_nothing_points_at():
    bank = {"A": [["KICK", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK", "UNUSED", "ALSO UNUSED"]))
    assert audit.orphans() == ["UNUSED", "ALSO UNUSED"]


def test_duplicate_sample_names_are_reported_as_ambiguous():
    """The machine enforces no uniqueness, and zones reference by name."""
    bank = {"A": [["TWICE", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["TWICE", "TWICE", "ONCE"]))

    assert audit.ambiguous() == {"TWICE": 2}
    assert audit.dangling() == [], "ambiguous is not the same as missing"
    assert "duplicated" in audit.summary()


def test_the_walk_is_bounded_by_GROUPS_and_not_by_a_guess():
    """Reading past the last keygroup returns a stale buffer, not an error.

    The extended layer does not bounds-check (§11), so a walk with a fixed
    upper bound would manufacture references by re-reading the last real
    keygroup -- and they would look entirely plausible.
    """
    bank = {"SMALL": [["ONE", None, None, None]]}
    fake = FakeBank(bank, ["ONE"])
    audit = a.collect(fake)

    assert len(audit.references) == 1
    assert fake.reads == 5, "four zones plus one key range, and no more"


def test_programs_playing_silence_groups_by_program_worst_first():
    bank = {"OK": [["HERE", None, None, None]],
            "BAD": [["GONE", "ALSO GONE", None, None]],
            "WORSE": [["X", "Y", "Z", None], ["W", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["HERE"]))

    grouped = audit.programs_playing_silence()
    assert list(grouped) == [2, 1], "four missing before two"
    assert 0 not in grouped


def test_a_program_whose_keygroup_count_cannot_be_read_is_reported_not_skipped():
    """Silence about an unread program would look like a clean bank."""
    bank = {"FINE": [["OK", None, None, None]], "BROKEN": [[None] * 4]}

    class Grumpy(FakeBank):
        def get_parameter(self, param, index, *, timeout=None, **kw):
            if index == 1:
                raise RuntimeError("no reply")
            return super().get_parameter(param, index, timeout=timeout, **kw)

    audit = a.collect(Grumpy(bank, ["OK"]))
    assert audit.unread == [(1, "BROKEN")]
    assert "could not be read" in audit.summary()


def test_a_zone_that_cannot_sound_is_not_reported_as_missing():
    """A disabled zone keeps whatever name it last held.

    Velocity 0 is note-off, so hi_vel == 0 can never be selected whatever
    the low value says, and both spellings seen in real material -- (1, 0)
    inverted and (0, 0) -- leave a leftover name behind, often a ROM
    waveform's. Reporting those as missing samples is a fault the user
    cannot act on and did not cause.

    Measured by the sibling mpc2emu over 54,488 zones from real discs. Not
    reproduced on this project's hardware, where every zone reads 0..127 --
    the layout is confirmed here, the semantics are theirs.
    """
    bank = {"KIT": [["LIVE", ("GONE INVERT", 1, 0), ("GONE FLAT", 0, 0),
                     ("ALSO GONE", 64, 0)]]}
    audit = a.collect(FakeBank(bank, ["LIVE"]))

    assert len(audit.references) == 4, "all four zones are still named"
    assert audit.dangling() == [], "none of the three can ever sound"
    assert len(audit.suppressed()) == 3
    assert "cannot sound" in audit.summary()

    everything = audit.dangling(include_unreachable=True)
    assert len(everything) == 3, "asked for, and returned"


def test_a_reachable_zone_naming_a_missing_sample_is_still_reported():
    """The suppression must not swallow the case the check exists for."""
    bank = {"KIT": [[("MISSING", 0, 127), ("DISABLED", 0, 0), None, None]]}
    audit = a.collect(FakeBank(bank, ["SOMETHING ELSE"]))

    assert [r.sample for r in audit.dangling()] == ["MISSING"]
    assert [r.sample for r in audit.suppressed()] == ["DISABLED"]


def test_the_walk_costs_four_reads_per_zone_plus_one_per_keygroup():
    """The velocity pair is free; the key range is one read per keygroup.

    LOVEL and HIVEL are contiguous with the name, so a 14-byte read gets all
    three where the name alone took 12 -- fetching them separately would
    have tripled the walk. LONOTE and HINOTE live at the top of the keygroup
    header, so they cost one 2-byte read, once per keygroup rather than once
    per zone.
    """
    bank = {"KIT": [["A", "B", None, None], ["C", None, None, None]]}
    fake = FakeBank(bank, ["A", "B", "C"])
    a.collect(fake)
    assert fake.reads == 2 * (4 + 1), "two keygroups, four zones plus a range"


def test_an_unencodable_sample_name_does_not_abort_the_whole_audit():
    """encode_name refuses what the device cannot store, which is right for
    writing and wrong for classifying names that already exist. Letting it
    raise took the entire audit down over one odd name."""
    import pytest

    with pytest.raises(ValueError):
        m.encode_name("a lowercase name")

    bank = {"KIT": [["KICK", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK", "a lowercase name"]))

    assert [r.sample for r in audit.references] == ["KICK"]
    assert audit.indistinguishable == [], "unencodable is not all-zero"
    assert "a lowercase name" in audit.orphans()


def test_a_blank_sample_name_collides_with_an_empty_zone_too():
    """An encoder that substitutes can write a name that vanishes.

    mpc2emu's str_to_akai turns anything the Akai alphabet lacks into a
    space, so a sample named entirely of CJK or punctuation encodes to
    twelve spaces -- byte-identical to an unassigned zone. A zone naming it
    reads as empty here, and the sample looks like an orphan.
    """
    bank = {"KIT": [["KICK", None, None, None]]}
    audit = a.collect(FakeBank(bank, ["KICK", "   ", ""]))

    assert audit.indistinguishable == ["   ", ""]
    assert "lower bound" in audit.summary()
    # and it is not mistaken for a dangling reference
    assert audit.dangling() == []


def test_an_inverted_velocity_range_is_dead_too():
    """Measured, not reasoned: a machine could clamp, swap or wrap it.

    On an S3000XL, lo=100 hi=50 at velocity 75 gave 0.00003 RMS against
    0.00711 for the full range -- as silent as an out-of-range zone, and the
    pair read back as written, so nothing was swapped.
    """
    bank = {"KIT": [[("BACKWARDS", 100, 50), ("FORWARDS", 0, 127), None, None]]}
    audit = a.collect(FakeBank(bank, []))

    by_zone = {r.zone: r for r in audit.references}
    assert by_zone[1].reachable is False, "lo > hi selects nothing"
    assert by_zone[2].reachable is True

    assert [r.sample for r in audit.dangling()] == ["FORWARDS"]
    assert [r.sample for r in audit.suppressed()] == ["BACKWARDS"]


def test_a_single_velocity_point_is_still_reachable():
    """lo == hi is a one-velocity zone, not an empty one."""
    bank = {"KIT": [[("PINPOINT", 64, 64), None, None, None]]}
    audit = a.collect(FakeBank(bank, []))
    assert audit.references[0].reachable is True
    assert [r.sample for r in audit.dangling()] == ["PINPOINT"]


def test_an_inverted_key_range_kills_every_zone_in_the_keygroup():
    """Measured: 72..48 at note 60 gave 0.00003 RMS against 0.00711 full.

    A dead key range kills the whole keygroup, unlike a dead velocity pair
    which kills only its own zone.
    """
    class KeyRanged(FakeBank):
        def __init__(self, bank, samples, ranges):
            super().__init__(bank, samples)
            self.ranges = ranges

        def get_header_bytes(self, region, index, offset, size, *,
                             selector=0, timeout=None):
            if size == 2:                      # the key-range read
                lo, hi = self.ranges[selector]
                return bytes([lo, hi])
            return super().get_header_bytes(region, index, offset, size,
                                            selector=selector, timeout=timeout)

    bank = {"KIT": [["A", "B", None, None], ["C", None, None, None]]}
    audit = a.collect(KeyRanged(bank, [], {0: (72, 48), 1: (24, 127)}))

    dead = [r for r in audit.references if not r.reachable]
    assert {r.sample for r in dead} == {"A", "B"}, "both zones of keygroup 0"
    assert [r.sample for r in audit.dangling()] == ["C"]


def test_a_single_key_keygroup_is_reachable():
    """60..60 sounded on note 60 at 0.00712 -- a `lo < hi` guard kills it."""
    class KeyRanged(FakeBank):
        def get_header_bytes(self, region, index, offset, size, *,
                             selector=0, timeout=None):
            if size == 2:
                return bytes([60, 60])
            return super().get_header_bytes(region, index, offset, size,
                                            selector=selector, timeout=timeout)

    audit = a.collect(KeyRanged({"KIT": [["ONE KEY", None, None, None]]}, []))
    assert audit.references[0].reachable is True
    assert [r.sample for r in audit.dangling()] == ["ONE KEY"]
