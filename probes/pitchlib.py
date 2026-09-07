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
"""f0 estimation, YIN-style. Validated before use -- see pitchvalidate.py.

Two earlier attempts failed on this material and both failed SILENTLY, giving
confident wrong numbers rather than errors:

  HPS            returns SUB-OCTAVES on harmonically sparse signals. These
                 programs run FILFRQ 14-15, a corner near 22 Hz, so their
                 content is nearly sinusoidal -- exactly the failure case.
                 Pure 1046 Hz sine read as 261 Hz (-2401 cents).
  plain ACF      accurate k36..k84 but octave-errors at k90 (-1200 cents),
                 because r(2T) can exceed r(T).

YIN's cumulative mean normalised difference with an absolute threshold is the
standard fix for exactly that octave error.
"""
import numpy as np, math

def f0_yin(x, sr, fmin=40.0, fmax=3000.0, thresh=0.15):
    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    if x.size < 2048: return None
    tmin = max(2, int(sr / fmax)); tmax = min(int(sr / fmin), x.size // 2 - 1)
    if tmax <= tmin + 2: return None
    n = 1 << int(np.ceil(np.log2(x.size * 2)))
    X = np.fft.rfft(x, n=n)
    r = np.fft.irfft(X * np.conj(X), n=n)[:x.size]
    pw = np.concatenate(([0.0], np.cumsum(x * x)))
    tau = np.arange(tmax + 1)
    m = x.size - tau
    d = (pw[x.size] - pw[tau]) + (pw[m] - pw[0]) - 2 * r[:tmax + 1]
    d[0] = 0.0
    cum = np.cumsum(d[1:])
    dp = np.ones_like(d)
    idx = np.arange(1, tmax + 1)
    dp[1:] = d[1:] * idx / np.maximum(cum, 1e-30)
    cand = None
    for t in range(tmin, tmax):
        if dp[t] < thresh and dp[t] <= dp[t + 1]:
            cand = t; break
    if cand is None:
        seg = dp[tmin:tmax]
        if seg.size == 0: return None
        cand = tmin + int(np.argmin(seg))
    t = cand
    if 1 <= t < tmax - 1:
        a, b, c = dp[t - 1], dp[t], dp[t + 1]
        den = a - 2 * b + c
        if den != 0: t = t + 0.5 * (a - c) / den
    return sr / t if t > 0 else None

def cents(f, ref):
    return 1200.0 * math.log2(f / ref) if f and ref and f > 0 and ref > 0 else None

def key_hz(k):
    return 440.0 * 2 ** ((k - 69) / 12.0)
