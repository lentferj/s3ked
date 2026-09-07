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
"""Lomb-Scargle periodogram for unevenly sampled data. No scipy here.

PRG 1 sustains only ~0.35 s, so a held note gives 35 frames and no usable
resolution. LFO2TRIG is 0 -- free-running -- so retriggering does NOT reset the
LFO phase, and the modulation is continuous ACROSS notes. Sampling it through
the gaps gives a long baseline from a short sound, but the samples are uneven,
which an FFT cannot take. Lomb-Scargle is built for exactly that.
"""
import numpy as np, math

def lombscargle(t, y, freqs):
    t = np.asarray(t, float); y = np.asarray(y, float)
    y = y - y.mean()
    out = np.empty(len(freqs))
    for k, f in enumerate(freqs):
        w = 2.0 * math.pi * f
        s2 = np.sin(2 * w * t).sum(); c2 = np.cos(2 * w * t).sum()
        tau = math.atan2(s2, c2) / (2 * w)
        wt = w * (t - tau)
        c = np.cos(wt); s = np.sin(wt)
        cc = (c * c).sum(); ss = (s * s).sum()
        yc = (y * c).sum(); ys = (y * s).sum()
        out[k] = 0.5 * ((yc * yc / cc if cc > 0 else 0.0) +
                        (ys * ys / ss if ss > 0 else 0.0))
    return out

def peak(t, y, lo=1.0, hi=15.0, n=4000):
    fr = np.linspace(lo, hi, n)
    P = lombscargle(t, y, fr)
    j = int(np.argmax(P))
    return float(fr[j]), P, fr
