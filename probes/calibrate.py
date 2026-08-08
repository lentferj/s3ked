#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The rig model -- MIDI out, JACK capture, record-then-play-then-measure, and
# the pre-EQ tap requirement -- is ported from the sibling mpc2emu project's
# tests/re_banks/hw_measure.py, GPL-2.0-or-later.
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

"""Sweep one parameter across its range and measure what the machine does.

    probes/calibrate.py filter --port "…" --program 0 --dry-run
    probes/calibrate.py filter --port "…" --program 0 --allow-write --out filter.csv

**Why this is cheap here and was not on the sibling projects.** Calibrating
the E-MU E4XT meant building a bank per parameter value, writing an SD card,
carrying it to the sampler and loading it -- so those curves were fitted from
four to six stopwatch points, and the bench session had to be planned around
the card swaps. This family takes the parameter over SysEx, acknowledged,
between one note and the next. A hundred-point sweep is one unattended run,
and the only thing it costs is the note length times the number of points.
That changes what is worth measuring: sweep the whole range, do not fit a
curve to five points and hope.

**Nothing here has been run against hardware.** The sweeps encode what to
neutralise and in what order, which is most of the value, but the byte offsets
they write are the same unverified transcription the rest of the project rests
on -- see DISCLAIMER.md. Work through ``HW_CHECKLIST.md`` first: if step 4
does not confirm the program header, every number this produces is a
measurement of the wrong parameter.

``--dry-run`` runs the whole pipeline against a synthetic machine, with no
MIDI port and no audio device. It is not a simulation of a real S3000 -- the
curves it invents are made up -- but it exercises the sweep, the measurement
and the fit, which is how this file was developed with no sampler in the room.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import measure as ms          # noqa: E402
from s3k import params as p            # noqa: E402

LEAD_IN = 1.5                          # recorder head start before the first note
TAIL = 2.0                             # recording tail after the last note-off


# ==========================================================================
# The rig
# ==========================================================================

@dataclass
class Rig:
    """How the sampler is wired to this host.

    RECORD FROM THE HARDWARE CAPTURE PORTS, NEVER FROM A DOWNSTREAM CLIENT.
    On a bench where the same inputs also feed an EQ and a room-correction
    plugin on the way to the monitors, tapping the downstream node convolves
    every measurement with a correction curve: it tilts spectra, moves every
    -3 dB corner and changes resonance peak heights. A verification pass would
    then reproduce the same error twice and look like a confirmation. Tap in
    parallel with the monitor path, upstream of everything in it.

    The S3000XL's individual outputs are the other trap: ``OUTPUT`` and
    ``VOSCL`` route a program away from the main stereo pair, so a program
    left on an individual output measures as silence on the main outs. The
    filter and envelope sweeps set ``OUTPUT`` themselves for that reason.
    """

    midi_port: str
    midi_channel: int = 0                          # 0-indexed
    capture: Sequence[str] = ("system:capture_1", "system:capture_2")

    def _midi_out(self):
        import rtmidi
        out = rtmidi.MidiOut()
        hits = [i for i, name in enumerate(out.get_ports()) if self.midi_port in name]
        if not hits:
            raise SystemExit(
                f"no MIDI output matching {self.midi_port!r} -- is the interface on? "
                f"(`s3kcli ports` lists what this host can see)"
            )
        out.open_port(hits[0])
        return out

    def play_and_record(self, note: int, hold: float, velocity: int = 100,
                        gap: float = 0.5, out_wav: Optional[str] = None):
        """Record one note. Returns ``(wav_path, t_on, t_off)`` in audio time.

        The two times come from the MIDI clock plus the measured onset offset,
        never from silence detection: a slow attack starts too quietly for a
        gate to find, and the amount by which the gate is late is exactly the
        attack time the sweep is trying to measure.
        """
        total = LEAD_IN + hold + gap + TAIL
        out_wav = out_wav or tempfile.mktemp(suffix=".wav")
        rec = subprocess.Popen(
            ["jack_rec", "-f", out_wav, "-d", f"{total:.1f}", *self.capture],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(LEAD_IN)
        out = self._midi_out()
        t0 = time.monotonic()
        out.send_message([0x90 | self.midi_channel, note, velocity])
        time.sleep(hold)
        t_off = time.monotonic() - t0
        out.send_message([0x80 | self.midi_channel, note, 0])
        del out
        rec.wait()
        return out_wav, 0.0, t_off


# ==========================================================================
# Sweep definitions
# ==========================================================================

@dataclass
class Sweep:
    """One calibration run: what to hold still, what to vary, what to measure.

    ``prepare`` is the part that carries the domain knowledge. A filter sweep
    with key-follow still enabled measures a corner that moves with the test
    note; an envelope sweep with velocity dependence still enabled measures
    the velocity curve as well; a level sweep through the tone section
    measures the tone section. Every entry here exists because leaving it out
    silently changes the answer rather than producing an obvious failure.
    """

    name: str
    param: str                                     # the parameter being swept
    region: str
    values: Sequence[int]
    measure: str                                   # key into MEASURERS
    unit: str
    note: int = 60
    hold: float = 3.0
    velocity: int = 100
    stereo: bool = False
    source: str = "any sustained sample"
    ref_band: Tuple[float, float] = (100.0, 500.0)
    prepare: Sequence[Tuple[str, str, int]] = field(default_factory=tuple)
    reference_value: Optional[int] = None          # take a reference at this value first
    blocked_on: Optional[str] = None
    why: str = ""


_ENV1_OPEN = (
    # Envelope 1 is the amplitude envelope. Held wide open, it contributes
    # nothing to the shape of the note, so whatever shape IS there came from
    # the parameter under test.
    ("keygroup", "ATTAK1", 0),
    ("keygroup", "DECAY1", 0),
    ("keygroup", "SUSTN1", 99),
    ("keygroup", "RELSE1", 0),
    ("keygroup", "V_ATT1", 0),
    ("keygroup", "V_REL1", 0),
    ("keygroup", "O_REL1", 0),
    ("keygroup", "K_DAR1", 0),
)

_MAIN_OUT = (
    # A program routed to an individual output measures as silence on the main
    # pair, which looks exactly like a broken rig.
    ("program", "OUTPUT", 0),
    ("program", "PANPOS", 0),
    ("program", "PRLOUD", 99),
    ("program", "V_LOUD", 0),
)

_FILTER_NEUTRAL = (
    ("keygroup", "K_FREQ", 0),        # key follow off: the corner must not track the note
    ("keygroup", "VFREQ1", 0),        # velocity zone 1 filter offset
    ("keygroup", "MODVFILT1", 0),
    ("keygroup", "MODVFILT2", 0),
    ("keygroup", "MODVFILT3", 0),
    ("program", "SPFILT", 0),         # soft-pedal filter reduction
)

_LFO_OFF = (
    ("program", "LFODEP", 0),
    ("program", "LFORAT", 0),
    ("program", "PANDEP", 0),
    ("program", "VELDEP", 0),
)


SWEEPS: Dict[str, Sweep] = {
    "filter": Sweep(
        name="filter",
        param="FILFRQ", region="keygroup",
        values=tuple(range(0, 100, 2)),
        measure="corner_hz", unit="Hz",
        hold=3.0,
        source="broadband: white noise, or failing that a bright saw",
        # An octave low enough that most of the FILFRQ range sits above it.
        # Points whose corner falls below this band come back NaN rather than
        # flooring at the band edge -- see measure.corner_frequency.
        ref_band=(50.0, 100.0),
        reference_value=99,
        prepare=_MAIN_OUT + _ENV1_OPEN + _FILTER_NEUTRAL + _LFO_OFF,
        why="FILFRQ 0..99 is documented as 'basic filter frequency' with no "
            "unit anywhere. Everything downstream -- showing a frequency in "
            "the editor, converting a cutoff INTO an Akai program -- needs "
            "the map from that integer to hertz.",
    ),
    "amp-attack": Sweep(
        name="amp-attack",
        param="ATTAK1", region="keygroup",
        values=(0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 99),
        measure="attack_s", unit="s",
        hold=12.0,
        source="a sample with an immediate onset, so the attack measured is "
               "the envelope's and not the sample's",
        prepare=_MAIN_OUT + _LFO_OFF + (
            ("keygroup", "DECAY1", 0),
            ("keygroup", "SUSTN1", 99),
            ("keygroup", "RELSE1", 0),
            ("keygroup", "V_ATT1", 0),
            ("keygroup", "K_DAR1", 0),
        ),
        why="ATTAK1 is a RATE, so 99 is fastest and the curve almost "
            "certainly runs the other way from the parameter number. Hold "
            "must exceed the slowest attack or the top of the range measures "
            "as 'still rising'.",
    ),
    "amp-release": Sweep(
        name="amp-release",
        param="RELSE1", region="keygroup",
        values=(0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 99),
        measure="release_s", unit="s",
        hold=2.0,
        source="a sustained sample, looped, so the level at note-off is steady",
        prepare=_MAIN_OUT + _LFO_OFF + (
            ("keygroup", "ATTAK1", 0),
            ("keygroup", "DECAY1", 0),
            ("keygroup", "SUSTN1", 99),
            ("keygroup", "V_REL1", 0),
            ("keygroup", "O_REL1", 0),
            ("keygroup", "K_DAR1", 0),
        ),
        why="Release is measured from note-off to -40 dB. A NaN at the slow "
            "end means the release outran the 12 s measurement window, which "
            "is a finding: widen the window and re-run those points only.",
    ),
    "amp-decay": Sweep(
        name="amp-decay",
        param="DECAY1", region="keygroup",
        values=(0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 99),
        measure="decay_s", unit="s",
        hold=10.0,
        source="a sustained sample, looped",
        prepare=_MAIN_OUT + _LFO_OFF + (
            ("keygroup", "ATTAK1", 0),
            ("keygroup", "SUSTN1", 50),          # MUST be below peak or there
            ("keygroup", "RELSE1", 0),           # is no decay phase to time
            ("keygroup", "K_DAR1", 0),
        ),
        why="Sustain is deliberately at half: with SUSTN1 at 99 there is no "
            "decay segment and every point returns NaN.",
    ),
    "loudness": Sweep(
        name="loudness",
        param="PRLOUD", region="program",
        values=tuple(range(0, 100, 5)),
        measure="rms_db", unit="dB",
        hold=3.0,
        source="a sustained sample, looped",
        reference_value=99,
        prepare=_MAIN_OUT[:2] + _ENV1_OPEN + _LFO_OFF + (
            ("program", "V_LOUD", 0),
            ("keygroup", "VLOUD1", 0),
        ),
        why="0..99 could be dB, could be linear amplitude, could be a table. "
            "The answer decides how a converter maps a gain onto this field "
            "-- get it wrong and every converted program is mixed wrong.",
    ),
    "pan": Sweep(
        name="pan",
        param="PANPOS", region="program",
        values=tuple(range(-50, 51, 5)),
        measure="balance_db", unit="dB",
        hold=2.0, stereo=True,
        source="a sustained MONO sample -- a stereo one pans its own image",
        prepare=_MAIN_OUT[:1] + _ENV1_OPEN + _LFO_OFF + (
            ("program", "STEREO", 99),
            ("keygroup", "VPANO1", 0),
        ),
        why="Settles the pan law: linear-amplitude, constant-power, or a "
            "table. Needs a two-channel recording; a mono sum makes every "
            "position look alike.",
    ),
    "lfo-rate": Sweep(
        name="lfo-rate",
        param="LFORAT", region="program",
        values=tuple(range(0, 100, 5)),
        measure="mod_hz", unit="Hz",
        hold=10.0,
        source="a sustained sample, looped",
        prepare=_MAIN_OUT + _ENV1_OPEN + (
            ("program", "LFODEP", 99),
            ("program", "LFODEL", 0),
            ("program", "VELDEP", 0),
            ("program", "PANDEP", 0),
        ),
        blocked_on="LFO1 must be routed to something audible. The table has "
                   "MODSLFOT/MODSLFOL as SOURCES of modulation OF the LFO, "
                   "not the LFO's own destination, and the S1000 spec's "
                   "routing prose is not conclusive. Confirm on the panel "
                   "which destination LFO1 drives before trusting a curve.",
        why="LFORAT is 'speed of LFO1', unitless. Ten seconds of note gives "
            "the envelope FFT enough resolution to resolve rates below 1 Hz.",
    ),
    "tuning": Sweep(
        name="tuning",
        param="KGTUNO", region="keygroup",
        values=(0, 1, 2, 4, 8, 16, 32, 50),
        measure="cents", unit="cents",
        hold=2.0,
        source="a steady pitched sample with a clear fundamental",
        reference_value=0,
        prepare=_MAIN_OUT + _ENV1_OPEN + _LFO_OFF + (
            ("program", "PTUNO", 0),
        ),
        why="The tuning fields are documented as 'cent:semi' pairs, so the "
            "low byte should be cents and a step of 1 should move the pitch "
            "by one cent. That is a claim about a two-byte layout, and it is "
            "cheap to check: sweep it and see whether 50 gives 50 cents or "
            "half a semitone of something else.",
    ),
}


# ==========================================================================
# Measurement dispatch
# ==========================================================================

#: Every measurement the sweeps may name. Checked before a note is played:
#: a typo that only surfaces after the first recording costs a whole take.
_MEASUREMENTS = frozenset({
    "corner_hz", "attack_s", "release_s", "decay_s",
    "rms_db", "balance_db", "mod_hz", "cents",
})


def _measure(kind: str, wav: str, t_on: float, t_off: float,
             reference=None, ref_band: Tuple[float, float] = (100.0, 500.0)
             ) -> Tuple[float, object]:
    """Returns ``(value, reference_to_carry_forward)``."""
    if kind not in _MEASUREMENTS:
        raise KeyError(f"unknown measurement {kind!r}; expected one of "
                       f"{sorted(_MEASUREMENTS)}")
    if kind == "balance_db":
        stereo, _sr = ms.read_wav(wav, mono=False)
        return ms.balance_db(stereo), reference

    mono, sr = ms.read_wav(wav)
    env, t = ms.envelope(mono, sr)
    offset = ms.anchor_offset(env, t, t_on)
    on, off = t_on + offset, t_off + offset

    if kind == "attack_s":
        return ms.attack_time(env, on, off), reference
    if kind == "release_s":
        return ms.release_time(env, on, off), reference
    if kind == "decay_s":
        return ms.decay_time(env, on, off), reference
    if kind == "rms_db":
        seg = mono[int((on + 0.25) * sr): int(off * sr)]
        return ms.rms_db(seg), reference
    if kind == "mod_hz":
        i0, i1 = int(on / ms.DEFAULT_HOP), int(off / ms.DEFAULT_HOP)
        return ms.modulation_rate_hz(env[i0:i1]), reference
    if kind == "corner_hz":
        seg = mono[int(on * sr): int(off * sr)]
        freqs, mag = ms.spectrum(seg, sr)
        if reference is None:
            return float("nan"), mag          # this take IS the reference
        return ms.corner_frequency(freqs, mag, reference=reference,
                                   ref_lo=ref_band[0], ref_hi=ref_band[1]), reference
    if kind == "cents":
        seg = mono[int((on + 0.1) * sr): int(off * sr)]
        f = ms.fundamental_hz(seg, sr)
        if reference is None:
            return 0.0, f
        return ms.cents_between(f, reference), reference
    raise AssertionError(f"measurement {kind!r} is listed but not implemented")


# ==========================================================================
# Driving
# ==========================================================================

def apply_prepare(bridge, sweep: Sweep, program: int, keygroup: int,
                  verbose: bool = True) -> None:
    """Neutralise everything that would otherwise contaminate the sweep."""
    for region, name, value in sweep.prepare:
        param = p.lookup(name, region)
        bridge.set_parameter(param, program, value, keygroup=keygroup)
        if verbose:
            print(f"    {region}.{name} = {value}")


def run_sweep(bridge, rig, sweep: Sweep, program: int = 0, keygroup: int = 0,
              keep_dir: Optional[str] = None, verbose: bool = True) -> List[dict]:
    """Set, play, record, measure -- once per value. Returns one row per point."""
    param = p.lookup(sweep.param, sweep.region)
    if verbose:
        print(f"  preparing ({len(sweep.prepare)} parameters neutralised)")
    apply_prepare(bridge, sweep, program, keygroup, verbose=False)

    reference = None
    order = list(sweep.values)
    if sweep.reference_value is not None:
        order = [sweep.reference_value] + [v for v in order
                                           if v != sweep.reference_value]

    rows: List[dict] = []
    for i, value in enumerate(order):
        bridge.set_parameter(param, program, value, keygroup=keygroup)
        wav = None
        if keep_dir:
            wav = str(Path(keep_dir) / f"{sweep.name}_{value:+04d}.wav")
        path, t_on, t_off = rig.play_and_record(
            sweep.note, sweep.hold, velocity=sweep.velocity, out_wav=wav)
        got, reference = _measure(sweep.measure, path, t_on, t_off, reference,
                                  ref_band=sweep.ref_band)
        is_ref = sweep.reference_value is not None and i == 0
        if not is_ref:
            rows.append({"value": value, sweep.measure: got})
        if verbose:
            tag = "  (reference)" if is_ref else ""
            print(f"    {sweep.param:>8} = {value:>4}  ->  "
                  f"{got:10.4g} {sweep.unit}{tag}")
    rows.sort(key=lambda r: r["value"])
    return rows


def summarise(sweep: Sweep, rows: List[dict]) -> dict:
    """Fit the curve and say honestly how well it fits."""
    xs = [r["value"] for r in rows]
    ys = [r[sweep.measure] for r in rows]
    usable = [(x, y) for x, y in zip(xs, ys)
              if isinstance(y, float) and math.isfinite(y) and y > 0]
    out = {"sweep": sweep.name, "parameter": sweep.param, "unit": sweep.unit,
           "points": len(rows), "usable": len(usable)}
    if len(usable) >= 3:
        a, b, r2 = ms.fit_exponential([x for x, _ in usable],
                                      [y for _, y in usable])
        out.update(fit=f"{sweep.unit} = {a:.6g} * exp({b:.6g} * {sweep.param})",
                   a=a, b=b, r2=r2)
        out["fit_trustworthy"] = bool(r2 is not None and r2 > 0.99)
    return out


# ==========================================================================
# Dry run: a fake machine, so the pipeline can be exercised with no hardware
# ==========================================================================

class _SyntheticBridge:
    """Accepts every write and remembers it. Verifies nothing."""

    def __init__(self):
        self.writes: List[Tuple[str, str, int]] = []
        self.state: Dict[str, int] = {}

    def set_parameter(self, param, index, value, *, keygroup=0, **_kw):
        self.writes.append((param.region, param.name, int(value)))
        self.state[param.name] = int(value)


class _SyntheticRig:
    """Renders a note from the fake machine's current state.

    The curves it invents are NOT claims about a real S3000 -- they are
    plausible shapes chosen so that the fit, the NaN handling and the
    reference take all get exercised. Anything printed by --dry-run is
    fiction; the point is that the pipeline ran.
    """

    SR = 44100

    def __init__(self, state: Dict[str, int]):
        self.state = state

    def play_and_record(self, note, hold, velocity=100, gap=0.5, out_wav=None):
        import wave
        import numpy as np

        n = int((hold + 1.0) * self.SR)
        t = np.arange(n) / self.SR
        s = self.state

        # Invented curves. See the class docstring.
        atk = 0.002 * math.exp(0.075 * (99 - s.get("ATTAK1", 99)))
        rel = 0.002 * math.exp(0.075 * (99 - s.get("RELSE1", 99)))
        sus = s.get("SUSTN1", 99) / 99.0
        env = np.clip(t / max(atk, 1e-4), 0, 1)
        held = t <= hold
        env = np.where(held, sus + (1 - sus) * np.exp(-t / 0.4) * (env >= 1) + env * 0,
                       0.0)
        env = np.where(t < atk, (t / max(atk, 1e-4)) * 1.0, env)
        after = ~held
        env = np.where(after, sus * np.exp(-(t - hold) / max(rel, 1e-4)), env)

        rng = np.random.default_rng(11)
        src = rng.standard_normal(n) * 0.2
        corner = 30.0 * math.exp(0.062 * s.get("FILFRQ", 99))
        spec = np.fft.rfft(src)
        f = np.fft.rfftfreq(n, 1 / self.SR)
        spec *= 1.0 / np.sqrt(1.0 + (f / corner) ** 8)
        src = np.fft.irfft(spec, n=n)

        lfo_hz = 0.08 * math.exp(0.05 * s.get("LFORAT", 0))
        if s.get("LFODEP", 0):
            env = env * (1 + 0.5 * np.sin(2 * np.pi * lfo_hz * t))

        gain = 10 ** ((s.get("PRLOUD", 99) - 99) * 0.35 / 20.0)
        mono = env * src * gain

        pan = s.get("PANPOS", 0) / 50.0
        left = mono * math.cos((pan + 1) * math.pi / 4)
        right = mono * math.sin((pan + 1) * math.pi / 4)

        path = out_wav or tempfile.mktemp(suffix=".wav")
        frames = (np.stack([left, right], axis=1).clip(-1, 1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self.SR)
            w.writeframes(frames.tobytes())
        return path, 0.0, hold


# ==========================================================================
# CLI
# ==========================================================================

def _print_plan(sweep: Sweep) -> None:
    print(f"\n{sweep.name}: sweep {sweep.region}.{sweep.param} over "
          f"{len(sweep.values)} values -> {sweep.unit}")
    print(f"  source needed : {sweep.source}")
    print(f"  note {sweep.note}, hold {sweep.hold} s, velocity {sweep.velocity}"
          f"{', STEREO capture required' if sweep.stereo else ''}")
    print(f"  neutralises   : {len(sweep.prepare)} parameters")
    est = len(sweep.values) * (LEAD_IN + sweep.hold + 0.5 + TAIL)
    print(f"  run time      : about {est / 60:.1f} min unattended")
    if sweep.why:
        print(f"  why           : {sweep.why}")
    if sweep.blocked_on:
        print(f"  BLOCKED ON    : {sweep.blocked_on}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sweep", nargs="?", choices=sorted(SWEEPS),
                    help="which calibration to run")
    ap.add_argument("--list", action="store_true", help="describe every sweep and exit")
    ap.add_argument("--port", help="MIDI port substring for the sampler")
    ap.add_argument("--midi-channel", type=int, default=1,
                    help="1-indexed MIDI channel the program answers on")
    ap.add_argument("--capture", nargs=2,
                    default=["system:capture_1", "system:capture_2"],
                    help="JACK capture ports -- the HARDWARE inputs, not a "
                         "downstream client (see Rig)")
    ap.add_argument("--exclusive-channel", type=int, default=0)
    ap.add_argument("--program", type=int, default=0)
    ap.add_argument("--keygroup", type=int, default=0)
    ap.add_argument("--allow-write", action="store_true",
                    help="required: a sweep writes parameters continuously")
    ap.add_argument("--dry-run", action="store_true",
                    help="run against a synthetic machine, no hardware touched")
    ap.add_argument("--out", help="write the rows here as CSV")
    ap.add_argument("--keep-wavs", help="directory to keep every recording in")
    args = ap.parse_args(argv)

    if args.list or not args.sweep:
        for sweep in SWEEPS.values():
            _print_plan(sweep)
        print("\nRun order and the reasoning behind it: "
              "docs/re_procedures/calibration.md")
        return 0

    sweep = SWEEPS[args.sweep]
    _print_plan(sweep)

    if args.dry_run:
        print("\n  [DRY RUN] synthetic machine -- every number below is fiction\n")
        bridge = _SyntheticBridge()
        rig = _SyntheticRig(bridge.state)
    else:
        if not args.allow_write:
            print("\nrefusing to run: a sweep writes parameters continuously. "
                  "Pass --allow-write once you have worked through "
                  "HW_CHECKLIST.md and saved the machine's state to disk.",
                  file=sys.stderr)
            return 2
        if not args.port:
            print("--port is required without --dry-run", file=sys.stderr)
            return 2
        from s3k.bridge import S3kBridge
        bridge = S3kBridge.standard(args.port,
                                    exclusive_channel=args.exclusive_channel)
        rig = Rig(args.port if not args.capture else args.port,
                  midi_channel=args.midi_channel - 1, capture=tuple(args.capture))
        print(f"  connected: {bridge.description}")

    if args.keep_wavs:
        Path(args.keep_wavs).mkdir(parents=True, exist_ok=True)

    rows = run_sweep(bridge, rig, sweep, program=args.program,
                     keygroup=args.keygroup, keep_dir=args.keep_wavs)
    summary = summarise(sweep, rows)
    print("\n  " + json.dumps(summary, indent=2).replace("\n", "\n  "))

    if args.out:
        with open(args.out, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["value", sweep.measure])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
