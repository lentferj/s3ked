# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# Every law in this file was measured on hardware by probes/calibrate.py; none
# of it is transcribed from a document, because no document states any of it.
# See docs/RESOLUTION_NOTES.md §20-§26 for the measurements and their bounds.
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

"""What a parameter value *means*, in hertz, seconds, decibels or cents.

The tables in :mod:`s3k.params` carry each parameter's range and none of its
meaning: ``FILFRQ`` is "basic filter frequency, 0 to 99" and not one word about
which hertz. These are the laws that close that gap, measured on an S3000XL.

**Every law has a range it was measured over, and that range is part of the
law.** Outside it a conversion is extrapolation, and this module says so rather
than quietly returning a number -- because a fit is not a specification, and a
sampler is free to do something else where nobody looked.

**An envelope DECAY or RELEASE value is a rate, not a duration**, and is
reported here in dB/s or FILFRQ-units per second accordingly. Both envelopes
slew at a constant rate in the log domain, so how long a stage *takes* depends
on how far it has to travel: ``time = span / rate``. Measured by holding the
value fixed and varying the distance -- the rate held to within 0.3 %
(amplitude) and 1.9 % (filter) while the duration moved 19 % and 28 %.

**An envelope ATTACK is a duration, and for the opposite reason.** The attack
is a linear ramp in *amplitude* rather than in the log domain (r² 0.9982
against 0.9343 for linear-in-dB), and it always travels the same distance --
zero to peak -- so its time is a property of the value alone. Linear attack,
exponential decay: the classic analog pairing, and the two obey different
exponents (0.11175 against ~0.0977), so they are two laws rather than one law
applied twice.

**No law here is provisional any more.** The mechanism stays -- a law whose
meaning is unsettled carries ``provisional`` and renders with a ``!`` -- and it
is still exercised by the tests, because the next measurement that comes back
half-answered should be marked rather than rounded up into certainty. The last
two entries to leave that list were ``FILQ`` (§53) and ``LFODEL`` (§55), and
neither was settled by fitting harder: each needed a different measurement.

**The two ends of a range are not symmetrical.** Sometimes an endpoint is
independently known even though the curve stops short of it: ``FILFRQ`` 99 is
*measured* wide open, because the calibration took its 0 dB reference there and
found no attenuation. Where such a fact exists it is recorded in ``endpoints``
and used in preference to refusing. This distinction is not academic -- the
sibling mpc2emu project clamped a "fully open filter" request to the top of the
*fitted* range, and every converted program came out audibly darker than before
the calibration existed. Caution made the output worse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

__all__ = [
    "Scale", "SCALES", "to_physical", "from_physical", "describe",
    "parse_quantity", "value_from_quantity",
]

#: Suffixes accepted on input, mapped to (unit, multiplier into the base unit).
#: Deliberately conservative: no bare "c" for cents, nothing that could be read
#: as something else. A plain number is never a quantity -- it is a raw value.
_UNIT_SUFFIXES = {
    "khz": ("Hz", 1000.0),
    "hz": ("Hz", 1.0),
    "ms": ("s", 0.001),
    "msec": ("s", 0.001),
    "sec": ("s", 1.0),
    "s": ("s", 1.0),
    "db": ("dB", 1.0),
    "cents": ("cents", 1.0),
    "cent": ("cents", 1.0),
}

#: Longest first, or a shorter suffix swallows a longer one that ends with it:
#: "cents" ends in "s", so seconds would claim "50cents" and then choke on the
#: leftover "50cent". Sorted here rather than hand-ordered above so adding a
#: suffix later cannot quietly reintroduce that.
_SUFFIXES = sorted(_UNIT_SUFFIXES.items(), key=lambda kv: -len(kv[0]))


@dataclass(frozen=True)
class Scale:
    """A measured mapping between a parameter value and a physical quantity."""

    region: str
    param: str
    unit: str
    kind: str                      # "exp" | "linear" | "pan" | "reso" | "pole"
    a: float
    b: float
    fitted: Tuple[int, int]        # the range the law was measured over
    r2: float
    #: Set when the law is known to be incompletely specified -- the number
    #: is the best available but its meaning is not yet settled. Rendered with
    #: a "!" so it can never be mistaken for a finished measurement.
    provisional: str = ""
    note: str = ""
    #: Why ``fitted`` stops where it does. Required whenever the fitted range
    #: is narrower than the parameter's own, because a range is as much a
    #: measurement as a coefficient and its reason is the part that gets lost
    #: in transcription. The sibling mpc2emu carried ``DECAY1`` as 0..99 for
    #: exactly that long: the number was in prose, the reason was in a
    #: different message, and only one of the two survived.
    bounds: str = ""
    #: Values outside ``fitted`` whose meaning is independently known.
    endpoints: Dict[int, str] = field(default_factory=dict)

    def value_to_physical(self, value: float) -> float:
        if self.kind == "exp":
            return self.a * math.exp(self.b * value)
        if self.kind == "linear":
            return self.a * value + self.b
        if self.kind == "pan":
            theta = (value + 50.0) / 100.0 * (math.pi / 2.0)
            eps = 1e-6
            theta = min(max(theta, eps), math.pi / 2 - eps)
            return self.a * 20.0 * math.log10(math.tan(theta)) + self.b
        if self.kind == "reso":
            # Resonant boost at the corner, in dB, for a pole pair whose
            # damping falls LINEARLY to zero at ``a``. At the corner the
            # response is 1/(2z), so relative to the value at 0 the boost is
            # 20 log10(z0/z) = -20 log10(1 - v/a) -- the z0 cancels, which is
            # why one parameter describes all sixteen steps.
            if value >= self.a:
                raise ValueError(
                    f"{self.param} {value} is at or past self-oscillation "
                    f"({self.a:.2f}); the field only reaches 15")
            return -20.0 * math.log10(1.0 - value / self.a)
        if self.kind == "pole":
            # A quantity that runs away as the value approaches ``b``, which
            # lies past the field's own maximum. Zero at value 0 by
            # construction, so the coefficient is the only free scale.
            if value >= self.b:
                raise ValueError(
                    f"{self.param} {value} is at or past the pole "
                    f"({self.b:.2f}); the field only reaches 99")
            return self.a * value / (self.b - value)
        raise ValueError(f"unknown scale kind {self.kind!r}")

    def resonance_q(self, value: float) -> float:
        """Q of the resonant pole pair. ``reso`` scales only; ``b`` is Q at 0."""
        if self.kind != "reso":
            raise ValueError(f"{self.param} is not a resonance scale")
        if value >= self.a:
            raise ValueError(f"{self.param} {value} is at or past {self.a:.2f}")
        return self.b / (1.0 - value / self.a)

    def physical_to_value(self, physical: float) -> float:
        if self.kind == "exp":
            if physical <= 0:
                raise ValueError(f"{self.unit} must be positive for {self.param}")
            return math.log(physical / self.a) / self.b
        if self.kind == "linear":
            return (physical - self.b) / self.a
        if self.kind == "pan":
            theta = math.atan(10.0 ** ((physical - self.b) / (self.a * 20.0)))
            return theta / (math.pi / 2.0) * 100.0 - 50.0
        if self.kind == "reso":
            if physical < 0:
                raise ValueError(f"{self.param} cannot cut, only boost")
            return self.a * (1.0 - 10.0 ** (-physical / 20.0))
        if self.kind == "pole":
            if physical < 0:
                raise ValueError(f"{self.param} cannot be negative")
            return self.b * physical / (self.a + physical)
        raise ValueError(f"unknown scale kind {self.kind!r}")

    def within(self, value: float) -> bool:
        return self.fitted[0] <= value <= self.fitted[1]


#: Measured 2026-08-11 on an S3000XL. RESOLUTION_NOTES §20-§26.
SCALES: Dict[Tuple[str, str], Scale] = {
    ("keygroup", "FILFRQ"): Scale(
        "keygroup", "FILFRQ", "Hz", "exp", 6.4597, 0.07100, (44, 92), 0.99984,
        bounds="every corner from 44 to 92 was measured. Below 44 the corner drops\nunder the lowest note's fundamental, so no harmonic sits beneath it\nand the fit becomes one-sided; above 92 the source runs out of\nharmonics above the corner and it becomes one-sided the other way.\nThe limit is where the SOURCE has energy either side of the corner,\nnot where the machine stops -- and it is a limit of this sawtooth,\nnot of the method.",
        note="One octave per 9.76 units; 7.36% per step, so a step is about\n"
             "0.81 semitones.\n"
             "MEASURED FROM THE RESONANCE PEAK, not from a spectral centroid.\n"
             "Turning FILQ up grows a peak AT the corner; differencing the\n"
             "spectrum against FILQ 0 at the same FILFRQ cancels the source's\n"
             "own spectrum and every fixed pole, so what is left locates the\n"
             "filter and nothing else. See RESOLUTION_NOTES §54.\n"
             "This REPLACES a law fitted by inverting a spectral centroid,\n"
             "which read 20-30% high and got worse as the corner rose:\n"
             "0.28 octaves at FILFRQ 40, 0.40 at 70, 0.52 at 99. A centroid is\n"
             "an average of everything the source contains, so it sits above\n"
             "the corner by an amount that depends on the source -- the error\n"
             "was in the ruler, and it was a slope error, not an offset.\n"
             "The old ruler also FOLDED (§20): it fell to a minimum and rose\n"
             "again, so only its rising branch could be inverted and the fold\n"
             "moved with pitch. A resonance peak does not fold, and the corner\n"
             "does not move with FILQ (919 Hz at all sixteen settings, §53).\n"
             "Cross-checks: two independent runs read 919 and 930 Hz at\n"
             "FILFRQ 70, 1.2% apart. The law was fitted on 62..92 and then\n"
             "predicted 44, 50 and 56 -- corners it had not seen -- to within\n"
             "0.2%, 0.5% and 2.2%.",
        endpoints={99: "wide open"},
    ),
    ("keygroup", "KGTUNO"): Scale(
        "keygroup", "KGTUNO", "cents", "linear", 100.0 / 256.0, 0.0,
        (-5120, 5120), 0.9998,
        bounds="confirmed by PITCH from -5120 to +5120 -- twenty semitones each\n"
               "way -- against a law first fitted over 0..50 raw, which is only\n"
               "0..19.5 cents. The field's declared range was 0..50 until the\n"
               "same measurement widened it to +/-12800: that was the document's\n"
               "display range in SEMITONES, transcribed as a raw range, and it\n"
               "made a one-semitone detune impossible to express. Beyond +/-20\n"
               "semitones the scale is unverified -- the pitch detector tops out,\n"
               "and whether the sampler transposes that far is a separate\n"
               "question from whether the field stores it (it does; every value\n"
               "up to 32767 round-tripped exactly).",
        note="One raw unit is 1/256 of a semitone, so the low byte is the\n"
             "binary fraction the document means by \"-50.00 to +50.00\".\n"
             "Measured: 256 -> +99.8 cents, 512 -> +199.8, 1280 -> +499.9,\n"
             "2560 -> +999.9, 5120 -> +1999.9, and the negatives match to\n"
             "0.3 cents. RESOLUTION_NOTES §56.",
    ),
    ("keygroup", "VTUNO1"): Scale(
        "keygroup", "VTUNO1", "cents", "linear", 100.0 / 256.0, 0.0, (-5120, 5120),
        0.999,
        bounds="range widened and the scale confirmed by KGTUNO's pitch\nmeasurement over +/-20 semitones (§56); this field shares the\nencoding and the 1/256-semitone unit. Originally: measured at both extremes at a fixed velocity; the intermediate\n"
               "points are not swept, so this is two points plus a structural\n"
               "constant rather than a fitted curve.",
        note="Per-ZONE tuning offset -- a STATIC offset, not a velocity\n"
             "modulation. The V in these zone field names refers to the\n"
             "velocity ZONE the field belongs to, not to velocity as a\n"
             "modulation source: LOVEL1/HIVEL1 define the zone and\n"
             "VTUNO1/VLOUD1/VFREQ1/VPANO1 are that zone's offsets.\n"
             "Same exact 100/256 scale as KGTUNO and PTUNO, with which it\n"
             "shares its spec wording -- measured 0.3878 cents/unit against\n"
             "the structural 0.390625, 0.7% apart.\n"
             "Its three companions respond equally strongly at a fixed\n"
             "velocity: VLOUD1 39.81 dB, VFREQ1 3346 Hz, VPANO1 118.57 dB\n"
             "across their ranges.",
    ),
    ("program", "PTUNO"): Scale(
        "program", "PTUNO", "cents", "linear", 100.0 / 256.0, 0.0, (-5120, 5120),
        0.9998,
        note="same exact 100/256 scale as KGTUNO, spot-checked at two values",
        bounds="range widened and the scale confirmed by KGTUNO's pitch\nmeasurement over +/-20 semitones (§56); this field shares the\nencoding and the 1/256-semitone unit. Originally: spot-checked rather than swept; KGTUNO carries the evidence.",
    ),
    ("program", "PRLOUD"): Scale(
        "program", "PRLOUD", "dB", "linear", 0.61872, -0.61872 * 99, (0, 99),
        0.99965,
        bounds="swept at 28 values with velocity neutralised (V_LOUD 0). It\n"
               "does NOT saturate: monotonic all the way to 99, no plateau.\n"
               "That was checked because two runs appeared to show a ceiling\n"
               "at 80, which turned out to be V_LOUD's boost pushing the\n"
               "OUTPUT into a limit, not PRLOUD flattening.",
        note="relative to PRLOUD 99. The measured intercept was an absolute\n"
             "level that depended on the rig's gain and meant nothing away\n"
             "from that bench; the useful quantity is attenuation from full.\n"
             "Re-measured at 0.61872 dB/unit, r2 0.99965, superseding an\n"
             "earlier 0.642719 at r2 0.9933 -- the worst fit in this table,\n"
             "and the reason it was re-examined. The residuals still tilt\n"
             "(+0.081 dB over the first half, -0.081 over the second), so a\n"
             "slight curvature remains and the straight line is a good\n"
             "approximation rather than the true shape.",
    ),
    ("keygroup", "SUSTN1"): Scale(
        "keygroup", "SUSTN1", "dB", "linear", 0.60676, -0.60676 * 99, (40, 99),
        0.99993,
        bounds="fitted 40..99, every point verified to have at least 6 dB of\n"
               "headroom to the output ceiling and 22 dB above the noise\n"
               "floor, both measured in the same session. Below 40 the level\n"
               "approaches the floor; 0 is separately known to be silent.",
        note="linear in dB, NOT in amplitude; a half-amplitude sustain is 89.\n"
             "Re-measured with headroom verified at BOTH ends after an audit\n"
             "found earlier sweeps ran into an output ceiling. The original\n"
             "0.60832 survives that check to 0.26%, so the ceiling did not\n"
             "materially bite here -- only the topmost point sat near it.\n"
             "A control run at PRLOUD 70 gave 0.57888, wrong by 4.6% in the\n"
             "other direction because its low end was in the noise floor. A\n"
             "ceiling and a floor both FLATTEN a curve, from opposite ends,\n"
             "so two such runs do not bracket the truth -- they both\n"
             "understate the slope and the answer lies outside them.\n"
             "Residual halves +0.005/-0.004 dB, the flattest in this table.",
        endpoints={0: "silent"},
    ),
    ("program", "LFORAT"): Scale(
        "program", "LFORAT", "Hz", "linear", 0.11867, -0.04, (10, 99), 0.9995,
        bounds="below 10 the LFO period exceeds the capture window, so a rate\ncannot be counted rather than the machine doing anything odd.",
        note="LFO1, which drives pitch",
    ),
    ("keygroup", "ATTAK1"): Scale(
        "keygroup", "ATTAK1", "s", "exp", 0.000201173, 0.10844, (55, 90),
        0.99988,
        bounds="swept 55..90. Below 55 the whole rise crosses in fewer than\n"
               "thirteen analysis windows; above 90 it outlasts the capture.\n"
               "Both are limits of the rig, not of the machine.",
        note="A genuine RISE TIME, and the only envelope stage that has one.\n"
             "The attack is a linear ramp in AMPLITUDE -- r2 0.9982 against\n"
             "0.9578 square-law, 0.9343 linear-in-dB and 0.9302 exponential,\n"
             "consistent to within 0.001 across eight values. That is the\n"
             "classic analog pairing: linear attack, exponential decay.\n"
             "It is a time rather than a rate BECAUSE the amplitude attack\n"
             "always travels the same distance, zero to peak. A decay's\n"
             "distance depends on the sustain level, which is why those are\n"
             "rates. The earlier fit (0.00023924, exp 0.10463) is withdrawn:\n"
             "it was 19% low at value 90.\n"
             "Note the exponent, 0.11175, is NOT the 0.0977 the decays share.\n"
             "Attack and decay are different laws, not one law twice.",
        endpoints={0: "fastest"},
    ),
    ("keygroup", "DECAY1"): Scale(
        "keygroup", "DECAY1", "dB/s", "exp", 23525.6, -0.09776, (45, 85),
        0.99998,
        bounds="swept 45..85 at a fixed ~48 dB span. Below 45 the fall crosses\n"
               "in too few analysis windows to time; above 85 it outlasts the\n"
               "capture. Both are limits of the rig, not of the machine.",
        note="A RATE, not a duration. The stage slews at this many decibels\n"
             "per second, so it takes span/rate seconds to cross whatever\n"
             "distance it is given -- divide the distance by this to convert\n"
             "a target time. Every individual curve fits a straight line in\n"
             "dB at r2 0.999 to 1.000, so the ramp model is confirmed value\n"
             "by value rather than on average, which is exactly what the\n"
             "retracted exponential model never managed: it sat at 0.945\n"
             "everywhere, and flat mediocrity is bias rather than noise.",
        endpoints={0: "fastest"},
    ),
    ("keygroup", "RELSE1"): Scale(
        "keygroup", "RELSE1", "dB/s", "exp", 22055.3, -0.09683, (55, 70),
        0.99956,
        bounds="fitted from 55 up. At 45 and 50 the fall crosses the whole\n"
               "range in 30-42 analysis windows and the rate is quantised;\n"
               "including those two dropped the fit from r2 0.9996 to 0.9916,\n"
               "so they are measured but excluded. Above 70 the fall outlasts\n"
               "the 2 s capture tail.",
        note="A RATE, not a duration: decibels per second, so the time taken\n"
             "is span/rate. The span is the level the note had reached when\n"
             "the key was let go, so a release 'time' is not a property of\n"
             "this value alone.\n"
             "MEASURED, not assumed (RESOLUTION_NOTES §34). Sweeping SUSTN1\n"
             "with this value held: the level at note-off tracks the SUSTN1\n"
             "dB law to within 0.33 dB, the rate holds to CV 0.6%, and the\n"
             "fall time divided by 0.60832*SUSTN1 holds to CV 0.9% while the\n"
             "fall time alone varies 35%. So for a note released from its\n"
             "sustain, span = 0.60832 * SUSTN1 dB.\n"
             "That run is the calibration's first real cross-validation: it\n"
             "predicts data from two laws fitted on separate sweeps, neither\n"
             "of them fitted to it.",
        endpoints={0: "fastest"},
    ),
    # --- the filter envelope ------------------------------------------------
    # Measured through a FILFRQ ruler rather than through the raw spectral
    # centroid: the centroid's floor is the fundamental and its ceiling is the
    # source's own brightness, so it compresses the axis it is supposed to be
    # reading. See RESOLUTION_NOTES §28.
    ("keygroup", "ATTAK2"): Scale(
        "keygroup", "ATTAK2", "s", "exp", 0.001363, 0.09703, (40, 85), 0.999807,
        bounds="40..85 at two drive levels. Below 40 the rise completes inside\n"
               "the 0.14 s detector latency; above 85 it outlasts an 8 s\n"
               "capture -- 99 would need about 30 s.",
        note="Seconds for the filter envelope to traverse its FULL 0..99 range.\n"
             "Re-measured 2026-08-12 with the resonance tracker (§58). §28 read\n"
             "this through a spectral centroid and left it provisional over a\n"
             "THREEFOLD disagreement between MODVFILT1 18 and 25, recorded as\n"
             "depth-dependence. It was not: swept at MODVFILT1 5 and 10 the\n"
             "times agree to 1.00 +/- 0.01 across nine values while the spans\n"
             "differ by exactly the drive ratio. The depth-dependence was a\n"
             "CEILING -- the corner saturating at the top of the filter's own\n"
             "range -- and the provisional mark comes off.\n"
             "IDENTICAL TO ENV3R1, which is the same measurement over the same\n"
             "distance: 0.200/0.290/0.560/1.250/3.010 s against\n"
             "0.200/0.300/0.550/1.250/3.010 at values 40/50/60/70/80. Two\n"
             "envelopes, one time base.\n"
             "WHETHER IT IS A RATE OR A DURATION IS NOT SETTLED HERE. §28 read\n"
             "it as a duration, but an attack always travels 0-to-full, so a\n"
             "fixed distance cannot tell the two apart. ENV3R1 -- the same law\n"
             "-- IS a rate, proven by varying its target level (§57), which\n"
             "makes a rate the better guess. Envelope 2 has its own attack\n"
             "target in ENV2L1 and the test has not been run.",
    ),
    ("keygroup", "DECAY2"): Scale(
        "keygroup", "DECAY2", "s", "exp", 0.002464, 0.09844, (40, 80), 0.999972,
        bounds="40..80. Above 80 the fall outlasts the capture; at 90 the span\n"
               "had already collapsed from 1.63 octaves to 0.97, which is the\n"
               "truncation showing rather than the machine changing.",
        note="Seconds to traverse the FULL 0..99 range, so a phase covering\n"
             "part of it takes  full_time * (distance / 99). About twice\n"
             "ATTAK2 at the same value.\n"
             "Re-measured 2026-08-12 with the resonance tracker (§58); the\n"
             "previous law was 25200 * exp(-0.09796 v) FILFRQ-units/s, read\n"
             "through a spectral centroid. Its RATE-not-duration finding\n"
             "survives -- that came from varying the span by 72% and watching\n"
             "the rate hold to 1.9%, a relative comparison a biased ruler\n"
             "distorts far less than an absolute one.",
        endpoints={0: "fastest"},
    ),
    ("keygroup", "RELSE2"): Scale(
        "keygroup", "RELSE2", "s", "exp", 0.001344, 0.09692, (40, 80), 0.999975,
        bounds="40..80. Measured after note-off, with the recording tail raised\n"
               "to 10 s and RELSE1 parked at 99 so the amplitude outlasts the\n"
               "filter release -- otherwise the note falls into the noise while\n"
               "the corner is still moving.",
        note="Seconds to traverse the FULL 0..99 range, released to ENV2L4.\n"
             "Re-measured 2026-08-12 (§59); the previous law was\n"
             "61190 * exp(-0.10123 v) FILFRQ-units/s, read through a spectral\n"
             "centroid, over 58..76.\n"
             "THE ATTACK AND THE RELEASE ARE ONE LAW, across both envelopes:\n"
             "  ATTAK2  0.001363 * exp(0.09703 v)\n"
             "  RELSE2  0.001344 * exp(0.09692 v)\n"
             "  ENV3R1  0.001392 * exp(0.09669 v)\n"
             "coefficients within 3.6%, exponents within 0.35%, predictions\n"
             "2.2% apart at value 70. The DECAYS are about twice as slow --\n"
             "DECAY2 1.9-2.0x and ENV3R3 2.0-2.3x -- so the family is one time\n"
             "base with the decay stages running at half rate, not five\n"
             "separate calibrations.",
        endpoints={0: "fastest"},
    ),
    ("keygroup", "K_DAR1"): Scale(
        "keygroup", "K_DAR1", "x per semitone", "exp", 1.0, -0.0015286,
        (-50, 50), 0.9960,
        bounds="the full -50..50 was swept, at notes 48/56/64/72/84.",
        note="Key scaling of the amplitude DECAY rate. The `K_` prefix in this\n"
             "group means KEY, not keygroup, which makes K_DAR2 and K_DAR3 its\n"
             "likely companions.\n"
             "  rate = base * exp(-0.0015286 * K_DAR1 * (note - 64)) dB/s\n"
             "The value here is the multiplier per semitone per unit of depth.\n"
             "**Referenced to note 64**, and unusually this is visible in the\n"
             "raw table rather than fitted: every depth from -50 to +50 reads\n"
             "25.3 dB/s at note 64, and the K_DAR1 0 row is flat at 25.3 across\n"
             "36 semitones. Second key-driven field to pivot there after\n"
             "K_FREQ, so §43's rule now rests on two independent measurements.\n"
             "At full depth the decay rate changes by a factor of 0.40 per\n"
             "octave -- positive depth makes high notes decay slower.\n"
             "It is the ONLY responder among six envelope scaling fields:\n"
             "V_REL1, O_REL1, V_ATT2, V_REL2 and V_ENV2 are all inert (§47).",
    ),
    ("keygroup", "K_DAR3"): Scale(
        "keygroup", "K_DAR3", "x per semitone", "exp", 1.0, 0.0015617,
        (-20, 20), 0.99,
        bounds="-20..+20, with the SOUND held at note 48 by KGTUNO while the\n"
               "NOTE swept 48..68 across the pivot (§62). The coefficient here\n"
               "is phase 3's; the release scales more weakly, at 0.0011903.",
        note="Key scaling of envelope 3, and it does exactly what its\n"
             "description says -- 'dependence of envelope 3 release and DECAY\n"
             "rate on key'. It scales PHASE 3 and the RELEASE, and leaves\n"
             "PHASE 2 alone:\n"
             "  phase 3    coefficient 0.0015617   pivot 63.5\n"
             "  release    coefficient 0.0011903   pivot 64.0\n"
             "  phase 2    coefficient 0.0000451   no effect -- 30x smaller\n"
             "             than phase 3's and inside its own control\n"
             "Phase 3's coefficient is within 2% of K_DAR1's 0.0015286 and 7%\n"
             "of K_DAR2's 0.0014603, so all three envelopes scale with the key\n"
             "by one law. The release's is 24% lower, which is a real\n"
             "difference rather than scatter: both its depths agree on it.\n"
             "The first attempt at this measured PHASE 2 and found nothing --\n"
             "route live, detector working, aimed at a stage the field does\n"
             "not govern. That reading was CORRECT for phase 2 and would have\n"
             "been recorded as 'K_DAR3 is inert', which is the §48 error one\n"
             "layer in. A negative needs the right stage as well as the right\n"
             "route.",
    ),
    ("keygroup", "V_ENV3"): Scale(
        "keygroup", "V_ENV3", "x", "linear", 1.0, 0.0, (-50, 50), 0.99,
        bounds="tested at -50, 0 and +50 against velocities 20 and 120. The\n"
               "shape between those depths is not measured -- this entry\n"
               "records that the field works and in which direction, not a\n"
               "curve.",
        note="Note-on velocity scales envelope 3's AMOUNT, bipolar:\n"
             "  V_ENV3 +50   span 0.515 octaves at velocity 20, 2.242 at 120\n"
             "  V_ENV3   0   1.674 and 1.684 -- the control, ratio 0.99\n"
             "  V_ENV3 -50   2.391 and 0.290, the sense inverted\n"
             "Envelope 3 reaches the filter only as matrix source 14, so this\n"
             "scales whatever that route is driving.",
    ),
    ("keygroup", "V_ATT3"): Scale(
        "keygroup", "V_ATT3", "x", "linear", 1.0, 0.0, (-50, 50), 0.99,
        bounds="tested at -50, 0 and +50 against velocities 20 and 120, with\n"
               "ENV3R1 at 70 so there is about 1.25 s of rise to scale.",
        note="Note-on velocity scales envelope 3's ATTACK, bipolar and very\n"
             "strong:\n"
             "  V_ATT3 +50   rise 0.160 s at velocity 20, 5.920 s at 120\n"
             "  V_ATT3   0   1.110 and 1.250 -- the control, ratio 0.89\n"
             "  V_ATT3 -50   5.920 and 0.140, the sense inverted\n"
             "A 37-fold swing at full depth. The control is 0.89 rather than\n"
             "1.00, so an effect smaller than about 11% could not be claimed\n"
             "from this run; the measured effects are 30-fold and 42-fold.\n"
             "The first attempt read 0.000 s at every velocity and depth,\n"
             "because ENV3R1 was 0 and there was no rise for velocity to\n"
             "scale. Its control came back NaN, which is the only reason that\n"
             "was not written down as an inert field.",
    ),
    ("keygroup", "K_DAR2"): Scale(
        "keygroup", "K_DAR2", "x per semitone", "exp", 1.0, 0.0014603,
        (-20, 20), 0.99584,
        bounds="-20..+20 at notes 24/30/36/42/48. The DEPTH is narrow because\n"
               "at +/-50 these notes span a 400-fold range of decay times and\n"
               "no single DECAY2 holds that inside a measurable window. The\n"
               "NOTES are low because the corner tracker's resolution is the\n"
               "harmonic spacing -- 3.5% of the corner at note 24, 14% at 48,\n"
               "56% at 72 -- so anything read through the filter lives in the\n"
               "bottom two octaves. §48 could use notes 48..84 for K_DAR1\n"
               "because it measured the amplitude envelope in dB, which has no\n"
               "comb.",
        note="Key scaling of the filter envelope's DECAY, and the companion to\n"
             "K_DAR1 that its note predicted.\n"
             "  time = base * exp(+0.0014603 * K_DAR2 * (note - 64))\n"
             "The sign is positive where K_DAR1's is negative because this is\n"
             "a TIME and that was a RATE; they are the same behaviour.\n"
             "PREDICTED BEFORE MEASUREMENT (§61) from K_DAR1's coefficient:\n"
             "time(24)/time(48) of 0.48 at depth +20 and 2.08 at -20, measured\n"
             "0.50 and 2.16 -- 4% off both. The fitted coefficient lands 4.5%\n"
             "from K_DAR1's, so the two envelopes scale with the key by the\n"
             "same law.\n"
             "The control is exact: at K_DAR2 0 the decay reads 0.670..0.680 s\n"
             "across 24 semitones, a spread of 1.015x.\n"
             "The pivot was taken on trust from §48 for one day and is now\n"
             "MEASURED at note 63.6 (§62), by holding the SOUND at note 48\n"
             "with KGTUNO while the NOTE swept 48..68 across the pivot. That\n"
             "works because key scaling reads the MIDI note and not the\n"
             "sounding pitch -- two conditions that sound identical and differ\n"
             "only in note number gave 1.95 at depth +20 and exactly 1.00 at\n"
             "depth 0.",
    ),
    ("keygroup", "K_FREQ"): Scale(
        "keygroup", "K_FREQ", "semitones/octave", "linear", 1.0, 0.0, (0, 12),
        0.99890,
        bounds="the full 0..12 was swept, at three notes, each with its own\n"
               "ruler rebuilt in the same session.",
        note="Keyboard tracking of the filter, and the value IS semitones of\n"
             "corner shift per octave of key -- exactly as the document says,\n"
             "with 12 being 1:1. Measured 9.2 FILFRQ units per octave at\n"
             "K_FREQ 12 against 9.39 for true 1:1, so 98%.\n"
             "  shift (FILFRQ units) = 0.06386 * K_FREQ * (note - 64)\n"
             "**Referenced to note 64, not to middle C and not to the sample\n"
             "root.** Slopes of +0.1310, +0.5362 and +0.8973 units per step at\n"
             "notes 66, 72 and 78 extrapolate to zero shift at note 63.8\n"
             "(r2 0.99890). Assuming note 60 was what made this look like 8.4\n"
             "semitones per octave rather than 12.\n"
             "That reference is the MIDI range's centre, and it is shared:\n"
             "V_LOUD and V_ATT1 both pivot at velocity 64. Three fields, two\n"
             "source types, one rule -- modulation is referenced to the middle\n"
             "of the controller's range. It predicts where MWLDEP, PRSDEP and\n"
             "the per-zone velocity fields will pivot, so it can be refuted.",
    ),
    ("keygroup", "FILQ"): Scale(
        "keygroup", "FILQ", "dB", "reso", 15.84, 1.067, (0, 15), 0.999975,
        bounds="the full 0..15, every value measured. The law is fitted to the\n"
               "DAMPING, which is linear across the whole range, so there is no\n"
               "sub-range where it holds better.",
        note="FILQ sets the damping of one pole pair, and it does so LINEARLY:\n"
             "  z = 0.46864 - 0.029587 * FILQ    r2 0.999975\n"
             "Damping reaches zero at FILQ 15.84 -- past the top of the field,\n"
             "so the machine stops just short of self-oscillation. Q runs from\n"
             "1.07 at FILQ 0 to about 20 at FILQ 15, and the boost at the\n"
             "corner is -20 log10(1 - FILQ/15.84) dB: +3.3 at 5, +8.7 at 10,\n"
             "+25.5 at 15. The last three steps are worth more than the first\n"
             "ten put together, which is what makes the field feel abrupt.\n"
             "Measured by holding the corner still and sliding the harmonic\n"
             "comb across it with the NOTE (K_FREQ 0), differencing every\n"
             "harmonic against the same harmonic at FILQ 0. The fit is to the\n"
             "whole transfer function -- 2985 points -- not to the height of\n"
             "the peak, and because it is a RATIO any fixed non-resonant poles\n"
             "cancel exactly, so it isolates the pair FILQ actually moves.\n"
             "Two runs with different note sets agree: fc 919 vs 918 Hz, slope\n"
             "-0.029587 vs -0.029688, zero at 15.84 vs 15.93.\n"
             "The corner does NOT move with FILQ: 919 Hz at every setting.\n"
             "It does sit BELOW the corner the FILFRQ law gives -- 919 Hz\n"
             "measured against 1229 nominal at FILFRQ 70, 0.42 octaves down.\n"
             "That REPLICATES the earlier 0.41 octaves at FILFRQ 77 (1570 vs\n"
             "2093), so the offset is a property of the machine and not of one\n"
             "operating point: the two fields do not share a frequency\n"
             "reference and a converter must not assume they do. The FILFRQ\n"
             "law was fitted by inverting a spectral centroid, which is not\n"
             "the corner, and a resonance peak locates it far better -- see\n"
             "RESOLUTION_NOTES for the re-derivation this calls for.\n"
             "Reading the peak height directly under-reads it -- 23.2 dB\n"
             "observed at FILQ 15 against 25.5 from the fit -- because at Q 20\n"
             "the peak is 46 Hz wide and a harmonic comb steps past it. That\n"
             "is the same trap as the first two attempts, and the reason the\n"
             "law is fitted to damping instead of to peak height.",
    ),
    ("keygroup", "ENV3R1"): Scale(
        "keygroup", "ENV3R1", "s", "exp", 0.001392, 0.09669, (40, 90), 0.999936,
        bounds="below 40 the phase completes inside the detector's 0.14 s\n"
               "latency; above 90 the sweep outruns a 9 s capture (20 s at 99).\n"
               "Both ends are the rig, not the machine.",
        note="Seconds for the envelope to traverse its FULL 0..99 level range.\n"
             "ENV3R1 sets a RATE, not a duration -- measured by holding it fixed\n"
             "and sweeping the distance (ENV3L1): the time tracked the distance\n"
             "5.05x against 5.39x at R1 70, and 4.95x against 5.11x at R1 80,\n"
             "with seconds-per-octave constant to 3% across the sweep. So a\n"
             "phase takes  full_time * (distance / 99).\n"
             "HIGHER IS SLOWER, despite the table calling it 'Attack rate':\n"
             "0.067 s at 40, 0.46 s at 60, 3.2 s at 80, 20 s at 99. The rate\n"
             "falls 9.2% per step and halves every 7.2 steps.\n"
             "Envelope 3 has no fixed destination -- it reaches the filter only\n"
             "as assignable-matrix source 14 (§48). Measured there, with the\n"
             "corner read from the resonance peak (§57), which is why this is\n"
             "in seconds rather than in the octaves the old centroid ruler\n"
             "would have given.",
    ),
    ("keygroup", "ENV3R2"): Scale(
        "keygroup", "ENV3R2", "s", "exp", 0.002565, 0.09762, (40, 80), 0.999833,
        bounds="40..80, with value 70 excluded as a single bad take -- it read\n"
               "0.010 s between neighbours at 1.04 and 2.80, with the largest\n"
               "span in the set. Excluded as an outlier and named as one\n"
               "rather than quietly dropped.",
        note="Seconds for a full 0..99 traverse, phase 2 of envelope 3.\n"
             "PREDICTED BEFORE IT WAS MEASURED (§60): §59's structure said a\n"
             "falling stage should join the decays at about 2.22 s at value\n"
             "70, and it measured 2.38 -- 7% off, on a law fitted to five\n"
             "other fields. It sits with DECAY2 (2.42 s) and ENV3R3 (2.49 s).\n"
             "Two detector faults had to be cleared first, and both were\n"
             "caught by their own signature rather than by inspection. The\n"
             "fall time read 0.020 s at EVERY setting because the minimum was\n"
             "taken over the whole track, and with an instant attack that\n"
             "minimum is the base corner in the opening frame -- so 90%-below\n"
             "-peak was satisfied before the fall began. Then the spans\n"
             "collapsed 2.10 -> 1.40 -> 0.56 because phase 2 happens DURING\n"
             "the note and the note was 1.5 s, inherited from a release probe\n"
             "where 1.5 was right.",
    ),
    ("keygroup", "ENV3R4"): Scale(
        "keygroup", "ENV3R4", "s", "exp", 0.001515, 0.09549, (40, 80), 0.999973,
        bounds="40..80, measured after note-off with the recording tail and\n"
               "the analysis window BOTH raised to 10 s, and RELSE1 at 99 so\n"
               "the amplitude outlasts the filter release.",
        note="Seconds for a full 0..99 traverse, the release phase of\n"
             "envelope 3.\n"
             "PREDICTED BEFORE IT WAS MEASURED (§60): §59's structure said a\n"
             "release should join the attack law at about 1.20 s at value 70,\n"
             "and it measured 1.21 -- 1% off. It sits with ATTAK2, RELSE2 and\n"
             "ENV3R1, all within 1.21..1.25 s at that value.\n"
             "So the grouping really is by STAGE TYPE and not by envelope:\n"
             "attack and release share one rate, the falling stages run at\n"
             "about half of it, across both envelopes and all seven fields.",
    ),
    ("keygroup", "ENV3R3"): Scale(
        "keygroup", "ENV3R3", "s", "exp", 0.003815, 0.09258, (10, 85), 0.999802,
        bounds="above 85 the fall outruns a 9 s capture (36 s at 99). The low\n"
               "end is fine here because the measured phase was a FALL from a\n"
               "level already reached, so no attack had to complete first.",
        note="Seconds to traverse the full 0..99 level range, as ENV3R1.\n"
             "Also a rate, also higher-is-slower: 0.16 s at 40, 0.99 s at 60,\n"
             "6.3 s at 80, 36 s at 99 -- about 2.7x slower than ENV3R1 at the\n"
             "same value, while their exponents agree to 4.3%, so the two\n"
             "phases share a time base and differ in scale.\n"
             "ENV3R2 and ENV3R4 are NOT measured. They are presumably the same\n"
             "family, and presuming is what §48 did about envelope 3 being\n"
             "inert.",
    ),
    ("keygroup", "SUSTN2"): Scale(
        "keygroup", "SUSTN2", "%", "linear", 100.0 / 99.0, 0.0, (0, 99), 0.99978,
        bounds="the whole field. §28 stopped at 70 because its centroid ruler\n"
               "ran out of resolution once the corner passed the source\n"
               "bandwidth; the resonance tracker has no such limit here.",
        note="Percent of the filter envelope's full amount, and LINEAR in\n"
             "OCTAVES: 0.40, 0.80, 1.21, 1.64, 2.05 octaves at 20, 40, 60, 80,\n"
             "99 with MODVFILT1 10. Its sibling SUSTN1 is linear in dB, so both\n"
             "envelopes are linear in the log domain, each in its own.\n"
             "In absolute terms the shift is\n"
             "  octaves = 0.002075 * SUSTN2 * MODVFILT1\n"
             "which needs the depth as well and so cannot be rendered from this\n"
             "value alone. §28 gave 0.024645 FILFRQ-units for the same product,\n"
             "which is 0.002524 octaves -- 22% high, and high in the same\n"
             "direction as everything else that ruler measured (§54).\n"
             "Envelope 3 scales differently: 0.002685 octaves per unit of\n"
             "level x depth against this 0.002075. Measured, not explained.",
    ),
    ("program", "V_LOUD"): Scale(
        "program", "V_LOUD", "dB", "linear", 0.009474 * 63, 0.0, (-50, 50),
        0.99999,
        bounds="the full -50..50 was swept, at every velocity from 1 to 127\n"
               "for one depth. The number here summarises a two-variable law\n"
               "-- see the note for the form that actually applies.",
        note="Velocity sensitivity, and the value here is the dB of BOOST at\n"
             "full velocity relative to the pivot. The law is a gain about a\n"
             "pivot, not an attenuation below a knee:\n"
             "  gain_dB = 0.009474 * V_LOUD * (velocity - 64)\n"
             "so velocity above 64 makes the program LOUDER than PRLOUD\n"
             "alone would suggest, and below 64 quieter. Linear in raw\n"
             "velocity at r2 0.999992 over every value from 1 to 127, with\n"
             "residuals showing no tilt (+0.003 dB first half, -0.003 second).\n"
             "Every velocity produces a distinct level: no internal\n"
             "quantisation, the machine uses all 127.\n"
             "The slope is exactly proportional to V_LOUD -- 0.4741 dB per\n"
             "velocity unit at 50 against 0.2366 at 25.\n"
             "At V_LOUD 0 velocity does nothing at all, measured flat to\n"
             "0.00 dB, so this field is the whole of the sensitivity.\n"
             "**The flat top seen above velocity 66 is an OUTPUT CEILING,\n"
             "not part of the law.** It moves with PRLOUD -- velocity 66 at\n"
             "PRLOUD 99, 90 at 80, past 100 at 60 -- because a louder program\n"
             "reaches the ceiling sooner. A converter must expect clipping\n"
             "when PRLOUD is high and V_LOUD positive.\n"
             "This is the one field here not linear in a perceptual domain.",
    ),
    ("program", "PANRAT"): Scale(
        "program", "PANRAT", "Hz", "linear", 0.23708, 0.0, (5, 80), 0.999843,
        bounds="fitted 5..80. Above 80 the measured rate collapses to exactly\n"
               "HALF the extrapolation (ratios 0.502, 0.501, 0.500, 0.504),\n"
               "which is a detector artefact rather than a machine behaviour --\n"
               "the fold MOVED when the analysis window shortened, from above\n"
               "60 at 40 ms to above 80 at 15 ms. It did not move\n"
               "proportionally, so the mechanism is not understood and no\n"
               "correction is applied.",
        note="LFO2's rate, and LFO2 is NOT the pan-only oscillator §39 took it\n"
             "for: it is assignable-matrix source 8, and routed to the filter\n"
             "it works perfectly. Only its route to PAN is inert.\n"
             "  rate = 0.23708 * PANRAT Hz\n"
             "**Exactly twice LFO1's rate for the same value** -- 0.23708\n"
             "against LFORAT's 0.11867, a ratio of 1.998. Forced through the\n"
             "origin: the free fit's intercept was 0.026 Hz, and zero rate at\n"
             "zero is definitional.\n"
             "Its four companions all work too, through the same route:\n"
             "PANDEP gates the depth, PANDEL delays the growth, LFO2WAVE\n"
             "changes the shape, and LFO2TRIG mode 1 locks the phase to\n"
             "note-on (sd 54 Hz across five notes against 307..471 for the\n"
             "free-running modes).",
    ),
    ("program", "LFODEP"): Scale(
        "program", "LFODEP", "cents", "linear", 19.4932, 0.0, (0, 99), 0.99949,
        bounds="the full range was usable, which is unusual here -- most\n"
               "bounds in this table are limits of the rig rather than the\n"
               "machine, and this one has neither.",
        note="Peak-to-peak vibrato depth on LFO1, which drives pitch (§25).\n"
             "LINEAR in cents at r2 0.9997 against 0.8663 for exponential --\n"
             "not close. Full depth is ~1930 cents peak to peak, so +/-9.6\n"
             "semitones, a wider range than the panel suggests.\n"
             "Cents rather than hertz because a ratio is independent of the\n"
             "note played and of the rig's tuning.\n"
             "Forced through the origin: LFODEP 0 is no vibrato by\n"
             "definition, so the free fit's -17.4 cent intercept is bias --\n"
             "the same correction as KGTUNO and FILQ.\n"
             "Note what it joins: linear in CENTS is linear in the log\n"
             "domain, as SUSTN1 is linear in dB and SUSTN2 in octaves. Three\n"
             "independent level-like fields, all linear in their perceptual\n"
             "domain.",
    ),
    ("program", "MWLDEP"): Scale(
        "program", "MWLDEP", "cents", "linear", 19.5299, 0.0, (0, 99), 0.99968,
        bounds="the full range was usable, at wheel 127.",
        note="Mod wheel into LFO1, so peak-to-peak vibrato in cents. The value\n"
             "here is the depth at a FULL wheel; the wheel scales it linearly\n"
             "at 15.2001 cents per wheel unit (r2 0.99989).\n"
             "  cents_pp = 1930 * (MWLDEP/99) * (wheel/127)\n"
             "**No pivot.** The wheel is proportional to its value, 0.50 of\n"
             "full depth at wheel 64. That REFUTES the §43 rule for unipolar\n"
             "controllers: velocity and note pivot on 64 because each has an\n"
             "inherent centre, while a wheel rests at zero and a pivot there\n"
             "would mean a wheel at rest applying maximum modulation.",
    ),
    ("program", "PRSDEP"): Scale(
        "program", "PRSDEP", "cents", "linear", 19.50, 0.0, (0, 99), 0.9995,
        bounds="measured at pressure 127 across 0..99.",
        note="Channel pressure into LFO1. Same law and same scale as MWLDEP:\n"
             "  cents_pp = 1930 * (PRSDEP/99) * (pressure/127)\n"
             "15.20 cents per pressure unit at PRSDEP 99, matching the wheel's\n"
             "15.2001 to three figures.\n"
             "**Pressure must arrive DURING the note.** Sent before note-on it\n"
             "does nothing at all, which an earlier run read as the field being\n"
             "inert. It is not: sent 0.4 s after note-on it is linear across\n"
             "the whole controller range. That was my timing, not the machine.",
    ),
    ("program", "VELDEP"): Scale(
        "program", "VELDEP", "cents", "linear", 19.48, 0.0, (0, 99), 0.9996,
        bounds="measured at velocity 127 across 0..99.",
        note="Velocity into LFO1 depth, same law again:\n"
             "  cents_pp = 1930 * (VELDEP/99) * (velocity/127)\n"
             "So all four LFO1 depth sources -- LFODEP, MWLDEP, PRSDEP and\n"
             "VELDEP -- are linear and reach the SAME full-scale depth of\n"
             "about 1930 cents peak-to-peak, within 0.2% of each other.\n"
             "Note this one does NOT pivot on 64 either, despite velocity\n"
             "being the source: it is the DEPTH route rather than a bipolar\n"
             "modulation, so it scales from zero like the wheel. V_LOUD and\n"
             "V_ATT1 pivot; VELDEP does not.",
    ),
    ("program", "LFODEL"): Scale(
        "program", "LFODEL", "s", "pole", 0.06905, 103.41, (0, 99), 0.999555,
        bounds="the whole field, all fourteen points measured. Below LFODEL 20\n"
               "the delay is under the 0.008 s time resolution and reads zero,\n"
               "which is the right answer to two decimal places.",
        note="Delay before LFO1's vibrato begins:\n"
             "  seconds = 0.06905 * LFODEL / (103.41 - LFODEL)\n"
             "It runs away toward a pole at 103.4 -- past the field's own top,\n"
             "so the machine never reaches it, but the last few steps are very\n"
             "much steeper than the first: 0.065 s at 50, 0.24 s at 80, 0.46 s\n"
             "at 90, 1.55 s at 99. Same shape as FILQ (§53), which also runs\n"
             "to a pole sited just past the end of its range.\n"
             "THERE IS NO FADE-IN. §31 assumed the delay was followed by a\n"
             "ramp and that its detector measured the two as a sum. Measured\n"
             "two ways here -- a 5%-of-final threshold, and a straight line\n"
             "fitted to the rising edge and extrapolated back to zero -- the\n"
             "estimates agree to -0.010 +/- 0.016 s over thirteen settings.\n"
             "The vibrato starts abruptly, so delay is the whole story.\n"
             "The 0.187 s the detector reports at LFODEL 0 is its own latency,\n"
             "not the machine's: it is the swing window, and subtracting it is\n"
             "justified because fitting the offset freely returns 0.188 s.",
        endpoints={0: "no delay"},
    ),
    ("program", "PANPOS"): Scale(
        "program", "PANPOS", "dB", "pan", 1.2124, -0.54, (-45, 45), 0.9996,
        bounds="the last five units each side approach a hard mute, where a ratio of\ntwo levels stops being meaningful; both are separately known.",
        note="constant-power law; +/-50 is hard left/right",
        endpoints={-50: "hard left", 50: "hard right"},
    ),
}


def scale_for(region: str, param: str) -> Optional[Scale]:
    return SCALES.get((region, param))


def to_physical(region: str, param: str, value: float):
    """``(physical, unit, exact)``, or None where no law was measured.

    ``exact`` is False when *value* lies outside the measured range, in which
    case the number is an extrapolation and a caller should mark it as one.
    """
    s = scale_for(region, param)
    if s is None:
        return None
    try:
        return s.value_to_physical(value), s.unit, s.within(value)
    except (ValueError, OverflowError):
        return None


def from_physical(region: str, param: str, physical: float):
    """The parameter value that produces *physical*, and whether it is in range.

    Returns ``(value, exact)`` with *value* rounded and clamped to the
    parameter's own limits, or None where no law was measured.
    """
    s = scale_for(region, param)
    if s is None:
        return None
    try:
        raw = s.physical_to_value(physical)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    if not math.isfinite(raw):
        return None
    return int(round(raw)), s.within(raw)


def parse_quantity(text: str):
    """``("500Hz")`` -> ``(500.0, "Hz")``; None when *text* carries no unit.

    A plain number returns None rather than a quantity, so a raw parameter
    value and a physical one can never be confused: ``50`` is the value 50,
    ``50Hz`` is fifty hertz.
    """
    stripped = str(text).strip().replace(" ", "").lower()
    for suffix, (unit, factor) in _SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix):
            head = stripped[: -len(suffix)]
            try:
                return float(head) * factor, unit
            except ValueError:
                return None
    return None


def value_from_quantity(region: str, param: str, text: str):
    """Turn ``"500Hz"`` into a parameter value for *param*.

    Returns ``(value, exact)``. Raises ValueError when the parameter has no
    measured law, or when the unit does not match the one it was measured in --
    an editor must not silently accept ``FILFRQ 200ms``.
    """
    parsed = parse_quantity(text)
    if parsed is None:
        raise ValueError(f"{text!r} carries no unit")
    quantity, unit = parsed
    s = scale_for(region, param)
    if s is None:
        raise ValueError(
            f"{param} has no measured scale; give a raw value in "
            f"{param}'s own units"
        )
    if unit != s.unit:
        raise ValueError(
            f"{param} was measured in {s.unit}, not {unit}"
        )
    got = from_physical(region, param, quantity)
    if got is None:
        raise ValueError(f"{quantity} {unit} is not reachable for {param}")
    return got


def describe(region: str, param: str, value: float) -> str:
    """A short physical rendering, e.g. ``~4.2 kHz`` or ``~363 ms``.

    Returns "" when nothing was measured for this parameter. An extrapolated
    value is prefixed with ``?`` so a reader can see the difference between a
    number the machine was measured to produce and one this module inferred.
    """
    s = scale_for(region, param)
    if s is None:
        return ""
    known = s.endpoints.get(int(value)) if float(value).is_integer() else None
    if known:
        return known
    got = to_physical(region, param, value)
    if got is None:
        return ""
    physical, unit, exact = got
    mark = "" if exact else "?"
    if s.provisional:
        # Never let an unsettled meaning read like a finished one.
        mark = "!" + mark

    if unit == "Hz":
        text = (f"{physical / 1000:.2f} kHz" if physical >= 1000
                else f"{physical:.0f} Hz" if physical >= 10
                else f"{physical:.2f} Hz")
    elif unit == "s":
        text = (f"{physical * 1000:.0f} ms" if physical < 1
                else f"{physical:.2f} s")
    elif unit == "dB":
        text = f"{physical:+.1f} dB"
        if s.kind == "reso":
            # Q is the number that says what the resonance SOUNDS like; the
            # dB alone reads like a volume and this field is not one.
            text += f" (Q {s.resonance_q(value):.1f})"
    elif unit == "cents":
        text = f"{physical:+.1f} cents"
    elif unit == "%":
        text = f"{physical:.0f}%"
    elif unit in ("dB/s", "u/s"):
        text = f"{physical:.4g} {unit}"
    else:
        text = f"{physical:.3g} {unit}"
    return f"{mark}~{text}"
