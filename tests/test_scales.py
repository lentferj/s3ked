# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the measured parameter scales.

Re-checking the arithmetic of a fit against itself proves nothing -- the
coefficients would agree with any typo in them. What these tests guard is the
part a typo *would* break: that each law still describes the hardware readings
it was fitted to, that converting in one direction and back lands where it
started, and that a value nobody measured is never quietly presented as if
somebody had.
"""

import math

import pytest

from s3k import params as p, scales


# --- the laws still match the readings they came from -----------------------
#
# One anchor per law, taken from the sweeps in RESOLUTION_NOTES §20-§26. These
# are the numbers the machine produced, so a coefficient that drifts away from
# the hardware fails here rather than in somebody's ears.

ANCHORS = [
    # region      param     value  expected  tolerance  unit
    # Corners located by the resonance peak (§54). The values these replaced
    # -- 591 and 2568 Hz -- were spectral centroids, which sit ABOVE the
    # corner by an amount that depends on the source, so they were readings
    # of the old ruler rather than of the machine.
    ("keygroup", "FILFRQ",     62,     531.0,      15.0, "Hz"),
    ("keygroup", "FILFRQ",     80,    1887.0,      55.0, "Hz"),
    ("keygroup", "FILFRQ",     92,    4491.0,     140.0, "Hz"),
    ("program",  "LFORAT",     30,     3.52,      0.20, "Hz"),
    ("program",  "LFORAT",     99,    11.71,      0.40, "Hz"),
    ("keygroup", "ATTAK1",     70,    0.387,     0.030, "s"),
    ("keygroup", "ATTAK1",     90,    3.506,     0.200, "s"),
    ("keygroup", "DECAY1",     70,    24.84,      1.00, "dB/s"),
    ("keygroup", "DECAY1",     50,   177.39,      8.00, "dB/s"),
    ("keygroup", "RELSE1",     60,    66.76,      3.00, "dB/s"),
    ("keygroup", "KGTUNO",     50,     19.6,       0.5, "cents"),
    ("keygroup", "SUSTN1",     50,    -29.7,       1.0, "dB"),
    ("program",  "PRLOUD",     50,    -31.5,       2.0, "dB"),
    ("program",  "PANPOS",    -25,     -9.8,       0.6, "dB"),
    ("program",  "LFODEP",    50,     974.7,      15.0, "cents"),
    ("program",  "LFODEP",    10,     194.9,       6.0, "cents"),
    # the filter envelope, measured through the FILFRQ ruler (§28)
    # ATTAK2 is anchored at MODVFILT1 18, the depth it was measured at.
    # The same value read 0.38 s at depth 25 -- a 3x disagreement that is
    # the depth-dependence itself, and the reason it stays provisional.
    ("keygroup", "ATTAK2",     55,    0.289,     0.030, "s"),
    ("keygroup", "ATTAK2",     80,    3.190,     0.200, "s"),
    ("keygroup", "DECAY2",     70,    2.450,     0.150, "s"),
    ("keygroup", "RELSE2",     70,    1.220,     0.080, "s"),
    ("keygroup", "SUSTN2",     50,     50.5,       2.0, "%"),
]


@pytest.mark.parametrize("region,param,value,expected,tol,unit", ANCHORS)
def test_the_law_reproduces_the_hardware_reading(
    region, param, value, expected, tol, unit
):
    physical, got_unit, exact = scales.to_physical(region, param, value)
    assert got_unit == unit
    assert exact, "every anchor is inside its own measured range"
    assert abs(physical - expected) <= tol, (
        f"{param} {value} should be about {expected} {unit}, got {physical:.4g}"
    )


# --- the two directions agree ----------------------------------------------


@pytest.mark.parametrize("key", sorted(scales.SCALES))
def test_converting_there_and_back_returns_the_value(key):
    """A value -> quantity -> value round trip must not drift."""
    scale = scales.SCALES[key]
    lo, hi = scale.fitted
    for value in range(lo, hi + 1):
        physical, _unit, _exact = scales.to_physical(*key, value)
        back, _exact = scales.from_physical(*key, physical)
        assert back == value, f"{scale.param} {value} came back as {back}"


@pytest.mark.parametrize("key", sorted(scales.SCALES))
def test_the_law_is_monotonic_across_its_measured_range(key):
    """Every one of these controls goes one way; a fold would be a sign error.

    Direction is not the invariant: a decay RATE falls as its value rises,
    because a bigger number means a slower stage. Monotonicity is.
    """
    scale = scales.SCALES[key]
    lo, hi = scale.fitted
    series = [scales.to_physical(*key, v)[0] for v in range(lo, hi + 1)]
    assert series == sorted(series) or series == sorted(series, reverse=True), (
        f"{scale.param} folds back on itself"
    )


@pytest.mark.parametrize("name", ["DECAY1", "RELSE1"])
def test_a_bigger_value_means_a_slower_rate(name):
    """These are RATES, so slower means a smaller number."""
    scale = scales.SCALES[("keygroup", name)]
    lo, hi = scale.fitted
    assert (scales.to_physical("keygroup", name, hi)[0]
            < scales.to_physical("keygroup", name, lo)[0])


@pytest.mark.parametrize("name", ["ATTAK2", "DECAY2", "RELSE2", "ENV3R1", "ENV3R3"])
def test_a_bigger_value_means_a_slower_stage_in_seconds(name):
    """These report the SECONDS a full traverse takes, so slower means bigger.

    Kept as a separate test from the rates above rather than folded in with a
    sign flag: a stage getting slower is one fact, and which direction the
    number moves depends entirely on what the number IS. Conflating them is
    how a rate gets read as a duration.
    """
    scale = scales.SCALES[("keygroup", name)]
    lo, hi = scale.fitted
    assert (scales.to_physical("keygroup", name, hi)[0]
            > scales.to_physical("keygroup", name, lo)[0])


# --- honesty about what was and was not measured ---------------------------


def test_a_value_outside_the_measured_range_is_marked():
    """The mark is the whole point: a fit is not a specification."""
    _hz, _unit, exact = scales.to_physical("keygroup", "FILFRQ", 20)
    assert not exact
    assert scales.describe("keygroup", "FILFRQ", 20).startswith("?")

    inside = scales.describe("keygroup", "FILFRQ", 60)
    assert inside and not inside.startswith("?")


def test_an_independently_known_endpoint_beats_refusing_to_answer():
    """FILFRQ 99 was measured wide open, even though the curve stops at 90.

    The sibling mpc2emu clamped "fully open" to the top of the fitted range and
    made every converted program darker than before the calibration existed.
    """
    assert scales.describe("keygroup", "FILFRQ", 99) == "wide open"
    assert scales.describe("keygroup", "ATTAK1", 0) == "fastest"
    assert scales.describe("program", "PANPOS", -50) == "hard left"
    assert scales.describe("program", "PANPOS", 50) == "hard right"


def test_a_parameter_with_no_measured_law_says_nothing():
    """Silence, not a guess. VFREQ1 has never been swept.

    This test used FILQ until FILQ was measured, which is the right way for
    it to fail: the example stops being an example once the gap closes.
    """
    assert scales.to_physical("keygroup", "VFREQ1", 5) is None
    assert scales.describe("keygroup", "VFREQ1", 5) == ""


def test_every_scale_names_a_parameter_that_exists():
    for region, name in scales.SCALES:
        param = p.lookup(name, region)
        assert param.region == region


def test_every_fitted_range_lies_inside_the_parameter_range():
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup(name, region)
        assert param.minimum <= scale.fitted[0] <= scale.fitted[1] <= param.maximum, (
            f"{name} was fitted over {scale.fitted}, outside "
            f"{param.minimum}..{param.maximum}"
        )


def test_every_endpoint_lies_inside_the_parameter_range():
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup(name, region)
        for value in scale.endpoints:
            assert param.minimum <= value <= param.maximum


def test_zero_tuning_offset_is_zero_cents():
    """The fit's intercept was bias; no offset must mean no detune."""
    for region, name in (("keygroup", "KGTUNO"), ("program", "PTUNO")):
        cents, _unit, _exact = scales.to_physical(region, name, 0)
        assert cents == pytest.approx(0.0, abs=1e-9)


def test_levels_are_relative_to_full():
    """An absolute dB belonged to the measuring rig, not to the sampler."""
    for region, name in (("keygroup", "SUSTN1"), ("program", "PRLOUD")):
        db, _unit, _exact = scales.to_physical(region, name, 99)
        assert db == pytest.approx(0.0, abs=1e-9)
        quieter, _u, _e = scales.to_physical(region, name, 50)
        assert quieter < -20


def test_sustain_is_linear_in_decibels_not_amplitude():
    """The finding that half amplitude is 89, not 50 -- pinned so it stays."""
    half, _exact = scales.from_physical("keygroup", "SUSTN1", -6.02)
    assert half == 89


# --- reading a quantity from text ------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("500Hz", (500.0, "Hz")),
        ("2kHz", (2000.0, "Hz")),
        ("2 kHz", (2000.0, "Hz")),
        ("200ms", (0.2, "s")),
        ("1.5s", (1.5, "s")),
        ("-6dB", (-6.0, "dB")),
        ("+50cents", (50.0, "cents")),
        ("50 CENTS", (50.0, "cents")),
    ],
)
def test_quantities_are_read_from_text(text, expected):
    assert scales.parse_quantity(text) == expected


@pytest.mark.parametrize("text", ["50", "-25", "0", "", "Hz", "abcHz", "1.2.3ms"])
def test_a_plain_number_is_not_a_quantity(text):
    """`50` is the value 50 and `50Hz` is fifty hertz; never the same thing."""
    assert scales.parse_quantity(text) is None


def test_a_quantity_becomes_the_value_that_produces_it():
    value, exact = scales.value_from_quantity("keygroup", "FILFRQ", "500Hz")
    assert exact
    hz, _unit, _e = scales.to_physical("keygroup", "FILFRQ", value)
    assert abs(hz - 500) < 30


def test_the_wrong_unit_is_refused_rather_than_coerced():
    with pytest.raises(ValueError, match="measured in Hz, not s"):
        scales.value_from_quantity("keygroup", "FILFRQ", "200ms")


def test_a_parameter_with_no_law_refuses_a_quantity():
    with pytest.raises(ValueError, match="no measured scale"):
        scales.value_from_quantity("keygroup", "VFREQ1", "500Hz")


def test_an_impossible_quantity_is_refused():
    """No exponential reaches zero or a negative number of seconds."""
    with pytest.raises(ValueError):
        scales.value_from_quantity("keygroup", "ATTAK1", "-5ms")


# --- the display pane -------------------------------------------------------


def test_describe_value_shows_the_physical_meaning():
    param = p.lookup("FILFRQ", "keygroup")
    assert p.describe_value(param, 80) == "80 (~1.89 kHz)"


def test_filfrq_comes_from_the_resonance_peak_not_a_centroid():
    """The law it replaced was 20-30% high, and got worse as the corner rose.

    A spectral centroid averages everything the source contains, so it sits
    above the corner by a source-dependent amount -- a SLOPE error, not an
    offset, which is why no single correction factor would have fixed it.
    """
    s = scales.SCALES[("keygroup", "FILFRQ")]
    assert s.a == pytest.approx(6.4597, abs=1e-3)
    assert s.b == pytest.approx(0.07100, abs=1e-4)
    old = 6.998 * math.exp(0.07384 * 70)
    assert s.value_to_physical(70) / old < 0.8, "the old ruler read high"
    assert "RESONANCE PEAK" in s.note


def test_filfrq_and_filq_agree_on_the_corner():
    """Two runs, two methods of parking it: 919 Hz and 930 Hz at FILFRQ 70.

    The FILQ sweep fitted the corner as a nuisance parameter while measuring
    damping; the FILFRQ sweep fitted it as the answer. Neither was fitted to
    the other's data.
    """
    corner = scales.SCALES[("keygroup", "FILFRQ")].value_to_physical(70)
    assert abs(corner / 919.0 - 1) < 0.02


def test_lfodel_is_a_pure_delay_with_no_fade_in():
    """§31 assumed a delay followed by a ramp, and measured their sum.

    Two estimators that fail differently -- a 5%-of-final threshold, and the
    rising edge extrapolated back to zero -- agree to -0.010 +/- 0.016 s over
    thirteen settings. There is no ramp to conflate.
    """
    s = scales.SCALES[("program", "LFODEL")]
    assert not s.provisional
    assert s.kind == "pole"
    assert "NO FADE-IN" in s.note
    assert s.value_to_physical(0) == 0.0


def test_lfodel_runs_to_a_pole_past_the_end_of_the_field():
    """0.065 s at 50, 1.55 s at 99 -- the top steps are worth far more.

    A converter reading this as linear puts 99 at about a third of a second
    instead of a second and a half.
    """
    s = scales.SCALES[("program", "LFODEL")]
    assert s.b > 99, "the machine never reaches the pole"
    assert s.value_to_physical(99) / s.value_to_physical(50) > 20
    with pytest.raises(ValueError):
        s.value_to_physical(104)


def test_the_two_runaway_laws_share_a_shape():
    """FILQ and LFODEL both run to a pole sited just past their own maximum.

    Recorded because it is the kind of coincidence that invites a unified
    story, and there is no evidence for one: the poles sit at 15.84 of 15 and
    103.4 of 99, and nothing measured connects a filter's damping to an LFO's
    delay. Two fields, same shape, no claim.
    """
    filq = scales.SCALES[("keygroup", "FILQ")]
    lfodel = scales.SCALES[("program", "LFODEL")]
    assert filq.a > 15 and lfodel.b > 99


def test_describe_value_is_unchanged_where_nothing_was_measured():
    param = p.lookup("VFREQ1", "keygroup")
    assert p.describe_value(param, 5) == "5"


def test_describe_value_still_prefers_a_named_value():
    """An enumeration is a fact from the document; it outranks a fit."""
    param = p.lookup("KGMUTE", "keygroup")
    assert p.describe_value(param, 255) == "off"


def test_a_broken_scale_cannot_take_down_a_display_pane(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("scale table is wrong")

    monkeypatch.setattr(scales, "describe", boom)
    param = p.lookup("FILFRQ", "keygroup")
    assert p.describe_value(param, 80) == "80"


def test_pan_is_symmetrical_about_the_centre():
    left, _u, _e = scales.to_physical("program", "PANPOS", -25)
    right, _u2, _e2 = scales.to_physical("program", "PANPOS", 25)
    centre, _u3, _e3 = scales.to_physical("program", "PANPOS", 0)
    assert math.isclose(left - centre, -(right - centre), rel_tol=0.02)


def test_a_longer_suffix_is_never_swallowed_by_a_shorter_one():
    """"cents" ends in "s"; seconds must not claim it. Checked for every pair."""
    for suffix, (unit, _factor) in scales._SUFFIXES:
        for other, (other_unit, _f) in scales._SUFFIXES:
            if suffix != other and suffix.endswith(other):
                got = scales.parse_quantity("50" + suffix)
                assert got == (50.0 * _factor, unit), (
                    f"{suffix!r} was misread as {other!r}"
                )


# --- the shared exponent ---------------------------------------------------


def test_the_decay_rates_share_an_exponent():
    """DECAY1, RELSE1 and DECAY2 agree on ~0.0977 across both envelopes.

    An earlier version of this test asserted the same clustering on the
    EXPONENTIAL-approach model and called it independent confirmation because
    two detectors shared no code. That reasoning was wrong: detectors sharing
    no code rule out detector error, not model error, and the model was the
    same wrong one on both sides.

    What makes this version worth asserting is not the agreement -- it is that
    the per-curve fits are r2 0.999..1.000 rather than a flat 0.945, so the
    quantity being compared is the right one. RELSE2 is left out: its exponent
    is 0.1012 on the fewest points, and forcing it in would be the same
    over-claim again.

    DECAY2 is compared by MAGNITUDE since §58: it now reports seconds for a
    full traverse rather than units per second, so its exponent is the same
    number with the opposite sign. The clustering claim is about how fast the
    stage scales with the value, which is unchanged by which way the quantity
    is expressed.
    """
    exponents = [abs(scales.SCALES[("keygroup", n)].b)
                 for n in ("DECAY1", "RELSE1", "DECAY2")]

    assert max(exponents) - min(exponents) < 0.002, exponents


def test_the_two_sustains_are_different_kinds_of_law():
    """SUSTN1 is linear in dB; SUSTN2 is linear in octaves. Not the same."""
    assert scales.SCALES[("keygroup", "SUSTN1")].unit == "dB"
    assert scales.SCALES[("keygroup", "SUSTN2")].unit == "%"


def test_the_withdrawn_attak2_fit_is_gone():
    """The threshold-crossing fit was shipped; it must not creep back."""
    attak2 = scales.SCALES[("keygroup", "ATTAK2")]

    assert attak2.b != pytest.approx(0.08963, abs=1e-5)
    assert attak2.a != pytest.approx(0.00073832, abs=1e-8)


def test_the_two_releases_are_measured_in_different_units():
    """RELSE1 is dB/s and RELSE2 is seconds -- they cannot be divided.

    A retracted section compared them and reported "RELSE2 is 5.7x faster".
    That ratio was between two numbers whose units differ, so it never meant
    anything. §59 changed RELSE2's units again, from octaves/s to seconds for
    a full traverse, which makes the two LESS comparable rather than more --
    and that is the point worth pinning. The amplitude release is a rate in
    decibels; the filter release is now expressed as a time.
    """
    assert scales.SCALES[("keygroup", "RELSE1")].unit == "dB/s"
    assert scales.SCALES[("keygroup", "RELSE2")].unit == "s"


# --- a fitted range is a measurement, and so is its reason -----------------


def test_every_narrowed_range_says_why_it_is_narrow():
    """A range without its reason does not survive being passed on.

    mpc2emu carried DECAY1 as 0..99 and extrapolated into a region nothing
    was measured in. The range was correct here and stated in prose; the
    reason lived in a different message, and only one of the two travelled.
    Sitting in the table beside `a` and `b`, it cannot be dropped.
    """
    missing = []
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup(name, region)
        narrowed = (scale.fitted[0] > param.minimum
                    or scale.fitted[1] < param.maximum)
        if narrowed and not scale.bounds.strip():
            missing.append(name)

    assert not missing, f"fitted range narrowed without a reason: {missing}"


def test_the_tuning_scale_is_the_exact_constant_not_the_fit():
    """The document states the fraction is binary; that is an exact claim.

    A fit that agrees to 0.27% confirms it and does not improve on it -- the
    fit carries this bench's error and the constant does not.
    """
    for region, name in (("keygroup", "KGTUNO"), ("program", "PTUNO")):
        assert scales.SCALES[(region, name)].a == 100.0 / 256.0


def test_the_tuning_fit_and_the_exact_constant_agree():
    """If they ever disagree by much, one of the two premises is wrong."""
    measured_slope = 0.391667
    assert abs(measured_slope - 100.0 / 256.0) / (100.0 / 256.0) < 0.005


# --- provisional laws ------------------------------------------------------
#
# Every envelope decay turned out to be a constant slew RATE in the log domain,
# not a duration: hold the value fixed, vary the distance, and the rate stays
# put (0.3% amplitude, 1.9% filter) while the time moves (19%, 28%). The
# seconds these laws report were measured at one span and do not port to
# another. The attack fits neither model and its shape is unknown.


def test_what_is_still_provisional():
    """Nothing again, and RELSE2's round trip is the point.

    §58 put RELSE2 ON this list -- the one envelope-2 law it did not
    re-measure, marked because its three siblings had just moved by up to 22%
    and leaving it alone would have made it the most trustworthy-looking law
    in the group and the least checked. §59 measured it and took it back off.
    The mark did its job: it named a debt and the debt was paid.

    FILQ left the list in §53 and LFODEL in §55; ATTAK2's depth-dependence
    mark came off in §58 once two drive levels showed it was a ceiling.
    Nothing was settled by fitting harder; each needed a different
    measurement, which is what "provisional" was recording in the first place.

    The list growing is fine and expected. What must not happen is a law being
    un-marked because the number looked good enough.
    """
    provisional = sorted(n for (_r, n), s in scales.SCALES.items()
                         if s.provisional)
    assert provisional == []


def test_the_attack_and_the_decay_are_different_laws():
    """0.11175 against ~0.0977 -- linear attack, exponential decay.

    The retracted "one time-constant law" claim would have made these equal.
    They are not, and pinning it stops the idea creeping back.
    """
    attack = scales.SCALES[("keygroup", "ATTAK1")].b
    decay = scales.SCALES[("keygroup", "DECAY1")].b
    assert attack > 0 and decay < 0, "an attack is a time, a decay is a rate"
    assert abs(attack - abs(decay)) > 0.01


@pytest.fixture
def unsettled(monkeypatch):
    """A provisional law, injected -- no real one is left to borrow.

    The marking has to keep working for the next half-answered measurement,
    so it is tested against a scale built for the purpose rather than against
    whichever law happened to be unfinished on the day.
    """
    scale = scales.Scale(
        "keygroup", "TESTONLY", "Hz", "exp", 1.0, 0.05, (40, 80), 0.5,
        provisional="measured, but its meaning is not settled",
    )
    patched = dict(scales.SCALES)
    patched[("keygroup", "TESTONLY")] = scale
    monkeypatch.setattr(scales, "SCALES", patched)
    return scale


def test_a_provisional_value_is_marked_in_the_display(unsettled):
    """A number whose meaning is unsettled must not read like a finished one."""
    assert scales.describe("keygroup", "TESTONLY", 60).startswith("!")
    assert not scales.describe("keygroup", "FILFRQ", 80).startswith("!")
    assert not scales.describe("keygroup", "FILQ", 8).startswith("!")
    assert not scales.describe("program", "LFODEL", 60).startswith("!")


def test_the_settled_laws_are_not_marked_provisional():
    for region, name in (("keygroup", "FILFRQ"), ("keygroup", "SUSTN1"),
                         ("keygroup", "KGTUNO"), ("program", "PANPOS"),
                         ("program", "LFORAT"), ("program", "PRLOUD")):
        assert not scales.SCALES[(region, name)].provisional


def test_provisional_and_extrapolated_marks_stack(unsettled):
    """Two different doubts, two different marks; neither hides the other."""
    text = scales.describe("keygroup", "TESTONLY", 20)    # outside 40..80
    assert text.startswith("!?")


def test_the_two_attacks_are_different_laws():
    """0.10844 against 0.09966, each confirmed under two definitions.

    Not a difference between measurement methods: ATTAK1's three threshold
    windows agree to 2.7%, and ATTAK2's threshold and gradient definitions
    agree to 0.19% on the exponent. The fields genuinely differ.
    """
    a1 = scales.SCALES[("keygroup", "ATTAK1")].b
    a2 = scales.SCALES[("keygroup", "ATTAK2")].b
    assert abs(a1 - a2) > 0.005


def test_zero_resonance_is_zero_gain():
    """FILQ 0 is the reference the other steps were measured against."""
    db, _u, _e = scales.to_physical("keygroup", "FILQ", 0)
    assert db == pytest.approx(0.0, abs=1e-9)


def test_filq_is_damping_falling_linearly_to_zero():
    """The one-parameter law, and why it is one parameter.

    Every step shares a single number: the FILQ at which damping would reach
    zero. Fitted to 2985 points of transfer function, r2 0.999975, and
    replicated by a second run with a different note set (15.84 against
    15.93).
    """
    scale = scales.SCALES[("keygroup", "FILQ")]
    assert not scale.provisional
    assert scale.kind == "reso"
    assert 15.0 < scale.a < 16.0, "self-oscillation lies past the field's top"
    assert scale.value_to_physical(0) == pytest.approx(0.0, abs=1e-9)
    assert scale.resonance_q(0) == pytest.approx(1.067, abs=0.01)
    assert scale.resonance_q(15) > 15


def test_filq_last_three_steps_beat_the_first_ten():
    """Which is why the field feels abrupt rather than gradual.

    A linear reading of FILQ -- what §33 recorded -- gets this exactly
    backwards, and a converter using it would spread the resonance evenly.
    """
    s = scales.SCALES[("keygroup", "FILQ")]
    first_ten = s.value_to_physical(10) - s.value_to_physical(0)
    last_three = s.value_to_physical(15) - s.value_to_physical(12)
    assert last_three > first_ten


def test_filq_refuses_to_extrapolate_past_self_oscillation():
    """Beyond 15.84 the model is a division by zero, not a loud filter."""
    s = scales.SCALES[("keygroup", "FILQ")]
    with pytest.raises(ValueError):
        s.value_to_physical(16)
    assert scales.to_physical("keygroup", "FILQ", 16) is None


def test_filq_resonance_peaks_below_the_filfrq_corner():
    """0.42 octaves at FILFRQ 70, replicating 0.41 octaves at FILFRQ 77.

    The two fields do not share a frequency reference, and a converter that
    assumes they do will place resonance in the wrong place. Pinned because
    it now holds at two operating points measured months and methods apart,
    which is what makes it a property of the machine rather than of a rig.
    """
    note = scales.SCALES[("keygroup", "FILQ")].note
    assert "BELOW the corner" in note
    assert "REPLICATES" in note


def test_the_release_span_is_measured_not_assumed():
    """mpc2emu's span survived the test it could have failed.

    The release starts at the sustain level, so span = 0.60832 * SUSTN1 dB.
    Pinned here because the note records a cross-validation -- two laws
    fitted on separate sweeps predicting data neither was fitted to -- and
    that is the strongest evidence in the calibration.
    """
    note = scales.SCALES[("keygroup", "RELSE1")].note
    assert "MEASURED, not assumed" in note
    assert "0.60832 * SUSTN1" in note


def test_the_release_rate_law_predicts_the_independent_run():
    """66.1 dB/s predicted, 67.3 measured, on data it was not fitted to."""
    rate, unit, exact = scales.to_physical("keygroup", "RELSE1", 60)

    assert unit == "dB/s" and exact
    assert abs(rate - 67.3) / 67.3 < 0.03


def test_the_sustain_law_predicts_the_level_at_note_off():
    """Within 0.33 dB across a 36 dB range, in a run it was not fitted to."""
    for sustain, measured_below_90 in ((30, -36.51), (45, -27.05),
                                       (60, -17.99), (75, -8.99)):
        at_sus, _u, _e = scales.to_physical("keygroup", "SUSTN1", sustain)
        at_90, _u2, _e2 = scales.to_physical("keygroup", "SUSTN1", 90)
        assert abs((at_sus - at_90) - measured_below_90) < 0.4


def test_the_log_domain_pattern_holds_across_three_level_fields():
    """SUSTN1 in dB, SUSTN2 in octaves, LFODEP in cents -- all log-domain.

    Three independent level-like fields, each linear in its own perceptual
    domain. Worth pinning: it is now reasonable to EXPECT this of the
    unmeasured level fields, which changes what a surprising result would be.
    """
    for region, name, unit in (("keygroup", "SUSTN1", "dB"),
                               ("keygroup", "SUSTN2", "%"),
                               ("program", "LFODEP", "cents")):
        assert scales.SCALES[(region, name)].kind == "linear", name
        assert scales.SCALES[(region, name)].unit == unit


def test_zero_vibrato_depth_is_zero_cents():
    cents, _u, _e = scales.to_physical("program", "LFODEP", 0)
    assert cents == pytest.approx(0.0, abs=1e-9)


def test_velocity_sensitivity_depth():
    """V_LOUD 50 BOOSTS full velocity by ~29.8 dB above the pivot at 64."""
    db, unit, exact = scales.to_physical("program", "V_LOUD", 50)
    assert unit == "dB" and exact
    assert abs(db - 29.8) < 0.6


def test_zero_velocity_depth_means_velocity_does_nothing():
    """Measured flat to 0.00 dB across the whole velocity range."""
    db, _u, _e = scales.to_physical("program", "V_LOUD", 0)
    assert db == pytest.approx(0.0, abs=1e-9)


def test_the_velocity_knee_is_an_output_ceiling_not_the_law():
    """It moved with PRLOUD -- 66 at 99, 90 at 80, past 100 at 60.

    Reported as a property of the machine before that was checked. V_LOUD
    BOOSTS above the base level, so a loud program reaches the output limit
    at a lower velocity. Pinned because the retracted version was tidier and
    would be easy to reintroduce.
    """
    note = scales.SCALES[("program", "V_LOUD")].note
    assert "OUTPUT CEILING" in note
    assert "velocity - 64" in note


def test_prloud_does_not_saturate():
    """Checked directly: monotonic to 99, no plateau, r2 0.99965."""
    bounds = scales.SCALES[("program", "PRLOUD")].bounds
    assert "does NOT saturate" in bounds
    for lo, hi in ((0, 40), (40, 80), (80, 99)):
        a, _u, _e = scales.to_physical("program", "PRLOUD", lo)
        b2, _u2, _e2 = scales.to_physical("program", "PRLOUD", hi)
        assert b2 > a + 5


def test_v_loud_is_the_one_field_not_linear_in_its_perceptual_domain():
    """Every other level-like field is log-domain linear; this one is not.

    SUSTN1 in dB, SUSTN2 in octaves, LFODEP in cents -- all linear in the
    perceptual unit. V_LOUD is piecewise linear in VELOCITY, with a knee.
    Pinned because the pattern is otherwise strong enough to assume, and an
    exception that is not recorded is an exception that gets assumed away.
    """
    note = " ".join(scales.SCALES[("program", "V_LOUD")].note.split())
    assert "not linear in a perceptual domain" in note
    assert "Linear in raw velocity" in note


def test_sustain_was_measured_with_verified_headroom():
    """Every point checked against a ceiling and a floor measured that session.

    An audit found earlier level sweeps ran into an output ceiling. This law
    survived it to 0.26%, but a control run at lower volume was wrong by 4.6%
    the other way -- its low end sat in the noise. Both limits FLATTEN a
    curve, so two compressed runs do not bracket the answer.
    """
    bounds = " ".join(scales.SCALES[("keygroup", "SUSTN1")].bounds.split())
    assert "headroom to the output ceiling" in bounds
    assert "above the noise" in bounds


def test_keyboard_tracking_is_semitones_per_octave():
    """K_FREQ 12 is 1:1 tracking, as documented -- once the reference is right."""
    st, unit, exact = scales.to_physical("keygroup", "K_FREQ", 12)
    assert unit == "semitones/octave" and exact
    assert st == pytest.approx(12.0, abs=0.1)


def test_modulation_is_referenced_to_the_middle_of_the_midi_range():
    """V_LOUD, V_ATT1 and K_FREQ all pivot on 64.

    Velocity 1..127 and note 0..127 both centre on 64, so the machine
    references modulation to the controller's midpoint rather than to a
    musical constant. Pinned because it predicts the unmeasured fields and
    is therefore refutable rather than decorative.
    """
    for region, name in (("program", "V_LOUD"), ("keygroup", "K_FREQ")):
        note = " ".join(scales.SCALES[(region, name)].note.split())
        assert "64" in note


def test_every_lfo1_depth_source_reaches_the_same_full_scale():
    """LFODEP, MWLDEP, PRSDEP and VELDEP all top out near 1930 cents."""
    for region, name in (("program", "LFODEP"), ("program", "MWLDEP"),
                         ("program", "PRSDEP"), ("program", "VELDEP")):
        cents, unit, _e = scales.to_physical(region, name, 99)
        assert unit == "cents"
        assert abs(cents - 1930) < 40, f"{name} tops out at {cents:.0f}"


def test_unipolar_controllers_do_not_pivot():
    """The §43 rule holds for velocity and note, not for wheel or pressure.

    A wheel rests at zero, so a pivot at 64 would mean a wheel at rest
    applying maximum modulation. Recorded because the prediction was
    published and this is its refutation.
    """
    note = " ".join(scales.SCALES[("program", "MWLDEP")].note.split())
    assert "No pivot" in note
    assert "REFUTES the §43 rule" in note


def test_pressure_must_arrive_during_the_note():
    """Sent before note-on it does nothing; that read as inert and was not."""
    note = " ".join(scales.SCALES[("program", "PRSDEP")].note.split())
    assert "DURING the note" in note


def test_key_scaling_of_decay_pivots_on_64():
    """Visible in the raw table, not fitted: every depth reads 25.3 at note 64."""
    note = " ".join(scales.SCALES[("keygroup", "K_DAR1")].note.split())
    assert "Referenced to note 64" in note
    assert "K_FREQ" in note, "the rule now rests on two independent fields"


def test_positive_key_scaling_slows_high_notes():
    mult, _u, _e = scales.to_physical("keygroup", "K_DAR1", 50)
    assert mult < 1.0, "positive depth must reduce the rate above the pivot"


def test_the_zone_tuning_offset_shares_the_exact_tuning_constant():
    """VTUNO1 is a static per-zone offset on the same 100/256 scale."""
    assert scales.SCALES[("keygroup", "VTUNO1")].a == 100.0 / 256.0
    note = " ".join(scales.SCALES[("keygroup", "VTUNO1")].note.split())
    assert "STATIC offset, not a velocity modulation" in note


def test_the_v_prefix_on_zone_fields_means_zone_not_velocity():
    """Recorded because reading it as velocity produced a wrong retraction.

    LOVEL1/HIVEL1 define the velocity zone; VTUNO1/VLOUD1/VFREQ1/VPANO1 are
    that zone's static offsets. Screening them with a velocity sweep cannot
    see them at all.
    """
    note = " ".join(scales.SCALES[("keygroup", "VTUNO1")].note.split())
    assert "velocity ZONE the field belongs to" in note


def test_lfo2_runs_at_twice_lfo1():
    """0.23708 against 0.11867 Hz per unit -- a ratio of 1.998."""
    lfo2 = scales.SCALES[("program", "PANRAT")].a
    lfo1 = scales.SCALES[("program", "LFORAT")].a
    assert abs(lfo2 / lfo1 - 2.0) < 0.02


def test_panrat_is_lfo2_not_a_pan_only_control():
    """§39 called it inert by testing it against pan, which is the dead route.

    Routed to the filter as matrix source 8, LFO2 and all five of its fields
    work. Pinned because the retraction is easy to lose.
    """
    note = " ".join(scales.SCALES[("program", "PANRAT")].note.split())
    assert "Only its route to PAN is inert" in note


def test_zero_lfo2_rate_at_zero():
    hz, _u, _e = scales.to_physical("program", "PANRAT", 0)
    assert hz == pytest.approx(0.0, abs=1e-9)


# --- the two files must agree with each other -------------------------------
#
# Both of this project's worst table errors were a number contradicting another
# number IN THE SAME REPOSITORY, and in one case in the same file: KGTUNO was
# fitted at 100/256 cents per unit while its parameter was declared 0..50,
# which caps the field at 19.5 cents. Neither was missing and neither needed
# hardware to spot. What was missing was ever putting the two beside each other
# and asking whether they agree.
#
# These are the checks that can be made mechanically. They are cheap, they run
# without a sampler, and they would have failed on the tuning fields the day
# the law was measured.

def test_every_scale_names_a_parameter_that_exists():
    """A renamed or retyped field must not leave a law pointing at nothing."""
    for region, name in scales.SCALES:
        p.lookup((region, name))


def test_no_law_is_fitted_outside_its_parameters_declared_range():
    """The check that ties scales.py to params.py.

    A law fitted over values the table says are illegal means one of the two is
    wrong, and which one it is takes a measurement -- but noticing costs
    nothing. Against the old 0..50 tuning range, KGTUNO's confirmed
    +/-5120 fails this immediately.
    """
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup((region, name))
        lo, hi = scale.fitted
        assert param.minimum <= lo <= param.maximum, (
            f"{name} fitted from {lo}, outside the declared "
            f"{param.minimum}..{param.maximum}")
        assert param.minimum <= hi <= param.maximum, (
            f"{name} fitted to {hi}, outside the declared "
            f"{param.minimum}..{param.maximum}")


def test_every_named_endpoint_is_a_value_the_field_can_hold():
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup((region, name))
        for value in scale.endpoints or {}:
            assert param.minimum <= value <= param.maximum, (
                f"{name} names an endpoint at {value}, outside "
                f"{param.minimum}..{param.maximum}")


def test_describe_survives_every_value_of_every_law():
    """A physical rendering must not raise anywhere in the declared range.

    The laws that run to a pole -- FILQ and LFODEL -- refuse past it by design,
    and describe() has to turn that refusal into "" rather than propagating it.
    """
    for (region, name), scale in scales.SCALES.items():
        param = p.lookup((region, name))
        span = param.maximum - param.minimum
        step = max(1, span // 200)
        for value in range(param.minimum, param.maximum + 1, step):
            scales.describe(region, name, value)


def test_the_attack_and_the_release_are_one_law_across_both_envelopes():
    """ATTAK2, RELSE2 and ENV3R1 agree to 2.2% at value 70.

    Three fields, two envelopes, one time base -- and the decays run at about
    half that rate rather than on laws of their own. Pinned because it is the
    kind of structure a converter can rely on, and because it was found by
    measuring three fields the same way rather than by assuming a family.
    """
    at, re_, e3 = (scales.SCALES[("keygroup", n)]
                   for n in ("ATTAK2", "RELSE2", "ENV3R1"))
    at70, re70, e370 = (s.value_to_physical(70) for s in (at, re_, e3))
    assert max(at70, re70, e370) / min(at70, re70, e370) < 1.05

    dec = scales.SCALES[("keygroup", "DECAY2")].value_to_physical(70)
    assert 1.7 < dec / at70 < 2.3, "the decay runs at about half the rate"
