#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# s3ked is free software; you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# s3ked is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""`RELSE1` across its whole range, by two-crossing time.

`RELSE1` is a rate in dB/s and `scales.py` carries §158's law,
``23042.3 * exp(-0.09754 * v)``, fitted over 45..99. §158 recorded that the
sub-45 region was unmeasurable and why -- "at those settings the release is
over in a millisecond or two and what survives above the floor is the rig's
own tail".

§274 measured the whole range and found the recorded reason wrong: there is a
common tail, but it is not the rig's and not `RELSE1`'s, and it does not stop
the release being measured. The blocker was the **statistic**.

Run this:

    .venv/bin/python probes/relse_rate.py --out ~/temp/s3ked-logs/relse

THE STATISTIC, and why it is not a straight line in dB. The release is not
straight in dB -- §158's own peer challenge established that the opening
decade is slow -- so a least-squares line over the top 40 dB reports a rate
that depends on where it was told to stop. This takes instead the interval
between the level passing two fixed levels, both read from the same waveform:

    rate = (L2 - L1) / (t(L2) - t(L1))

which assumes no shape at all, and is **latency-free**: the host's note-off
reaches the machine several milliseconds later by a jitter comparable to the
whole fall at low `RELSE1`, but both crossings come from one waveform so any
offset cancels exactly. It also cannot be dragged by the noise floor, because
neither crossing is near it.

THE FRAME IS THE ONLY KNOB THAT MATTERS, and shorter is worse. The RMS of N
Gaussian samples carries a relative error of 1/sqrt(2N) -- 1.8 dB at 12
samples, 0.89 dB at 48. At the fast end the whole 12 dB interval is one or two
frames long, so a short frame measures its own noise:

    frame    RELSE1 45   RELSE1 20   RELSE1  0
      12 samples   -49 %      -94 %     -99 %
      24 samples   -55 %      -87 %     -99 %
      48 samples    +1 %      +47 %     +4 %      <- 1 ms

The hop is a separate knob and mostly does not matter: replaying the same
captures at hop 0.05 ms and 0.25 ms against a logged 0.5 ms agrees to a median
of **1.6 %**, but reaches **20 %** at `RELSE1` 5, 10 and 15, where the whole
12 dB interval is one or two frames long and a second crossing occasionally
latches onto a noise excursion. Treat a disagreement between hops as the
statistic telling you the interval is too short, not as the hop being wrong.

WHAT IT NEEDS FROM YOU, all of it learned from runs that returned nothing:

* **Only one session drives the sampler.** RAM only: this loads a volume
  (a disc READ), writes keygroup and program bytes, and writes them back.
  It never writes to a volume and never deletes.
* **`--volume` must already be on the card.** Load it yourself and say so.
* **Check the level.** Every capture's sustain is checked against the floor
  and a capture that is not sounding is void, not a low reading.
* **Check the program by reading `PRGNUM` back**, not by trusting the index
  the loader reported. §266's discipline.
* **Save the waveform.** Every capture goes to `.npy` before it is analysed.
  A summary row that outlives its own evidence is how §158's sub-45 diagnosis
  became unfalsifiable -- `rel1/fast.py` wrote the WAVs and they are gone.
"""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "probes"))

import numpy as np

from s3k import params as p
import s3kconnect
from jcap import Capture

INT16 = 20 * math.log10(32768.0)

#: Keygroup bytes this touches. Saved first, written back on every exit path.
KG_FIELDS = ("ATTAK1", "DECAY1", "SUSTN1", "RELSE1", "V_REL1", "O_REL1",
             "K_DAR1", "VLOUD1")
PG_FIELDS = ("LFODEP", "PRLOUD", "MODVAMP1", "MODVAMP2")

#: §158's prepare list, unchanged, so rows stay comparable with §158's.
PREPARE_KG = {"ATTAK1": 0, "DECAY1": 0, "SUSTN1": 99, "V_REL1": 0, "O_REL1": 0,
              "K_DAR1": 0, "VLOUD1": 0}
PREPARE_PG = {"LFODEP": 0, "PRLOUD": 85, "MODVAMP1": 0, "MODVAMP2": 0}

DEFAULT_VALUES = (99, 80, 70, 60, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0)


def envelope(x, sr, frame, hop):
    """RMS envelope via a running sum. Returns (dB, t)."""
    n, h = int(frame * sr), int(hop * sr)
    if len(x) < n:
        return np.zeros(0), np.zeros(0)
    c = np.concatenate(([0.0], np.cumsum(x * x)))
    st = np.arange(0, len(x) - n + 1, h)
    e = np.maximum(np.sqrt((c[st + n] - c[st]) / n), 1e-12)
    return 20 * np.log10(e), st / sr


def crossing_rate(x, sr, sus_db, t_off, frame=0.001, hop=0.00025,
                  l1=3.0, l2=15.0):
    """dB/s from the interval between two level crossings, or nan.

    Returns (rate, t_of_first_crossing_relative_to_note_off).
    """
    db, t = envelope(x, sr, frame, hop)
    after = t >= t_off - 0.05
    a = np.where(after & (db <= sus_db - l1))[0]
    b = np.where(after & (db <= sus_db - l2))[0]
    if len(a) == 0 or len(b) == 0:
        return float("nan"), float("nan")
    t1, t2 = t[a[0]], t[b[0]]
    if t2 <= t1:
        return float("nan"), float("nan")
    return (l2 - l1) / (t2 - t1), float(t1 - t_off)


def law(v, a=23042.3, b=-0.09754):
    """§158's law, and §274's whole-range fit."""
    return a * math.exp(b * v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="/tmp/s3ked-relse",
                    help="where captures and rows.json go")
    ap.add_argument("--volume", default="TC10 NOISE",
                    help="volume already present on the card (a disc read)")
    ap.add_argument("--program", type=int, default=50,
                    help="PRGNUM of the one-keygroup white-noise program")
    ap.add_argument("--note", type=int, default=48)
    ap.add_argument("--velocity", type=int, default=100)
    ap.add_argument("--pre", type=float, default=0.6)
    ap.add_argument("--hold", type=float, default=2.0)
    ap.add_argument("--tail", type=float, default=1.5)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--frame", type=float, default=0.001,
                    help="RMS frame in seconds; 0.001 is the measured value")
    ap.add_argument("--hop", type=float, default=0.00025)
    ap.add_argument("--l1", type=float, default=3.0, help="first crossing, dB down")
    ap.add_argument("--l2", type=float, default=15.0, help="second crossing, dB down")
    ap.add_argument("--values", type=int, nargs="+", default=list(DEFAULT_VALUES))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit; touches no hardware")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    if args.dry_run:
        print("would sweep RELSE1", args.values, "x", args.repeats,
              "repeats, into", args.out)
        print("law check: RELSE1 45 ->", round(law(45), 1), "dB/s (measured 338);"
              " RELSE1 10 ->", round(law(10), 1), "(measured 8000)")
        return 0

    def patient(fn, *a, tries=8, gap=1.4, **kw):
        last = None
        for _ in range(tries):
            try:
                return fn(*a, timeout=12.0, **kw)
            except Exception as e:          # noqa: BLE001 - retry is the point
                last = e
                time.sleep(gap)
        raise last

    kg_off = {n: p.lookup(("keygroup", n)).offset for n in KG_FIELDS}
    pg_off = {n: p.lookup(("program", n)).offset for n in PG_FIELDS}
    pn = p.lookup(("program", "PRGNUM")).offset

    log = open(os.path.join(args.out, "relse_rate.log"), "w",
              encoding="utf-8")

    def say(*t):
        s = " ".join(str(x) for x in t)
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()

    cap = Capture(sources=("system:capture_13", "system:capture_14"),
                  name="s3ked-relse")
    br = s3kconnect.connect(channels=(0,))
    sr = cap.samplerate
    midi = br.out
    say(f"[{time.strftime('%H:%M:%S')}] {br.description}")
    say(f"    capture: {' -> '.join(cap.sources)}   jack {sr} Hz")
    say("    RAM only: loading a volume (a disc read), writing keygroup and")
    say("    program bytes and writing them back. No volume is written.")

    try:
        patient(br.select_device, 1)
        time.sleep(1.0)
        patient(br.select_drive, 4)
        time.sleep(2.0)
        vols = [v.name.rstrip() for v in patient(br.volume_list)]
        if args.volume not in vols:
            say(f"!! volume {args.volume!r} is not on the card. Load it first.")
            say(f"   present: {vols}")
            return 2
        patient(br.select_volume, vols.index(args.volume))
        time.sleep(1.0)
        patient(br.trigger_load, 0)
        for _ in range(40):
            time.sleep(2.0)
            try:
                patient(br.program_list, tries=1)
                break
            except Exception:
                pass
        time.sleep(2.0)
        names = [n.rstrip() for n in patient(br.program_list)]

        # Program selection verified by reading it back, not by trusting an index.
        idx = None
        for i in range(len(names)):
            try:
                if patient(br.get_header_bytes, "program", i, pn, 1)[0] == args.program:
                    idx = i
                    break
            except Exception:
                continue
        if idx is None:
            say(f"!! no resident program reads PRGNUM {args.program}. Stopping "
                f"rather than editing an unknown one.")
            return 3
        say(f"    {args.volume}: {len(names)} programs; PRGNUM {args.program} "
            f"is index {idx} (read back)")

        saved = {
            "kg": {n: patient(br.get_header_bytes, "keygroup", idx, o, 1,
                              selector=0)[0] for n, o in kg_off.items()},
            "pg": {n: patient(br.get_header_bytes, "program", idx, o, 1)[0]
                   for n, o in pg_off.items()},
        }
        with open(os.path.join(args.out, "saved.json"), "w", encoding="utf-8") as fh:
            json.dump(saved, fh, indent=1)

        done = {"v": False}

        def restore(*_):
            if done["v"]:
                return
            done["v"] = True
            for n, v in saved["kg"].items():
                try:
                    br.set_header_bytes("keygroup", idx, kg_off[n],
                                        bytes([v & 0xFF]), selector=0, timeout=12.0)
                except Exception as e:      # noqa: BLE001
                    say(f"  !! restore kg {n}: {e}")
            for n, v in saved["pg"].items():
                try:
                    br.set_header_bytes("program", idx, pg_off[n],
                                        bytes([v & 0xFF]), timeout=12.0)
                except Exception as e:      # noqa: BLE001
                    say(f"  !! restore pg {n}: {e}")

        atexit.register(restore)
        signal.signal(signal.SIGINT, lambda *a: (restore(), sys.exit(1)))
        signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(1)))

        def wkg(n, v):
            br.set_header_bytes("keygroup", idx, kg_off[n], bytes([v & 0xFF]),
                                selector=0, timeout=12.0)

        def wpg(n, v):
            br.set_header_bytes("program", idx, pg_off[n], bytes([v & 0xFF]),
                                timeout=12.0)

        for n, v in PREPARE_KG.items():
            wkg(n, v)
        for n, v in PREPARE_PG.items():
            wpg(n, v)
        say("    prepared: " + ", ".join(f"{k} {v}" for k, v in PREPARE_KG.items()))

        t_off = args.pre + args.hold
        want = (args.pre + args.hold + args.tail) * sr

        def capture(tag):
            midi.send_message([0xC0, args.program])
            time.sleep(0.3)
            time.sleep(1.0)
            cap.start()
            time.sleep(args.pre)
            midi.send_message([0x90, args.note, args.velocity])
            time.sleep(args.hold)
            midi.send_message([0x80, args.note, 0])
            time.sleep(args.tail)
            ch = np.asarray(cap.stop_channels(), dtype=np.float64)
            if cap.xruns > 1 or cap.overflows:
                raise RuntimeError(f"{cap.xruns} xruns / {cap.overflows} "
                                   f"dropped -- capture is not contiguous")
            if ch.ndim != 2 or not (0.9 * want <= ch.shape[1] <= 1.1 * want):
                raise RuntimeError(f"capture is {ch.shape}, expected about "
                                   f"{want / sr:.2f}s")
            np.save(os.path.join(args.out, f"{tag}.npy"), ch)
            sched = {"tag": tag, "volume": args.volume,
                     "program": args.program, "prgnum_index": idx,
                     "note": args.note, "velocity": args.velocity, "sr": sr,
                     "t_off": t_off, "frame": args.frame, "hop": args.hop,
                     "sources": list(cap.sources),
                     "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                    time.gmtime())}
            with open(os.path.join(args.out, f"{tag}.sched.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(sched, fh, indent=1)
            return ch

        def floor_db(ch):
            best = None
            for c in range(ch.shape[0]):
                db, _ = envelope(ch[c], sr, args.frame, args.hop)
                if len(db):
                    f = float(np.median(db[:max(1, int(0.45 / args.hop))]))
                    best = f if best is None else max(best, f)
            return best

        say("")
        say(f"[{time.strftime('%H:%M:%S')}] === two-crossing rate "
            f"({args.l1:.0f} dB then {args.l2:.0f} dB, frame "
            f"{args.frame * 1000:.2f} ms) ===")
        say(f"  {'RELSE1':>6} {'rep':>4} {'sus dBFS':>9} {'floor dBFS':>11} "
            f"{'dB/s':>9} {'law':>9} {'ratio':>7}")
        rows = []
        for v in args.values:
            wkg("RELSE1", v)
            time.sleep(0.3)
            for k in range(args.repeats):
                tag = f"relse_{v:02d}_{k}"
                try:
                    ch = capture(tag)
                except Exception as e:      # noqa: BLE001
                    say(f"  {v:>6} {k:>4}  CAPTURE FAILED: {e}")
                    continue
                fl = floor_db(ch)
                best = None
                for c in range(ch.shape[0]):
                    db, t = envelope(ch[c], sr, 0.002, 0.0005)
                    sus = float(np.median(db[(t > t_off - 0.45) & (t < t_off - 0.15)]))
                    if best is None or sus > best[0]:
                        best = (sus, c)
                sus, c = best
                if not (sus > fl + 20):
                    say(f"  {v:>6} {k:>4}  VOID -- the note was not sounding "
                        f"(sustain {sus - INT16:.1f}, floor {fl - INT16:.1f} dBFS). "
                        f"Not a low reading: no reading.")
                    rows.append(dict(v=v, rep=k, void=True))
                    continue
                r, lat = crossing_rate(ch[c], sr, sus, t_off, args.frame,
                                       args.hop, args.l1, args.l2)
                lv = law(v)
                say(f"  {v:>6} {k:>4} {sus - INT16:9.1f} {fl - INT16:11.1f} "
                    f"{r:9.0f} {lv:9.0f} {r / lv if r == r else float('nan'):7.2f}")
                rows.append(dict(v=v, rep=k, sus_dbfs=sus - INT16,
                                 floor_dbfs=fl - INT16, rate=r, law=lv,
                                 latency_ms=lat * 1000))
            with open(os.path.join(args.out, "rows.json"), "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=1)

        say("")
        say(f"[{time.strftime('%H:%M:%S')}] === RESTORE ===")
        restore()
        done["v"] = True
        ok = True
        for n, v in saved["kg"].items():
            ok &= patient(br.get_header_bytes, "keygroup", idx, kg_off[n], 1,
                          selector=0)[0] == v
        for n, v in saved["pg"].items():
            ok &= patient(br.get_header_bytes, "program", idx, pg_off[n], 1)[0] == v
        say(f"    restored: {ok}")
        return 0 if ok else 4
    finally:
        cap.close()
        br.close()
        say(f"[{time.strftime('%H:%M:%S')}] port RELEASED")
        log.close()


if __name__ == "__main__":
    sys.exit(main())