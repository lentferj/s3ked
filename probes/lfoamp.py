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
"""LFO2 -> loudness: does it reach, and is it a product with PANDEP?

LFO2 is assignable source 8 (§52).  Loudness slot 1 is `MODSAMP1` (program 79)
with amount `MODVAMP1` (92).  §173 measured LFO1's loudness route as a product,
`one-sided swing_dB = 0.010068 * LFODEP * MODVAMP1`, so a product is the
hypothesis -- and §173's own warning governs the method: a coefficient taken at
one depth and applied flat over-reads by `99/depth`, **9.9x at depth 10**.
Linearity in one variable proves nothing; only equal-product equivalence does
(§173, and again in §270).

THE CONFOUND, which is why §265 had to come first.  LFO2's depth is `PANDEP`,
and §265 measured that `PANDEP` also GATES the pan route.  So one field moves
two destinations, and a swing in the stereo image would read as a swing in
level.  Two defences, because one is not enough:

  * every pan amount is set to zero AND ASSERTED at zero before any capture;
  * the statistic is the MONO SUM, where a pan modulation cancels and a
    loudness modulation does not.  The pan swing is reported beside the level
    swing in every row, so if the pan column moves the level column is
    suspect and the run says so rather than leaving it to the analysis.

Carries the four guards the preceding runs paid for:
  * a LEVEL per capture, refusing below -60 dBFS -- a silent capture and a
    working one are the same shape in any ratio statistic (§270 run 1);
  * PROGRAM SELECTION before every capture -- unselected, the machine plays
    whatever was selected, and §270 run 1 wrote 23 fields to a program that
    was never addressed;
  * the SNAPSHOT PRINTED BEFORE ANY WRITE, because a probe SIGKILLed in a
    native call never runs its `finally` and the log is then the only record
    of what to put back (§271);
  * a TROUGH check -- a deep tremolo drives the envelope toward the noise
    floor, and a swing that stops growing because the trough hit the floor is
    the instrument reporting itself (§265's 30.3 dB ceiling, §270's band edge).

Capture ports resolve through `jcap.resolve_sources`, which refuses a
substituted input; `system:*` ceased to exist when this box moved to PipeWire
and `connect()` went on succeeding against the wrong node (§271).
"""

import argparse
import sys
import time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked/probes")

import numpy as np
import rtmidi

from jcap import Capture
from pandepgate import coherent_swing_db
from s3kconnect import connect

SOURCES = ("system:capture_13", "system:capture_14")
HOP, SKIP, SILENT = 0.010, 0.60, -60.0

PREPARE = (
    ("program", "MODSAMP1", 8),  # loudness slot 1 <- LFO2
    ("program", "MODSPAN1", 8),  # pan source too, but its AMOUNT stays 0
    ("program", "MODVPAN1", 0),
    ("program", "MODVPAN2", 0),
    ("program", "MODVPAN3", 0),
    ("program", "PANPOS", 0),
    ("program", "PRLOUD", 70),  # 85 clipped the interface (calibrate.py)
    ("program", "V_LOUD", 0),
    ("program", "LFODEP", 0),  # LFO1 silent, so only LFO2 is in play
    ("program", "MODVAMP2", 0),
    ("keygroup", "MODVAMP3", 0),
    ("keygroup", "VLOUD1", 0),
    ("keygroup", "ATTAK1", 0),
    ("keygroup", "DECAY1", 0),
    ("keygroup", "SUSTN1", 99),
    ("keygroup", "RELSE1", 0),
    ("keygroup", "V_ATT1", 0),
)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--program", type=int, default=2)
    ap.add_argument("--keygroup", type=int, default=0)
    ap.add_argument(
        "--panrat", type=int, default=28, help="LFO2 rate; 28 -> 3.33 Hz, ~46 cycles in a 14 s hold"
    )
    ap.add_argument("--depth", type=int, default=20, help="PANDEP for the amount sweep")
    ap.add_argument("--amounts", default="0,2,5,10,20,35,50")
    ap.add_argument("--hold", type=float, default=14.0)
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--velocity", type=int, default=100)
    ap.add_argument("--allow-write", action="store_true")
    args = ap.parse_args()

    kg = dict(keygroup=args.keygroup)
    rate = 0.11880 * args.panrat  # §260's corrected law
    amounts = [int(x) for x in args.amounts.split(",")]
    touch = [(r, n) for r, n, _ in PREPARE] + [
        ("program", "PANDEP"),
        ("program", "PANRAT"),
        ("program", "MODVAMP1"),
    ]
    print("LFO2 -> loudness. source 8, depth PANDEP, amount MODVAMP1 (92)")
    print(
        "PANRAT %d -> %.3f Hz; sweep at PANDEP %d over amounts %s"
        % (args.panrat, rate, args.depth, amounts)
    )
    print("fields touched: %s" % ", ".join("%s.%s" % t for t in touch))
    if not args.allow_write:
        print("\n--allow-write not given: plan only, nothing sent.")
        return 0

    import atexit as _atexit
    import signal as _signal

    br = None
    out = None
    cap = None
    snap = {}
    _previous = {}

    def _restore_best_effort():
        if br is None or not snap:
            return
        for t, v in snap.items():
            try:
                br.set_parameter(t, args.program, v, **(kg if t[0] == "keygroup" else {}))
            except Exception:
                pass

    def _bail(signum, _frame):
        for _s, _h in _previous.items():
            try:
                _signal.signal(_s, _h)
            except (ValueError, OSError):
                pass
        raise KeyboardInterrupt(f"signal {signum}")

    _atexit.register(_restore_best_effort)
    for _sig in (_signal.SIGTERM, getattr(_signal, "SIGHUP", None), _signal.SIGINT):
        if _sig is None:
            continue
        try:
            _previous[_sig] = _signal.signal(_sig, _bail)
        except (ValueError, OSError):
            pass
    try:
        br = connect()
        snap = {
            t: br.get_parameter(t, args.program, **(kg if t[0] == "keygroup" else {}))
            for t in touch
        }
        # PRINTED BEFORE ANY WRITE: this line is the recovery path if the process
        # dies where `finally` cannot run (§271).
        print("\nSNAPSHOT %s" % {n: v for (_, n), v in snap.items()}, flush=True)

        prgnum = br.get_header_bytes("program", args.program, 15, 1)[0]
        chan = br.get_parameter(("program", "PMCHAN"), args.program)
        out = rtmidi.MidiOut()
        out.open_port(
            [k for k, n in enumerate(out.get_ports()) if "M4U XT" in n and "MIDI 1" in n][0]
        )
        # One persistent Capture for the whole run (m3): per-capture
        # create/destroy churn is what wedges the JACK server.
        cap = Capture(sources=SOURCES, name="s3ked-lfoamp")
        sr = int(cap.samplerate)
        sk = int(SKIP * sr)
    except Exception:
        _restore_best_effort()
        for _c in (cap, out, br):
            try:
                if _c is not None:
                    _c.close() if _c is br or _c is cap else _c.close_port()
            except Exception:
                pass
        raise

    def capture(tag):
        br.select_program_number(prgnum)
        time.sleep(0.35)
        cap.start()
        time.sleep(0.30)
        out.send_message([0x90 | chan, args.note, args.velocity])
        time.sleep(args.hold)
        out.send_message([0x80 | chan, args.note, 0])
        time.sleep(0.25)
        ch = cap.stop_channels()
        xr, ov = cap.xruns, cap.overflows
        if xr or ov:
            print("   !! xruns=%d overflows=%d" % (xr, ov))
        # Sample rate from the capture client (M6), never a constant.
        sr = int(cap.samplerate)
        sk = int(SKIP * sr)
        L = np.asarray(ch[0][sk:], float) / 32768.0
        R = np.asarray(ch[1][sk:], float) / 32768.0
        mono = (L + R) / 2.0
        lvl = 20 * np.log10(max(np.sqrt((mono**2).mean()), 1e-12))
        if lvl < SILENT:
            raise RuntimeError("capture %s SILENT at %.1f dBFS -- not analysing" % (tag, lvl))
        n = int(sr * HOP)
        m = len(mono) // n
        rms = lambda x: np.sqrt((x[: m * n].reshape(m, n) ** 2).mean(axis=1))
        env = 20 * np.log10(rms(mono) + 1e-12)
        # Right-minus-left, matching measure.balance_db (positive = right).
        bal = 20 * np.log10((rms(R) + 1e-12) / (rms(L) + 1e-12))
        return lvl, env, coherent_swing_db(env, HOP, rate), coherent_swing_db(bal, HOP, rate)[0]

    rows = []
    try:
        for r, n, v in PREPARE:
            br.set_parameter((r, n), args.program, v, **(kg if r == "keygroup" else {}))
        br.set_parameter(("program", "PANRAT"), args.program, args.panrat)
        for f in ("MODVPAN1", "MODVPAN2", "MODVPAN3"):
            got = br.get_parameter(("program", f), args.program)
            assert got == 0, "%s is %s, not 0 -- pan would confound this" % (f, got)
        print("PAN AMOUNTS ASSERTED ZERO on all three slots", flush=True)

        def run(dep, amt, label):
            br.set_parameter(("program", "PANDEP"), args.program, dep)
            br.set_parameter(("program", "MODVAMP1"), args.program, amt)
            assert br.get_parameter(("program", "MODVAMP1"), args.program) == amt
            lvl, env, (sw, pk, pf), bsw = capture("%s %d/%d" % (label, dep, amt))
            trough = float(np.percentile(env, 5))
            rows.append(
                dict(
                    dep=dep,
                    amt=amt,
                    prod=dep * amt,
                    lvl=lvl,
                    sw=sw,
                    pf=pf,
                    bsw=bsw,
                    trough=trough,
                    label=label,
                )
            )
            print(
                "%-5s %5d %6d %8d | %7.1f %9.3f %8.2f | %8.3f %9.1f"
                % (label, dep, amt, dep * amt, lvl, sw, pf, bsw, trough),
                flush=True,
            )

        print(
            "\n%-5s %5s %6s %8s | %7s %9s %8s | %8s %9s"
            % (
                "what",
                "DEPTH",
                "AMOUNT",
                "product",
                "lvl dB",
                "swing dB",
                "peak Hz",
                "pan sw",
                "trough dB",
            ),
            flush=True,
        )
        run(0, 50, "null")  # depth 0, amount set   -- §15: inert
        run(50, 0, "null")  # amount 0, depth set   -- §15: inert
        for a in amounts:
            run(args.depth, a, "sweep")
        for d, a in ((20, 20), (40, 10), (10, 40), (50, 8), (8, 50)):
            run(d, a, "eqprod")
    finally:
        for _s, _h in _previous.items():
            try:
                _signal.signal(_s, _h)
            except (ValueError, OSError):
                pass
        if br is not None:
            for t, v in snap.items():
                try:
                    br.set_parameter(t, args.program, v, **(kg if t[0] == "keygroup" else {}))
                except Exception as exc:
                    print("  !! restore %s.%s: %s" % (t[0], t[1], exc), flush=True)
            try:
                bad = [
                    t
                    for t, v in snap.items()
                    if br.get_parameter(t, args.program, **(kg if t[0] == "keygroup" else {})) != v
                ]
            except Exception:
                bad = [t for t in snap]
            print(
                "\nRESTORE: %s"
                % (
                    "VERIFIED all %d fields" % len(snap)
                    if not bad
                    else "*** FAILED on %s ***" % bad
                ),
                flush=True,
            )
        if cap is not None:
            try:
                cap.close()
            except Exception:
                pass
        if out is not None:
            try:
                out.send_message([0x80 | chan, args.note, 0])
            except Exception:
                pass
            try:
                out.close_port()
            except Exception:
                pass
        if br is not None:
            try:
                br.close()
            except Exception:
                pass

    eq = [r for r in rows if r["label"] == "eqprod" and r["sw"] > 0]
    if len(eq) > 1:
        v = [r["sw"] for r in eq]
        print("\nEQUAL-PRODUCT (400): %s" % ", ".join("%.2f" % x for x in v))
        print(
            "  spread %.2f dB = %.1f%% of the mean -- §173's criterion"
            % (max(v) - min(v), 100 * (max(v) - min(v)) / np.mean(v))
        )
    print("\nTROUGH CHECK -- a swing that stops growing because the trough hit")
    print("the floor is the instrument, not the machine (§265, §270):")
    for r in rows:
        if r["trough"] < SILENT + 15:
            print(
                "  %s %d/%d: trough %.1f dBFS is within 15 dB of the %g floor"
                % (r["label"], r["dep"], r["amt"], r["trough"], SILENT)
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
