# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""What makes the measurement code trustworthy with no sampler in the room.

Every test here synthesises a signal whose answer is known exactly -- an
attack that really is 200 ms, a spectrum whose corner really is 2 kHz, a
tremolo that really is 3 Hz -- and asserts the measurement recovers it. That
is a different kind of confidence from the rest of this project's tests: the
parameter tables can only be checked for internal consistency because the
truth lives in a document, but a filter corner has a *right answer* that can
be constructed.

So when a bench session reports "FILFRQ 40 gives 1.8 kHz", the reason to
believe the 1.8 is here, not on the bench.

Each block also carries its negative control -- the case that must NOT
produce a number -- because a measurement function that always returns
something plausible is the one that quietly wrecks a calibration.
"""

import math

import pytest

np = pytest.importorskip("numpy", reason="s3k.measure is bench tooling")

from s3k import measure as ms


SR = 44100


def _ramp_note(attack_s, hold_s, sr=SR, sustain=1.0):
    """A note that rises linearly over `attack_s`, then holds."""
    n_a, n_h = int(attack_s * sr), int(hold_s * sr)
    env = np.concatenate([np.linspace(0, sustain, n_a),
                          np.full(n_h, sustain)])
    carrier = np.sin(2 * np.pi * 220 * np.arange(len(env)) / sr)
    return env * carrier


# --- envelope ---------------------------------------------------------------


def test_envelope_of_a_steady_sine_is_its_rms():
    a = 0.5 * np.sin(2 * np.pi * 440 * np.arange(SR) / SR)
    env, t = ms.envelope(a, SR)
    assert env.mean() == pytest.approx(0.5 / math.sqrt(2), rel=0.02)
    assert t[1] - t[0] == pytest.approx(ms.DEFAULT_HOP)


def test_attack_time_recovers_a_known_ramp():
    """t90 of a linear 200 ms ramp is 180 ms, and that is what must come back."""
    a = _ramp_note(0.200, 1.0)
    env, _t = ms.envelope(a, SR)
    assert ms.attack_time(env, 0.0, 1.2) == pytest.approx(0.180, abs=0.015)


def test_attack_time_is_not_fooled_by_a_single_overshoot_sample():
    """The plateau is a percentile, not the max.

    One sample of overshoot at the start would otherwise set the target and
    the attack would measure as instantaneous -- the failure mode that makes a
    whole envelope sweep read as flat.
    """
    a = _ramp_note(0.200, 1.0)
    a[10] = 4.0
    env, _t = ms.envelope(a, SR)
    assert ms.attack_time(env, 0.0, 1.2) == pytest.approx(0.180, abs=0.02)


def test_release_time_recovers_an_exponential_decay():
    """-40 dB of an e^(-t/tau) decay lands at tau*ln(100)."""
    tau = 0.1
    hold = np.ones(int(0.5 * SR))
    n = int(3.0 * SR)
    tail = np.exp(-np.arange(n) / (tau * SR))
    env_shape = np.concatenate([hold, tail])
    a = env_shape * np.sin(2 * np.pi * 220 * np.arange(len(env_shape)) / SR)
    env, _t = ms.envelope(a, SR)
    assert ms.release_time(env, 0.0, 0.5) == pytest.approx(tau * math.log(100),
                                                           abs=0.03)


def test_release_time_reports_nan_rather_than_a_floor_value():
    """The negative control. A note that never decays has no release time, and
    saying so is right; returning the window length would look like data."""
    a = np.sin(2 * np.pi * 220 * np.arange(int(4 * SR)) / SR)
    env, _t = ms.envelope(a, SR)
    assert math.isnan(ms.release_time(env, 0.0, 0.5, window_s=2.0))


def test_decay_time_recovers_the_1_over_e_point():
    tau, sus = 0.30, 0.5
    n = int(3.0 * SR)
    shape = sus + (1.0 - sus) * np.exp(-np.arange(n) / (tau * SR))
    a = shape * np.sin(2 * np.pi * 220 * np.arange(n) / SR)
    env, _t = ms.envelope(a, SR)
    assert ms.decay_time(env, 0.0, 3.0) == pytest.approx(tau, abs=0.05)


def test_decay_time_is_nan_when_sustain_equals_peak():
    """With SUSTN at maximum there is no decay phase to time. The procedure
    sets sustain to about half precisely because of this."""
    a = _ramp_note(0.01, 3.0)
    env, _t = ms.envelope(a, SR)
    assert math.isnan(ms.decay_time(env, 0.0, 3.0))


def test_sustain_level_is_a_fraction_of_peak():
    n = int(2.0 * SR)
    shape = np.concatenate([np.linspace(0, 1.0, int(0.05 * SR)),
                            np.full(n, 0.4)])
    a = shape * np.sin(2 * np.pi * 220 * np.arange(len(shape)) / SR)
    env, _t = ms.envelope(a, SR)
    assert ms.sustain_level(env, 0.0, 2.0) == pytest.approx(0.4, abs=0.05)


def test_anchor_offset_finds_a_late_first_note():
    """The recorder starts first; the offset is what reconciles the clocks."""
    lead = np.zeros(int(1.5 * SR))
    a = np.concatenate([lead, _ramp_note(0.01, 1.0)])
    env, t = ms.envelope(a, SR)
    assert ms.anchor_offset(env, t, first_note_on=0.0) == pytest.approx(1.5, abs=0.02)


# --- level ------------------------------------------------------------------


def test_peak_and_rms_of_a_known_sine():
    a = 0.25 * np.sin(2 * np.pi * 440 * np.arange(SR) / SR)
    assert ms.peak_db(a) == pytest.approx(20 * math.log10(0.25), abs=0.05)
    assert ms.rms_db(a) == pytest.approx(20 * math.log10(0.25 / math.sqrt(2)),
                                         abs=0.05)


@pytest.mark.parametrize("ratio_db", [-12.0, -6.0, 0.0, 6.0, 12.0])
def test_balance_db_recovers_a_known_left_right_ratio(ratio_db):
    n = int(0.5 * SR)
    car = np.sin(2 * np.pi * 220 * np.arange(n) / SR)
    right = car * (10 ** (ratio_db / 20.0))
    assert ms.balance_db(np.stack([car, right], axis=1)) == pytest.approx(
        ratio_db, abs=0.05)


def test_balance_db_refuses_a_mono_recording():
    """Pan cannot be measured from a mono sum, and guessing would produce a
    curve that looks like a pan law and is not one."""
    with pytest.raises(ValueError):
        ms.balance_db(np.zeros(1000))


# --- spectrum ---------------------------------------------------------------


def _lowpass_noise(corner_hz, poles=4, seconds=4.0, sr=SR, seed=7):
    """White noise shaped by an ideal N-pole low-pass, applied in the frequency
    domain so the -3 dB point is exactly where we say it is."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * sr))
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    with np.errstate(divide="ignore"):
        mag = 1.0 / np.sqrt(1.0 + (f / corner_hz) ** (2 * poles))
    return np.fft.irfft(spec * mag, n=len(x)) * 0.1


@pytest.mark.parametrize("corner", [800.0, 2000.0, 5000.0])
def test_corner_frequency_recovers_a_known_lowpass(corner):
    """The measurement the whole FILFRQ curve rests on."""
    a = _lowpass_noise(corner)
    f, mag = ms.spectrum(a, SR, skip_s=0.0)
    got = ms.corner_frequency(f, mag, ref_lo=100.0, ref_hi=min(500.0, corner / 2))
    assert got == pytest.approx(corner, rel=0.15)


def test_corner_frequency_divides_out_the_source_spectrum():
    """A real sample is not white, so the reference take is what makes it
    usable: measure once wide open, then divide every later take by it."""
    tilt = _lowpass_noise(12000.0)                    # stands in for "wide open"
    shaped = _lowpass_noise(2000.0)
    f_ref, m_ref = ms.spectrum(tilt, SR, skip_s=0.0)
    f, m = ms.spectrum(shaped, SR, skip_s=0.0)
    got = ms.corner_frequency(f, m, reference=m_ref)
    assert got == pytest.approx(2000.0, rel=0.2)


def test_corner_frequency_refuses_when_the_corner_is_below_the_reference_band():
    """The floor artefact, caught the first time the dry-run sweep was read.

    With the corner below ref_hi the reference level is taken inside the
    stopband, the first bin past ref_hi is already 3 dB down, and the function
    returns a value just above ref_hi -- for every setting. A synthetic sweep
    reported "500.6 Hz" for seventeen consecutive filter settings before this
    guard existed, which is worse than no reading at all: a flat run of
    identical numbers looks like a filter that stops moving.
    """
    a = _lowpass_noise(200.0)
    f, mag = ms.spectrum(a, SR, skip_s=0.0)
    assert math.isnan(ms.corner_frequency(f, mag, ref_lo=100.0, ref_hi=500.0))
    # …and it is findable once the band is moved below the corner.
    assert ms.corner_frequency(f, mag, ref_lo=40.0, ref_hi=100.0) == pytest.approx(
        200.0, rel=0.25)


def test_corner_frequency_is_nan_for_a_flat_spectrum():
    """The negative control: nothing rolls off, so there is no corner. A
    number here would mean the search latches onto noise scatter."""
    rng = np.random.default_rng(3)
    f, mag = ms.spectrum(rng.standard_normal(int(4 * SR)) * 0.1, SR, skip_s=0.0)
    assert math.isnan(ms.corner_frequency(f, mag))


# --- pitch and modulation ---------------------------------------------------


@pytest.mark.parametrize("hz", [110.0, 220.0, 440.0])
def test_fundamental_hz_recovers_a_sine(hz):
    a = np.sin(2 * np.pi * hz * np.arange(int(0.5 * SR)) / SR)
    assert ms.fundamental_hz(a, SR) == pytest.approx(hz, rel=0.01)


def test_cents_between_an_octave_is_1200():
    assert ms.cents_between(880.0, 440.0) == pytest.approx(1200.0)
    assert ms.cents_between(440.0, 440.0) == pytest.approx(0.0)


@pytest.mark.parametrize("rate", [0.5, 3.0, 7.0])
def test_modulation_rate_recovers_a_tremolo(rate):
    """LFORAT is documented as "speed" with no unit. This is how it gets one."""
    n = int(8.0 * SR)
    t = np.arange(n) / SR
    a = (1 + 0.5 * np.sin(2 * np.pi * rate * t)) * np.sin(2 * np.pi * 220 * t)
    env, _ = ms.envelope(a, SR)
    assert ms.modulation_rate_hz(env) == pytest.approx(rate, rel=0.1)


# --- fitting ----------------------------------------------------------------


def test_fit_exponential_recovers_its_own_coefficients():
    x = np.linspace(0, 1, 20)
    y = 0.002 * np.exp(9.0 * x)
    a, b, r2 = ms.fit_exponential(x, y)
    assert a == pytest.approx(0.002, rel=0.01)
    assert b == pytest.approx(9.0, rel=0.01)
    assert r2 > 0.999


def test_fit_exponential_reports_a_poor_fit_as_a_poor_fit():
    """r2 is not decoration. A machine using a lookup table with a kink will
    fit badly, and that is a finding -- but only if the number is believed."""
    x = np.linspace(0, 1, 20)
    y = np.concatenate([np.full(10, 0.01), np.full(10, 1.0)])
    _a, _b, r2 = ms.fit_exponential(x, y)
    assert r2 < 0.9


def test_fit_exponential_needs_three_usable_points():
    a, b, r2 = ms.fit_exponential([0, 1], [1.0, 2.0])
    assert math.isnan(a) and math.isnan(b) and math.isnan(r2)


# --- io ---------------------------------------------------------------------


def test_read_wav_round_trip(tmp_path):
    import wave
    path = tmp_path / "x.wav"
    frames = (np.stack([np.full(100, 0.5), np.full(100, -0.5)], axis=1)
              * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames.tobytes())

    stereo, sr = ms.read_wav(str(path), mono=False)
    assert sr == SR and stereo.shape == (100, 2)
    assert stereo[:, 0].mean() == pytest.approx(0.5, abs=0.001)

    mono, _ = ms.read_wav(str(path))
    assert mono.mean() == pytest.approx(0.0, abs=0.001)


# --- pitch resolution: the estimator must out-resolve what it measures -------


def _tone(hz, sr=48000, seconds=1.2):
    import numpy as np
    t = np.arange(int(sr * seconds)) / sr
    # A few harmonics, like a real sampled waveform rather than a pure sine.
    return sum(np.sin(2 * np.pi * hz * k * t) / k for k in (1, 2, 3)), sr


def test_fundamental_resolves_finer_than_one_lag():
    """Integer lag quantises to ~9.4 cents at note 60 -- coarser than the
    parameter it exists to measure. RESOLUTION_NOTES §21."""
    import math

    base = 261.6
    a, sr = _tone(base)
    got = ms.fundamental_hz(a, sr)
    assert abs(1200 * math.log2(got / base)) < 2.0, f"{got} vs {base}"

    # Two tones a couple of cents apart must come back distinguishable, which
    # an integer-lag estimator cannot do at this pitch.
    shifted = base * 2 ** (5 / 1200)
    b, _sr = _tone(shifted)
    got_b = ms.fundamental_hz(b, sr)
    assert got_b > got, f"{got_b} not above {got}"
    assert abs(1200 * math.log2(got_b / got) - 5) < 3.0


def test_fundamental_is_accurate_across_the_range():
    import math

    for hz in (32.7, 65.4, 130.8, 261.6, 523.2):
        a, sr = _tone(hz)
        got = ms.fundamental_hz(a, sr)
        cents = abs(1200 * math.log2(got / hz))
        assert cents < 3.0, f"{hz} Hz -> {got} Hz ({cents:.1f} cents off)"


def test_a_fundamental_below_the_search_floor_is_not_silently_wrong():
    """A pitch under `lo` used to come back as the top of the range.

    40 Hz was the old default and 32.7 Hz is what note 24 sounds at, so the
    filter sweep's own test note fell straight through it and would have
    reported 2000 Hz. RESOLUTION_NOTES §21.
    """
    import math

    a, sr = _tone(32.7)
    got = ms.fundamental_hz(a, sr)
    assert abs(1200 * math.log2(got / 32.7)) < 5.0, f"got {got} for 32.7 Hz"


def test_a_harmonic_rich_tone_does_not_report_an_octave_low():
    """Autocorrelation is nearly as strong at twice the true period.

    A real sawtooth put note 60 at 130.8 Hz instead of 261.6 -- an octave out,
    and invisible against synthetic tones whose harmonics are too clean to make
    the sub-multiple competitive. RESOLUTION_NOTES §23.
    """
    import math
    import numpy as np

    for hz in (65.4, 130.8, 261.6):
        sr = 48000
        t = np.arange(int(sr * 1.2)) / sr
        saw = sum(np.sin(2 * np.pi * hz * k * t) / k for k in range(1, 41))
        got = ms.fundamental_hz(saw, sr)
        cents = 1200 * math.log2(got / hz)
        assert abs(cents) < 10, f"{hz} Hz -> {got} Hz ({cents:+.0f} cents)"
        assert got > hz * 0.9, f"{hz} Hz reported an octave low as {got}"
