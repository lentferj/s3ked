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
"""LFO -> filter-1 frequency: the rail, in cents.  Item 4 of mpc2emu's list.

`MODSFILT1` (program 84) selects the source -- LFO1 is 7, LFO2 is 8 -- and
`MODVFILT1` (keygroup 151) is the amount.  The depth is the LFO's own:
`LFODEP` for LFO1, `PANDEP` for LFO2.  §173 found the analogous LOUDNESS route
to be a PRODUCT of depth and amount, so a product is the hypothesis here.

THREE THINGS THIS MEASURES THAT A STATIC SWEEP CANNOT, each because of a
specific failure already paid for:

1. TIME-RESOLVED CORNER.  `calibrate.py mod-filter-noise` sweeps the same
   amount with `MODSFILT1` = velocity, where the corner is static for a fixed
   velocity, and averages a whole spectrum per setting. An LFO source moves
   the corner DURING the note, so that average reports the mean and not the
   excursion -- which is the quantity. Hence short windows and a corner per
   window.

2. WINDOW SENSITIVITY, REPORTED NOT ASSUMED.  Two projects derive the filter-2
   rail as ~230 +/- 8 and 216.6 cents/unit, and the thing that moves it is a
   baseline-window position -- a property of the ANALYSIS. So every run
   recomputes at several window sizes and prints the spread. If the answer
   moves with the window, the number is the instrument's and the run says so
   instead of picking one.

3. A BOUND CHECK.  §265 saturated at 30.3 dB invisibly: sweeping `MODVPAN1`
   25/40/50 returned 30.312/30.322/30.316 while the true excursion grew to
   47.8 dB, and §181's published 29.75 dB turned out to be that same ceiling.
   So the amount is swept and the excursion must keep growing; a plateau means
   the statistic stopped reporting before the machine did.

Excursion is a 5th-to-95th percentile of the per-window corner, not max-minus-
min: §189's rule that a margin taken at the extreme is reporting the tail.

WRITES PARAMETERS.  Every field touched is snapshotted and restored with a
verified read-back.  Nothing is written to disk or card.
"""

import argparse
import math
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked/probes")

import numpy as np
import rtmidi

import measure as ms
from jcap import Capture
from s3kconnect import connect

SOURCES = ("system:capture_13", "system:capture_14")
# jcap.resolve_sources maps these onto whatever the server actually
# publishes and REFUSES if it cannot -- this box moved from jackd to
# PipeWire on 2026-09-26 and `system:*` ceased to exist, while
# connect() went on succeeding against a substituted input.
LFO1, LFO2 = 7, 8

# The state the corner measurement needs, from calibrate.py's own
# mod-filter-noise prepare block -- every line there was paid for.
PREPARE = (
    ("program", "OUTPUT", 0),
    ("program", "PANPOS", 0),
    ("program", "PRLOUD", 70),      # 85 clipped the interface; a clipped peak
    ("program", "V_LOUD", 0),       # is manufactured spectrum, not a corner
    ("keygroup", "K_FREQ", 0),
    ("keygroup", "VFREQ1", 0),
    ("keygroup", "MODVFILT2", 0),
    ("keygroup", "MODVFILT3", 0),
    ("program", "SPFILT", 0),
    ("keygroup", "VLOUD1", 0),
    ("keygroup", "FILFRQ", 70),
    # Envelope 1 held wide open. Dropped from the first draft of this block and
    # put back before the first run: a note that decays across a 14 s hold puts
    # a slow ramp into the band-ratio series. Most of that lands below the
    # 0.15 Hz cut in `coherent`, so it would not have destroyed the fit -- but
    # "mostly excluded" is not a reason to leave a known signal in the data
    # when zeroing eight fields removes it. (calibrate.py's _ENV1_OPEN.)
    ("keygroup", "ATTAK1", 0),
    ("keygroup", "DECAY1", 0),
    ("keygroup", "SUSTN1", 99),
    ("keygroup", "RELSE1", 0),
    ("keygroup", "V_ATT1", 0),
    ("keygroup", "V_REL1", 0),
    ("keygroup", "O_REL1", 0),
    ("keygroup", "K_DAR1", 0),
)


def band_ratio(mono, sr, n_fft, lo=(80.0, 200.0), hi=(2500.0, 6000.0)):
    """High-band / low-band energy ratio in dB, one value per INDEPENDENT window.

    Not a per-window corner frequency. That was the first design and it does
    not work: a single FFT of noise has ~100% per-bin variance, so the corner
    estimate jitters enormously and a 5th-95th percentile of it reports the
    jitter. Measured on a synthetic one-pole swept a known amount -- a STATIC
    filter, true excursion 0 cents, came back as **2883 cents**. A band ratio
    integrates hundreds of bins instead, and its residual noise is random
    while the modulation is coherent, so the fit at the LFO rate averages the
    noise down by sqrt(N).

    WINDOWS MUST NOT OVERLAP. With hop = n_fft/4 the windows share 75% of
    their samples, the noise becomes correlated, and the coherent floor rose
    from 40 to 180 cents with the strongest component landing at 2.2 Hz
    instead of the LFO's 0.95. Independent windows, fewer points, lower floor.
    """
    f = np.fft.rfftfreq(n_fft, 1.0 / sr)
    ml = (f >= lo[0]) & (f <= lo[1])
    mh = (f >= hi[0]) & (f <= hi[1])
    out = []
    for k in range(len(mono) // n_fft):
        P = np.abs(np.fft.rfft(mono[k * n_fft:(k + 1) * n_fft] * np.hanning(n_fft))) ** 2
        out.append(10.0 * np.log10(P[mh].mean() / max(P[ml].mean(), 1e-30)))
    return np.array(out, float), n_fft / sr


def coherent(series, hop, target_hz):
    """Peak-to-peak amplitude at `target_hz`, plus the strongest component.

    Shared with probes/pandepgate.py, where it was validated against
    constructed signals and against the 0.400 Hz rig-wander trap.
    """
    x = np.asarray(series, float)
    x = x - x.mean()
    n = len(x)
    t = np.arange(n) * hop
    w = np.hanning(n)
    gain = w.sum() / 2.0
    amp = abs((x * w * np.exp(-2j * np.pi * target_hz * t)).sum()) / gain
    spec = np.abs(np.fft.rfft(x * w)) / gain
    freqs = np.fft.rfftfreq(n, hop)
    keep = freqs > 0.15
    k = int(np.argmax(spec[keep]))
    return 2.0 * amp, 2.0 * spec[keep][k], float(freqs[keep][k])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--program", type=int, default=0, help="RPLIST index")
    ap.add_argument("--keygroup", type=int, default=0)
    ap.add_argument("--lfo", type=int, choices=(1, 2), default=1)
    ap.add_argument("--rate", type=int, default=8,
                    help="LFORAT/PANRAT; low so each half-cycle outlasts a window")
    ap.add_argument("--depth", type=int, default=99)
    ap.add_argument("--amounts", default="0,5,10,20,35,50")
    ap.add_argument("--hold", type=float, default=14.0,
                    help="14 s gives a ~40 cent floor; 6 s gives 162")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--channel", type=int, default=None,
                    help="MIDI channel; defaults to the program's own PMCHAN, "
                         "because CALNOISE puts its six programs on 0..5 and the "
                         "channel is what selects which one sounds")
    ap.add_argument("--velocity", type=int, default=100)
    ap.add_argument("--windows", default="2048,4096,8192")
    ap.add_argument("--filfrq", type=int, default=70,
                    help="static centre. 60 put the centre at 580 Hz, only 643 "
                         "cents above a 400 Hz low band, so every excursion over "
                         "~1286 cents swept the corner INTO the baseline band")
    ap.add_argument("--lo-band", default="80,200")
    ap.add_argument("--hi-band", default="2500,6000")
    ap.add_argument("--allow-write", action="store_true",
                    help="required; without it the run only prints its plan")
    args = ap.parse_args()

    LO = tuple(float(x) for x in args.lo_band.split(","))
    HI = tuple(float(x) for x in args.hi_band.split(","))
    depth_field = "LFODEP" if args.lfo == 1 else "PANDEP"
    rate_field = "LFORAT" if args.lfo == 1 else "PANRAT"
    src = LFO1 if args.lfo == 1 else LFO2
    amounts = [int(x) for x in args.amounts.split(",")]
    windows = [int(x) for x in args.windows.split(",")]

    touch = [("program", "MODSFILT1"), ("program", depth_field),
             ("program", rate_field), ("keygroup", "MODVFILT1")] + \
            [(r, n) for r, n, _ in PREPARE]

    print("LFO%d -> filter 1.  source %d, depth %s=%d, rate %s=%d, amounts %s"
          % (args.lfo, src, depth_field, args.depth, rate_field, args.rate, amounts))
    print("windows: %s   note %d vel %d hold %.1fs" % (windows, args.note, args.velocity, args.hold))
    print("fields touched (all snapshotted): %s"
          % ", ".join("%s.%s" % t for t in touch))
    if not args.allow_write:
        print("\n--allow-write not given: plan only, nothing sent.")
        return 0

    br = connect()
    kg = dict(keygroup=args.keygroup)
    snap = {}
    for region, name in touch:
        snap[(region, name)] = br.get_parameter((region, name), args.program,
                                                **(kg if region == "keygroup" else {}))
    print("\nSNAPSHOT: " + ", ".join("%s=%s" % (n, v) for (_, n), v in snap.items()))

    chan = args.channel if args.channel is not None else \
        br.get_parameter(("program", "PMCHAN"), args.program)
    # RAW, for the same reason as pandepgate: PRGNUM carries
    # display_offset=1 (§267) and select_program_number wants the stored
    # 0-based number, not the one the panel shows.
    prgnum = br.get_header_bytes("program", args.program, 15, 1)[0]
    print("program %d (%s): PRGNUM %d, MIDI channel %d -- selected before each capture"
          % (args.program, br.program_list()[args.program], prgnum, chan))

    out = rtmidi.MidiOut()
    out.open_port([k for k, n in enumerate(out.get_ports())
                   if "M4U XT" in n and "MIDI 1" in n][0])

    SILENT_DBFS = -60.0

    def capture(tag=""):
        """Returns (mono, sr, level_dbfs) and REFUSES a silent capture.

        The first version of this probe returned only the audio, and its run on
        2026-09-26 could not afterwards be told from a run against a wedged
        JACK server -- a hung capture and a silent one are the same shape in the
        data, and a band ratio of a noise floor is flat by construction, which
        is indistinguishable from a filter that is not moving. `pandepgate.py`
        prints a level beside every swing for exactly this reason and this file
        was written without carrying it over.

        So the level is measured, printed, and a capture below SILENT_DBFS
        raises rather than being analysed. A refusal costs a re-run; a silent
        capture analysed as data costs a published rail that was never there.
        """
        # SELECT THE PROGRAM BEFORE EVERY CAPTURE. Both halves are required and
        # each alone is silent: with PRGNUM 122 selected, channel 0 gives
        # -96.2 dBFS and its own PMCHAN 2 gives -20.2; and without selecting at
        # all, PMCHAN 2 is also -96.2 because the machine's selected program was
        # 3 -- nothing to do with this volume. Run 1 of this probe wrote 23
        # parameters correctly to program index 2 and recorded fourteen seconds
        # of a program that was never addressed. pandepgate.py selects before
        # every capture; this file did not, which is the second lesson from
        # §265 it was built without.
        br.select_program_number(prgnum)
        time.sleep(0.35)
        with Capture(sources=SOURCES, name="s3ked-lfofilt") as cap:
            cap.start(); time.sleep(0.30)
            out.send_message([0x90 | chan, args.note, args.velocity])
            time.sleep(args.hold)
            out.send_message([0x80 | chan, args.note, 0])
            time.sleep(0.25)
            ch = cap.stop_channels()
            xr, ov = cap.xruns, cap.overflows
        if xr or ov:
            print("   !! xruns=%d overflows=%d -- capture is not contiguous" % (xr, ov))
        if len(ch) != 2:
            raise RuntimeError("capture returned %d channels, expected 2" % len(ch))
        sr = 48000
        mono = (np.asarray(ch[0], float) + np.asarray(ch[1], float)) / 2.0
        mono = mono[int(0.6 * sr):] / 32768.0
        lvl = 20.0 * np.log10(max(np.sqrt((mono ** 2).mean()), 1e-12))
        if lvl < SILENT_DBFS:
            raise RuntimeError(
                "capture %s is SILENT at %.1f dBFS (floor %.0f). Either nothing "
                "sounded or the JACK client got no audio -- both look identical "
                "in a band ratio. Not analysing it."
                % (tag or "?", lvl, SILENT_DBFS))
        return mono, sr, lvl

    try:
        for region, name, val in PREPARE:
            br.set_parameter((region, name), args.program, val,
                             **(kg if region == "keygroup" else {}))
        # ---- STAGE 1: static self-calibration, LFO off -----------------
        # Sweep FILFRQ and record, for each setting, BOTH the band ratio and
        # the corner in Hz taken from the whole-capture averaged spectrum --
        # which is where measure.corner_frequency is reliable, and carries its
        # own guard for a reference band that has slid into the stopband.
        br.set_parameter(("keygroup", "MODVFILT1"), args.program, 0, **kg)
        br.set_parameter(("program", depth_field), args.program, 0)
        br.set_parameter(("keygroup", "FILFRQ"), args.program, 99, **kg)
        ref_mono, sr, ref_lvl = capture("reference FILFRQ 99")
        print("  reference level %.1f dBFS" % ref_lvl)
        ref_spec = {w: ms.spectrum(ref_mono, sr, skip_s=0.0, n_fft=w)[1] for w in windows}

        print("\nSTAGE 1  static calibration, LFO off")
        print("%7s | %10s | %8s | %s" % ("FILFRQ", "corner Hz", "lvl dBFS",
                                   "  ".join("%11s" % ("ratio %d" % w) for w in windows)))
        cal = []
        for ff in (40, 50, 60, 70, 80):
            br.set_parameter(("keygroup", "FILFRQ"), args.program, ff, **kg)
            mono, sr, lvl = capture("FILFRQ %d" % ff)
            f, mag = ms.spectrum(mono, sr, skip_s=0.0, n_fft=8192)
            hz = ms.corner_frequency(f, mag, ref_lo=80.0, ref_hi=200.0,
                                     reference=ref_spec[8192])
            ratios = {w: float(np.median(band_ratio(mono, sr, w, LO, HI)[0])) for w in windows}
            cal.append((ff, hz, ratios))
            print("%7d | %10.1f | %8.1f | %s"
                  % (ff, hz, lvl, "  ".join("%11.2f" % ratios[w] for w in windows)))

        slope = {}
        for w in windows:
            good = [(h, r[w]) for _, h, r in cal if np.isfinite(h) and h > 0]
            if len(good) < 3:
                continue
            cents = np.array([1200 * np.log2(h / good[0][0]) for h, _ in good])
            dbs = np.array([d for _, d in good])
            m, b = np.polyfit(cents, dbs, 1)
            r = float(np.corrcoef(cents, dbs)[0, 1])
            slope[w] = m
            print("  n_fft %4d: %.6f dB per cent  (r=%.5f, 1 dB = %.0f cents)"
                  % (w, m, r, 1.0 / m if m else float("nan")))
        if not slope:
            print("  calibration failed -- corner unmeasurable; aborting before the sweep")
            return 1

        # ---- STAGE 2: LFO on, sweep the amount -------------------------
        br.set_parameter(("keygroup", "FILFRQ"), args.program, args.filfrq, **kg)
        br.set_parameter(("program", "MODSFILT1"), args.program, src)
        br.set_parameter(("program", rate_field), args.program, args.rate)
        br.set_parameter(("program", depth_field), args.program, args.depth)
        rate_hz = (0.11867 if args.lfo == 1 else 0.11880) * args.rate
        print("\nSTAGE 2  LFO%d at %s=%d -> %.3f Hz, depth %s=%d"
              % (args.lfo, rate_field, args.rate, rate_hz, depth_field, args.depth))
        print("%6s %7s | %s" % ("amount", "lvl dB",
                            "  ".join("%18s" % ("n_fft %d" % w) for w in windows)))
        rows = []
        for amt in amounts:
            br.set_parameter(("keygroup", "MODVFILT1"), args.program, amt, **kg)
            rb = br.get_parameter(("keygroup", "MODVFILT1"), args.program, **kg)
            assert rb == amt, "write not acknowledged: asked %d got %d" % (amt, rb)
            mono, sr, lvl = capture("amount %d" % amt)
            cells, by_w = [], {}
            for w in windows:
                if w not in slope:
                    cells.append("%18s" % "-"); continue
                ser, hop = band_ratio(mono, sr, w, LO, HI)
                sw, pk, pf = coherent(ser, hop, rate_hz)
                cents = sw / slope[w]
                by_w[w] = (cents, pf)
                cells.append("%7.0f c @%5.2fHz" % (cents, pf))
            rows.append((amt, by_w))
            print("%6d %7.1f | %s" % (amt, lvl, "  ".join(cells)))

        print("\nWINDOW SENSITIVITY -- disagreement here means the number is the instrument's")
        for amt, by_w in rows:
            v = [c for c, _ in by_w.values()]
            if len(v) > 1 and max(v) > 0:
                print("  amount %2d: %.0f..%.0f cents, spread %.0f (%.0f%% of median)"
                      % (amt, min(v), max(v), max(v) - min(v),
                         100 * (max(v) - min(v)) / max(np.median(v), 1e-9)))

        # IN-BAND GUARD. Run 2 returned 1915, 3415, 4286, 3144, 2554 cents for
        # amounts 5..50 -- rising then FALLING -- and the fall was entirely the
        # instrument: at a 580 Hz centre with a 150-400 Hz baseline, every
        # excursion over ~1286 cents sweeps the corner down INTO the baseline
        # band, so the reference is in the stopband for part of each cycle and
        # the ratio folds. That is measure.corner_frequency's documented failure
        # in a new coordinate, and §227's baseline-window problem again. A
        # folded number is not a small number, so it must not be reported as one.
        centre = next((h for ff, h, _ in cal if ff == args.filfrq and np.isfinite(h)), None)
        if centre:
            # BOTH DIRECTIONS. The first version of this guard computed only
            # the room BELOW the centre and reported a 6156-cent limit for a
            # case whose real limit was 2589 -- so it passed the two points that
            # had swept the corner up into the high band, which is the very
            # failure it was added to catch. A guard written against a
            # one-sided error inherited its one-sidedness.
            down = 2400.0 * math.log2(centre / LO[1])
            up = 2400.0 * math.log2(HI[0] / centre)
            limit = min(down, up)
            print("\nIN-BAND LIMIT: centre %.0f Hz, %.0f cents of room below the "
                  "%.0f Hz baseline top and %.0f above the %.0f Hz high band "
                  "=> binding limit %.0f cents"
                  % (centre, down, LO[1], up, HI[0], limit))
            for amt, by_w in rows:
                v = [c for c, _ in by_w.values()]
                if v and max(v) > limit:
                    print("  amount %2d: %.0f cents EXCEEDS the limit -- not a measurement"
                          % (amt, max(v)))

        print("\nBOUND CHECK -- the excursion must keep growing with the amount")
        w0 = windows[len(windows) // 2]
        seq = [(a, by_w[w0][0]) for a, by_w in rows if w0 in by_w]
        for (a1, v1), (a2, v2) in zip(seq, seq[1:]):
            flag = "   <-- PLATEAU: statistic may have stopped before the machine" \
                   if v2 <= v1 * 1.02 else ""
            print("  %2d -> %2d : %.0f -> %.0f cents%s" % (a1, a2, v1, v2, flag))
        print("\nFLOOR: ~40 cents at a 14 s hold. Treat anything under ~150 as zero.")
    finally:
        for (region, name), val in snap.items():
            br.set_parameter((region, name), args.program, val,
                             **(kg if region == "keygroup" else {}))
        bad = [n for (r, n), v in snap.items()
               if br.get_parameter((r, n), args.program,
                                   **(kg if r == "keygroup" else {})) != v]
        print("\nRESTORE: %s" % ("VERIFIED, all %d fields" % len(snap) if not bad
                                 else "*** FAILED on %s ***" % bad))
        out.send_message([0x80 | chan, args.note, 0])
        out.close_port()
    return 0


if __name__ == "__main__":
    sys.exit(main())
