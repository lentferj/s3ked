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
"""Does `PANDEP` gate the pan modulation matrix?  RESOLUTION_NOTES §265.

Reads the five programs of `HD3-PANDEP.img` and reports, for each, the
**coherent** stereo-balance swing at LFO2's own rate.

WHY COHERENT AND NOT BROADBAND.  A sibling measured a K2000 program with
nothing routed at 2.20 dB broadband but only 1.71 dB at the pan rate: its
strongest component was the rig's own 0.400 Hz image wander.  A broadband
percentile would have called that a pan.  So this reports three numbers per
program -- the amplitude AT the asked-for frequency, the strongest peak
anywhere, and that peak's frequency -- which lets the result say "the movement
is not where it should be" instead of merely "there is movement".

WRITES NOTHING.  The five programs differ only in `PANDEP` and `MODVPAN1`;
selecting one is a program change, so no parameter is set and nothing needs
restoring.  That is the whole reason the test was built as a disc.
"""

import argparse
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked/probes")

import numpy as np
import rtmidi

from jcap import Capture
from s3kconnect import connect

SOURCES = ("system:capture_13", "system:capture_14")   # proven live 2026-09-25
NOTE = 60
HOP = 0.010                     # 100 Hz balance series; LFO2 is a few Hz
SKIP = 0.60                     # let the attack pass before analysing


def balance_series(left, right, sr, hop=HOP):
    """dB difference between the channels, once per `hop` seconds."""
    n = int(sr * hop)
    m = min(len(left), len(right)) // n
    l = np.asarray(left[:m * n], float).reshape(m, n)
    r = np.asarray(right[:m * n], float).reshape(m, n)
    rl = np.sqrt((l ** 2).mean(axis=1)) + 1e-12
    rr = np.sqrt((r ** 2).mean(axis=1)) + 1e-12
    return 20.0 * np.log10(rl / rr)


def coherent_swing_db(series, hop, target_hz):
    """Peak-to-peak swing at `target_hz`, plus the strongest component.

    A direct DFT at the exact frequency, not an FFT bin, so a rate that falls
    between bins is not attenuated by up to 3.9 dB.
    """
    x = np.asarray(series, float)
    x = x - x.mean()
    n = len(x)
    t = np.arange(n) * hop
    w = np.hanning(n)
    gain = w.sum() / 2.0                      # coherent gain of the window
    amp_at = abs((x * w * np.exp(-2j * np.pi * target_hz * t)).sum()) / gain

    fs = 1.0 / hop
    spec = np.abs(np.fft.rfft(x * w)) / gain
    freqs = np.fft.rfftfreq(n, hop)
    lo = freqs > 0.15                          # ignore DC and very slow drift
    k = np.argmax(spec[lo])
    return 2.0 * amp_at, 2.0 * spec[lo][k], freqs[lo][k]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hold", type=float, default=6.0)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--velocity", type=int, default=100)
    args = ap.parse_args()

    br = connect()
    names = br.program_list()
    rows = []
    for i, nm in enumerate(names):
        prg = br.get_parameter(("program", "PRGNUM"), i)
        rate_unit = br.get_parameter(("program", "PANRAT"), i)
        dep = br.get_parameter(("program", "PANDEP"), i)
        amt = br.get_parameter(("program", "MODVPAN1"), i)
        src = br.get_parameter(("program", "MODSPAN1"), i)
        rows.append(dict(idx=i, name=nm, prgnum=prg, panrat=rate_unit,
                         pandep=dep, modvpan1=amt, modspan1=src))

    # §260's corrected law: rate = 0.11880 * PANRAT Hz.  §52's 0.23708 was
    # 2.002x too high and would put the probe frequency at 6.65 Hz, where a
    # real 3.33 Hz modulation reads as near-zero.
    target = 0.11880 * rows[0]["panrat"]
    print("LFO2 rate from PANRAT %d -> %.3f Hz (§260 law)\n" % (rows[0]["panrat"], target))

    out = rtmidi.MidiOut()
    port = [k for k, n in enumerate(out.get_ports())
            if "M4U XT" in n and "MIDI 1" in n][0]
    out.open_port(port)

    print("%-13s %6s %6s %9s | %9s %9s %9s"
          % ("program", "PANDEP", "MODV", "level dBFS", "swing@f", "peak", "peak Hz"))
    try:
        for row in rows:
            got = []
            for _ in range(args.repeats):
                br.select_program_number(row["prgnum"])
                time.sleep(0.35)
                with Capture(sources=SOURCES, name="s3ked-pandep") as cap:
                    cap.start()
                    time.sleep(0.30)
                    out.send_message([0x90, NOTE, args.velocity])
                    time.sleep(args.hold)
                    out.send_message([0x80, NOTE, 0])
                    time.sleep(0.25)
                    chans = cap.stop_channels()
                    xr, ov = cap.xruns, cap.overflows
                if len(chans) != 2 or xr or ov:
                    print("   !! chans=%d xruns=%d overflows=%d" % (len(chans), xr, ov))
                sr = 48000
                skip = int(SKIP * sr)
                L = np.asarray(chans[0][skip:], float) / 32768.0
                R = np.asarray(chans[1][skip:], float) / 32768.0
                # A SILENT program and a PERFECTLY STEADY one both give a
                # balance series of exactly 0.00: with no audio both channels
                # sit on the 1e-12 floor and 20*log10(1) is 0.  So the level
                # is reported beside the swing -- without it, "PANDEP 0 is
                # silent" cannot be told from "the note never played", and
                # the first run of this probe could not tell them apart.
                lvl = 20 * np.log10(max(np.sqrt((L ** 2).mean()), 1e-12))
                b = balance_series(L, R, sr)
                got.append(coherent_swing_db(b, HOP, target) + (lvl,))
            sw = float(np.median([g[0] for g in got]))
            pk = float(np.median([g[1] for g in got]))
            pf = float(np.median([g[2] for g in got]))
            lv = float(np.median([g[3] for g in got]))
            row.update(swing=sw, peak=pk, peak_hz=pf, level=lv)
            flag = "  <-- SILENT, not steady" if lv < -60 else ""
            print("%-13s %6d %6d %9.1f | %9.2f %9.2f %9.3f%s"
                  % (row["name"], row["pandep"], row["modvpan1"],
                     lv, sw, pk, pf, flag))
    finally:
        out.send_message([0x80, NOTE, 0])
        out.close_port()

    print()
    by = {r["name"]: r for r in rows}
    d99, d50, d0 = by["PD DEP 99"], by["PD DEP 50"], by["PD DEP 0"]
    if d50["swing"] > 0.01:
        print("RATIO 99:50 = %.3f   (product law predicts 1.980)"
              % (d99["swing"] / d50["swing"]))
    print("floor  PD CTRL   %.2f dB     route-absent PD NOMATRIX %.2f dB"
          % (by["PD CTRL"]["swing"], by["PD NOMATRIX"]["swing"]))
    print("PANDEP 0 with the route live: %.2f dB" % d0["swing"])


if __name__ == "__main__":
    main()
