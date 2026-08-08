# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The measurement primitives here -- RMS envelope, MIDI-clock anchoring,
# attack/release timing, fractional-octave smoothing and the -3 dB corner
# search -- are ported from the sibling mpc2emu project's
# tests/re_banks/hw_measure.py, GPL-2.0-or-later, where they were developed
# and debugged against a real E-MU E4XT and an Akai-generation MPC. The
# comments that explain WHY each one is shaped the way it is were paid for in
# bench time; they are kept.
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

"""Turn a recording of the sampler into a number.

s3ked can *set* ``FILFRQ`` to 63. Nothing in the project knows what 63
**is** -- the specification says "basic filter frequency, 0 to 99" and stops
there. The same is true of every rate, level and depth on the machine: the
parameter tables carry the range and not the meaning.

This module is the analysis half of closing that gap. It takes audio the
sampler produced and returns a physical quantity -- hertz, seconds, decibels
-- so that a sweep over a parameter yields a calibration curve. The driving
half is ``probes/calibrate.py``; the procedure and the order to do it in are
in ``docs/re_procedures/calibration.md``.

**Everything here is pure.** Functions take arrays and return numbers; none
of them opens a MIDI port, a JACK client or a file except :func:`read_wav`.
That is deliberate: it means every one of them can be tested against a
synthesised signal whose answer is known in advance, which is how this module
came to be trusted with no hardware in the building. See
``tests/test_measure.py`` -- a 200 ms synthetic attack must measure as 200 ms,
and a signal filtered at a known corner must report that corner.

NumPy is required to use this module and is *not* a project dependency: the
editor never calls it. Install it in the venv when you get to the bench.
"""

from __future__ import annotations

import math
import wave
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "read_wav",
    "envelope",
    "anchor_offset",
    "attack_time",
    "release_time",
    "decay_time",
    "sustain_level",
    "peak_db",
    "rms_db",
    "spectrum",
    "corner_frequency",
    "balance_db",
    "modulation_rate_hz",
    "fundamental_hz",
    "cents_between",
    "fit_exponential",
]

#: Envelope hop, in seconds. 5 ms resolves a 20 ms attack into four points,
#: which is the shortest thing worth claiming a number for on this machine.
DEFAULT_HOP = 0.005


def _np():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "s3k.measure needs numpy, which is not a project dependency "
            "because the editor never uses it. Install it in the venv: "
            "`.venv/bin/pip install numpy`."
        ) from exc
    return np


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

def read_wav(path: str, mono: bool = True):
    """Read a 16-bit WAV. Returns ``(samples, sample_rate)``.

    With ``mono`` the channels are averaged; without, the result is shaped
    ``(frames, channels)`` -- which is what :func:`balance_db` needs, and the
    reason pan calibration must not be recorded through a mono sum.
    """
    np = _np()
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        if w.getsampwidth() != 2:
            raise ValueError(
                f"{path}: {w.getsampwidth() * 8}-bit WAV; this reader handles 16-bit"
            )
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype("float64") / 32768.0
    a = a.reshape(-1, ch)
    return (a.mean(axis=1) if mono else a), sr


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------

def envelope(samples, sr: int, hop: float = DEFAULT_HOP):
    """RMS envelope and its time axis, in seconds."""
    np = _np()
    a = np.asarray(samples, dtype="float64")
    if a.ndim > 1:
        a = a.mean(axis=1)
    win = max(1, int(hop * sr))
    n = len(a) // win
    if n == 0:
        return np.zeros(0), np.zeros(0)
    env = np.sqrt((a[: n * win].reshape(n, win) ** 2).mean(axis=1))
    return env, np.arange(n) * hop


def anchor_offset(env, t, first_note_on: float, hop: float = DEFAULT_HOP,
                  search_s: float = 5.0, frac: float = 0.05) -> float:
    """Map the MIDI clock onto the audio clock, using the first note's onset.

    The recorder starts before the first note and the two clocks are not the
    same clock. Add the returned offset to every scheduled time.

    **Segment on the MIDI clock, never on silence detection.** A slow attack
    starts too quietly for a silence gate to find its true onset, so gating
    reports an onset late by however slow the attack is -- which is precisely
    the quantity an envelope sweep is trying to measure. The one place a level
    threshold is allowed is here, on the *first* note of a run, which the
    procedure requires to be a fast one.
    """
    np = _np()
    head = env[: int(search_s / hop)]
    if len(head) == 0 or head.max() <= 0:
        return 0.0
    return float(t[int(np.argmax(head > head.max() * frac))] - first_note_on)


def attack_time(env, on: float, off: float, frac: float = 0.90,
                hop: float = DEFAULT_HOP) -> float:
    """Seconds from onset to ``frac`` of the note's own plateau.

    The plateau is the 98th percentile rather than the maximum: a single
    sample of overshoot at the very start of a fast attack would otherwise set
    the target and the answer would come back near zero.
    """
    np = _np()
    seg = env[int(on / hop): int(off / hop)]
    if len(seg) < 10:
        return float("nan")
    pk = float(np.percentile(seg, 98))
    if pk <= 0:
        return float("nan")
    base = int(np.argmax(seg > pk * 0.02))
    return float((int(np.argmax(seg > pk * frac)) - base) * hop)


def _first_persistent(mask, hold: int) -> int:
    """Index of the first run of ``hold`` consecutive True values, or -1.

    The RMS envelope is not smooth: a 5 ms window holds a whole number of
    cycles only by accident, so a steady tone's envelope jitters by a couple
    of percent frame to frame. A bare "first frame below the threshold" search
    latches onto one of those dips -- measured on a synthetic decay with a
    known 300 ms time constant, it answered 245 ms. Requiring the crossing to
    persist costs one frame of resolution and removes that whole error class.
    The same reasoning shapes the run-length test in :func:`corner_frequency`.
    """
    run = 0
    for i, ok in enumerate(mask):
        run = run + 1 if ok else 0
        if run >= hold:
            return i - run + 1
    return -1


def release_time(env, on: float, off: float, drop_db: float = 40.0,
                 hop: float = DEFAULT_HOP, window_s: float = 12.0,
                 hold: int = 3) -> float:
    """Seconds from note-off until the level has fallen ``drop_db``.

    Measured against the median of the sustained part, not the peak, so a
    percussive attack does not inflate it.  Returns NaN when the note never
    falls that far inside ``window_s`` -- which is a *finding*, not a failure:
    it means the release is longer than the window and the window needs
    widening before that point can be claimed.
    """
    np = _np()
    i0 = int(off / hop)
    seg = env[i0: i0 + int(window_s / hop)]
    held = env[int(on / hop) + 40: i0]
    if len(seg) < 10 or len(held) == 0:
        return float("nan")
    lvl = float(np.percentile(held, 50))
    target = lvl * (10 ** (-drop_db / 20.0))
    if lvl <= 0:
        return float("nan")
    i = _first_persistent(seg < target, hold)
    return float(i * hop) if i >= 0 else float("nan")


def decay_time(env, on: float, off: float, hop: float = DEFAULT_HOP,
               hold: int = 3) -> float:
    """Seconds from the attack peak down to the sustain plateau.

    ``DECAY1``/``DECAY2`` only produce a measurable segment when the sustain
    level is *below* the peak; with ``SUSTN`` at maximum there is no decay
    phase to time and this returns NaN. The procedure sets sustain to about
    half for exactly this reason.
    """
    np = _np()
    i0, i1 = int(on / hop), int(off / hop)
    seg = env[i0:i1]
    if len(seg) < 20:
        return float("nan")
    peak_i = int(np.argmax(seg))
    tail = seg[max(peak_i, len(seg) // 2):]
    sus = float(np.percentile(tail, 50))
    # The peak is a percentile of the frames around it rather than the single
    # loudest frame: envelope jitter (see _first_persistent) inflates a bare
    # max by a couple of percent, which biases the 1/e target low.
    pk = float(np.percentile(seg[peak_i:peak_i + 4], 50)) if peak_i + 4 <= len(seg) \
        else float(seg[peak_i])
    if pk <= 0 or sus >= pk * 0.95:
        return float("nan")          # no decay phase to measure
    target = sus + (pk - sus) * math.exp(-1.0)      # the 1/e point
    i = _first_persistent(seg[peak_i:] <= target, hold)
    return float(i * hop) if i >= 0 else float("nan")


def sustain_level(env, on: float, off: float, hop: float = DEFAULT_HOP) -> float:
    """Sustain plateau as a fraction of the note's peak."""
    np = _np()
    seg = env[int(on / hop): int(off / hop)]
    if len(seg) < 10:
        return float("nan")
    pk = float(seg.max())
    if pk <= 0:
        return float("nan")
    return float(np.percentile(seg[len(seg) // 2:], 50) / pk)


# --------------------------------------------------------------------------
# Level
# --------------------------------------------------------------------------

def peak_db(samples, ref: float = 1.0) -> float:
    """Peak level in dB relative to ``ref`` (default full scale)."""
    np = _np()
    a = np.abs(np.asarray(samples, dtype="float64"))
    pk = float(a.max()) if a.size else 0.0
    return 20 * math.log10(max(pk, 1e-12) / ref)


def rms_db(samples, ref: float = 1.0) -> float:
    """RMS level in dB relative to ``ref``.

    Prefer this to :func:`peak_db` for level calibration: a peak is one sample
    and moves with the sample's own transient, while an RMS over the sustained
    part is what "loudness" means on a curve.
    """
    np = _np()
    a = np.asarray(samples, dtype="float64")
    if a.size == 0:
        return float("-inf")
    return 20 * math.log10(max(float(np.sqrt((a ** 2).mean())), 1e-12) / ref)


def balance_db(stereo) -> float:
    """Right-minus-left level difference in dB.

    Positive means the sound sits right. This is the measurement that settles
    a pan law: ``PANPOS`` runs -50..+50 and the specification does not say
    whether that is linear in amplitude, constant-power, or a table.

    **Requires a two-channel recording.** A mono sum makes every pan position
    look identical apart from a constant-power dip in the middle, which is the
    kind of result that looks like data and is not.
    """
    np = _np()
    a = np.asarray(stereo, dtype="float64")
    if a.ndim != 2 or a.shape[1] != 2:
        raise ValueError("balance_db needs a (frames, 2) stereo array")
    l = math.sqrt(float((a[:, 0] ** 2).mean()))
    r = math.sqrt(float((a[:, 1] ** 2).mean()))
    return 20 * math.log10(max(r, 1e-12) / max(l, 1e-12))


# --------------------------------------------------------------------------
# Spectrum
# --------------------------------------------------------------------------

def spectrum(samples, sr: int, skip_s: float = 0.25, n_fft: int = 1 << 14):
    """Average magnitude spectrum of a steady segment. Returns ``(freqs, mag)``.

    ``skip_s`` drops the attack transient, which would otherwise smear the
    result across the whole band.
    """
    np = _np()
    a = np.asarray(samples, dtype="float64")
    if a.ndim > 1:
        a = a.mean(axis=1)
    a = a[int(skip_s * sr):]
    if len(a) < n_fft:
        return np.zeros(0), np.zeros(0)
    win = np.hanning(n_fft)
    frames = len(a) // n_fft
    acc = np.zeros(n_fft // 2 + 1)
    for k in range(frames):
        acc += np.abs(np.fft.rfft(a[k * n_fft:(k + 1) * n_fft] * win))
    return np.fft.rfftfreq(n_fft, 1 / sr), acc / max(frames, 1)


def _smooth_log(freqs, mag, frac: float = 1 / 6):
    """Fractional-octave smoothing: each bin averaged over a band around it.

    Keeps a filter knee sharp while flattening noise scatter. Not cosmetic:
    an unsmoothed -3 dB search latches onto the first bin that happens to dip,
    and on real measured noise that reported a 545 Hz corner for a spectrum
    only 1.7 dB down at 10 kHz.
    """
    np = _np()
    m = np.asarray(mag, dtype="float64")
    out = np.full(len(m), np.nan)
    lo = np.searchsorted(freqs, freqs / (2 ** (frac / 2)), "left")
    hi = np.searchsorted(freqs, freqs * (2 ** (frac / 2)), "right")
    finite = np.isfinite(m)
    csum = np.concatenate([[0.0], np.cumsum(np.where(finite, m, 0.0))])
    ccnt = np.concatenate([[0], np.cumsum(finite.astype("int64"))])
    n = ccnt[hi] - ccnt[lo]
    ok = n > 0
    out[ok] = (csum[hi] - csum[lo])[ok] / n[ok]
    return out


def corner_frequency(freqs, mag, drop_db: float = 3.0, ref_lo: float = 100.0,
                     ref_hi: float = 500.0, reference=None,
                     smooth: float = 1 / 6, ref_flat_db: float = 3.0) -> float:
    """The -``drop_db`` point of a low-pass, in hertz.

    This is what turns ``FILFRQ`` from a number into a frequency.

    Source material, best to worst: **noise** (flat and continuous, so the
    filter is the only shape present), **saw** (every harmonic, no gaps),
    square (odd harmonics only -- the search can land in an even-harmonic gap
    and report a corner that is not there), sine (useless: one frequency says
    nothing about where a filter turns over).

    The S3000XL has no oscillator, so the source is whatever sample is in
    memory. Get a noise sample onto the machine before measuring -- see the
    procedure doc; that is a disk-image job, not a SysEx one.

    ``reference`` is the spectrum of the same source with the filter wide
    open. Pass it and the source's own spectral shape divides out, which is
    what makes a real sample usable instead of only synthetic noise. Take it
    once at ``FILFRQ 99`` and reuse it for the whole sweep.

    **The reference band must lie in the passband, and the guard for that is
    load-bearing.** ``ref_lo``..``ref_hi`` is where the 0 dB reference level
    is taken. If the corner has moved *below* that band, the band is itself in
    the stopband: the level taken there is already attenuated, the first bin
    past ``ref_hi`` is already 3 dB below it, and the function returns a
    number just above ``ref_hi`` -- for every setting, so a whole sweep floors
    at one value and looks like a filter that stops moving. That is exactly
    what a first synthetic sweep here did: 17 consecutive points all reported
    "500.6 Hz". ``ref_flat_db`` catches it by checking the band is actually
    flat, and returns NaN instead, which is a signal to lower the band (or to
    accept that the source has no energy far enough down to measure there).

    Give the reference band about an octave. Fractional-octave smoothing
    averages very few bins down at 40 Hz, so a narrow low band stays noisy
    -- on measured noise a genuinely flat 40-100 Hz band still showed 1.7 dB
    of apparent tilt, which is why the flatness tolerance is half the drop
    being searched for rather than something tighter.
    """
    np = _np()
    if len(freqs) == 0:
        return float("nan")
    curve = np.asarray(mag, dtype="float64")
    if reference is not None:
        ref_curve = np.asarray(reference, dtype="float64")
        if len(ref_curve) == len(curve):
            # Only trust bins the reference actually excited; a harmonic gap
            # divides noise by noise and produces garbage either way.
            floor = ref_curve.max() * 1e-4
            curve = np.where(ref_curve > floor,
                             curve / np.maximum(ref_curve, 1e-12), np.nan)
    if smooth:
        curve = _smooth_log(freqs, curve, frac=smooth)

    band = (freqs >= ref_lo) & (freqs <= ref_hi)
    ref = float(np.nanmean(curve[band])) if band.any() else float("nan")
    if not math.isfinite(ref) or ref <= 0:
        return float("nan")

    # Is the reference band actually in the passband? Compare its lower and
    # upper halves; a passband is flat across them, a stopband is not.
    mid = (ref_lo + ref_hi) / 2
    lo_half = curve[(freqs >= ref_lo) & (freqs < mid)]
    hi_half = curve[(freqs >= mid) & (freqs <= ref_hi)]
    if lo_half.size and hi_half.size:
        lo_m, hi_m = float(np.nanmean(lo_half)), float(np.nanmean(hi_half))
        if lo_m > 0 and hi_m > 0:
            tilt = 20 * math.log10(hi_m / lo_m)
            if tilt < -ref_flat_db:
                return float("nan")     # corner is below the band: see docstring

    db = 20 * np.log10(np.maximum(curve, 1e-12) / ref)

    # Require the drop to PERSIST. A real corner stays down; a noise dip does
    # not. `hold` bins is about a sixth of an octave at the top of the range.
    below = (freqs > ref_hi) & (db <= -drop_db)
    hold = max(3, int(len(freqs) * 0.002))
    run = 0
    for i in np.where(freqs > ref_hi)[0]:
        run = run + 1 if below[i] else 0
        if run >= hold:
            return float(freqs[i - run + 1])
    return float("nan")


def fundamental_hz(samples, sr: int, lo: float = 40.0, hi: float = 2000.0) -> float:
    """Fundamental frequency by autocorrelation, for tuning calibration.

    ``PTUNO``/``KGTUNO``/``STUNO`` are documented as "cent:semi" pairs, and
    what a unit of each is worth on the wire is exactly the kind of claim that
    should be measured rather than assumed.
    """
    np = _np()
    a = np.asarray(samples, dtype="float64")
    if a.ndim > 1:
        a = a.mean(axis=1)
    a = a - a.mean()
    if len(a) < int(sr / lo) * 2:
        return float("nan")
    corr = np.correlate(a, a, mode="full")[len(a) - 1:]
    lo_lag, hi_lag = int(sr / hi), int(sr / lo)
    if hi_lag >= len(corr):
        return float("nan")
    seg = corr[lo_lag:hi_lag]
    if not seg.size or seg.max() <= 0:
        return float("nan")
    lag = int(np.argmax(seg)) + lo_lag
    return float(sr) / lag


def cents_between(f_measured: float, f_reference: float) -> float:
    """Interval in cents. The unit every tuning parameter should report in."""
    if not (f_measured > 0 and f_reference > 0):
        return float("nan")
    return 1200.0 * math.log2(f_measured / f_reference)


def modulation_rate_hz(env, hop: float = DEFAULT_HOP, lo: float = 0.05,
                       hi: float = 25.0) -> float:
    """Dominant modulation frequency of an envelope, in hertz.

    ``LFORAT``/``PANRAT`` run 0..99 and the specification calls them "speed",
    with no unit anywhere. Point LFO1 at loudness, hold a note, and the
    envelope's own periodicity is the LFO rate.
    """
    np = _np()
    e = np.asarray(env, dtype="float64")
    if len(e) < 16:
        return float("nan")
    e = e - e.mean()
    if not np.any(e):
        return float("nan")
    spec = np.abs(np.fft.rfft(e * np.hanning(len(e))))
    freqs = np.fft.rfftfreq(len(e), hop)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return float("nan")
    idx = np.where(band)[0]
    return float(freqs[idx[int(np.argmax(spec[band]))]])


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------

def fit_exponential(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares fit of ``y = a * exp(b * x)``. Returns ``(a, b, r2)``.

    Sampler time and frequency scales are almost always exponential in the
    parameter -- the sibling project's MPC envelope curve came out as
    ``t(v) = 0.001005 * e^(10.3022 v)`` over four decades. Fit in log space,
    report r2, and *do not* trust a curve whose r2 is below about 0.99 without
    looking at the residuals: a poor fit usually means the machine is using a
    lookup table with a kink, which is a finding in itself.
    """
    np = _np()
    xs = np.asarray(list(x), dtype="float64")
    ys = np.asarray(list(y), dtype="float64")
    ok = np.isfinite(xs) & np.isfinite(ys) & (ys > 0)
    if ok.sum() < 3:
        return float("nan"), float("nan"), float("nan")
    xs, ys = xs[ok], ys[ok]
    ln = np.log(ys)
    b, ln_a = np.polyfit(xs, ln, 1)
    pred = ln_a + b * xs
    ss_res = float(((ln - pred) ** 2).sum())
    ss_tot = float(((ln - ln.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(math.exp(ln_a)), float(b), r2
