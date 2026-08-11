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
import os
import signal
import subprocess
import sys
import tempfile
import time
import wave
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

class _InProcessRecorder:
    """One JACK client for the whole session, instead of one per capture.

    `jack_rec` is spawned and torn down per recording, and on this bench it
    stops exiting roughly every 8-10 captures and takes the JACK **server**
    with it -- after which no client can register and recovery needs the audio
    graph restarted (RESOLUTION_NOTES §16, §19). Registering once and keeping
    the client alive removes the create/destroy cycle entirely, which is the
    thing that wedges.

    Falls back to `jack_rec` when the binding is unavailable, so the probe
    still runs on a host without it.
    """

    def __init__(self, capture: Sequence[str], name: str = "s3ked-cal"):
        import jack
        import numpy as np

        self._np = np
        self.client = jack.Client(name, no_start_server=True)
        self.ports = [self.client.inports.register(f"in{i}")
                      for i in range(len(capture))]
        self._frames = []
        self._armed = False

        @self.client.set_process_callback
        def _process(nframes):          # noqa: ARG001 - jack calls with frames
            if self._armed:
                self._frames.append(
                    [p.get_array().copy() for p in self.ports])

        self.client.activate()
        for src, dst in zip(capture, self.ports):
            self.client.connect(src, dst)
        self.samplerate = self.client.samplerate

    def record(self, seconds: float, during=None, then=None, after: float = 0.0):
        """Capture *seconds*, calling *during* at the start and *then* at
        *after* seconds in -- both while recording is still running.

        `then` exists because the release phase happens after note-off, and a
        note-off sent once the capture has finished records nothing: the
        release sweep returned NaN at every one of fourteen points because of
        exactly that. Anything measured after an event has to have the event
        inside the window.
        """
        self._frames = []
        self._armed = True
        try:
            time.sleep(0.15)            # let a couple of periods land first
            if during is not None:
                during()
            if then is not None:
                time.sleep(max(after - 0.15, 0.0))
                then()
                time.sleep(max(seconds - after, 0.0))
            else:
                time.sleep(max(seconds - 0.15, 0.0))
        finally:
            self._armed = False
        np = self._np
        if not self._frames:
            return np.zeros((0, len(self.ports)), dtype="float32")
        chans = [np.concatenate([blk[i] for blk in self._frames])
                 for i in range(len(self.ports))]
        return np.stack(chans, axis=1)

    def write_wav(self, path: str, data) -> None:
        np = self._np
        frames = (np.clip(data, -1, 1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(data.shape[1] if data.ndim > 1 else 1)
            w.setsampwidth(2)
            w.setframerate(int(self.samplerate))
            w.writeframes(frames.tobytes())

    def close(self):
        try:
            self.client.deactivate()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass


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

    def _recorder(self):
        """The in-process JACK client, or None if the binding is missing."""
        if getattr(self, "_rec_failed", False):
            return None
        cached = getattr(self, "_cached_rec", None)
        if cached is None:
            try:
                cached = _InProcessRecorder(self.capture)
                object.__setattr__(self, "_cached_rec", cached)
            except Exception as exc:
                object.__setattr__(self, "_rec_failed", True)
                print(f"  (in-process recorder unavailable: {exc}; "
                      f"falling back to jack_rec)")
                return None
        return cached

    def _port(self):
        """One MIDI port for the whole sweep, opened lazily.

        Opening a fresh ALSA client per note costs 51 create/destroy cycles on
        a 50-point sweep -- half the reason the runtime estimate was so far out
        -- and each one is a chance to fail mid-run.
        """
        cached = getattr(self, "_cached_out", None)
        if cached is None:
            cached = self._midi_out()
            object.__setattr__(self, "_cached_out", cached)
        return cached

    def close(self):
        rec = getattr(self, "_cached_rec", None)
        if rec is not None:
            rec.close()
            object.__setattr__(self, "_cached_rec", None)
        cached = getattr(self, "_cached_out", None)
        if cached is not None:
            try:
                cached.close_port()
            except Exception:
                pass
            object.__setattr__(self, "_cached_out", None)

    def _midi_out(self):
        """Open the one port named. Ambiguity raises rather than guessing.

        A multi-port interface enumerates as several ports sharing a prefix --
        an ESI M4U XT is four -- so a substring like "M4U XT" matches all of
        them and ``hits[0]`` picks whichever the driver happened to enumerate
        first. That works until it silently does not, and a sweep driving the
        wrong DIN socket measures silence and fits a curve to it. Reported by
        the sibling mpc2emu project, who hit it running this rig from their
        side.
        """
        import rtmidi
        out = rtmidi.MidiOut()
        ports = out.get_ports()
        exact = [i for i, name in enumerate(ports) if name == self.midi_port]
        hits = exact or [i for i, name in enumerate(ports) if self.midi_port in name]
        if not hits:
            raise SystemExit(
                f"no MIDI output matching {self.midi_port!r} -- is the interface on? "
                f"(`s3kcli ports` lists what this host can see)"
            )
        if len(hits) > 1:
            listing = "\n  ".join(ports[i] for i in hits)
            raise SystemExit(
                f"{self.midi_port!r} matches {len(hits)} ports; name one exactly:"
                f"\n  {listing}"
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
        ours = not out_wav
        if ours:
            handle, out_wav = tempfile.mkstemp(suffix=".wav", prefix="s3ked-cal-")
            os.close(handle)

        recorder = self._recorder()
        if recorder is not None:
            out = self._port()
            t0 = [0.0]

            def _play():
                t0[0] = time.monotonic()
                out.send_message([0x90 | self.midi_channel, note, velocity])

            def _release():
                out.send_message([0x80 | self.midi_channel, note, 0])

            # note-on at ~0.15 s in, note-off `hold` later, and TAIL of
            # recording after that so the release has somewhere to land.
            data = recorder.record(0.15 + hold + TAIL, during=_play,
                                   then=_release, after=0.15 + hold)
            recorder.write_wav(out_wav, data)
            return out_wav, 0.0, hold

        rec = subprocess.Popen(
            ["jack_rec", "-f", out_wav, "-d", f"{total:.1f}", *self.capture],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(LEAD_IN)
            out = self._port()
            t0 = time.monotonic()
            out.send_message([0x90 | self.midi_channel, note, velocity])
            time.sleep(hold)
            t_off = time.monotonic() - t0
            out.send_message([0x80 | self.midi_channel, note, 0])
            # Bounded, and reaped either way. An unbounded wait here is what
            # blocked a sweep for 24 minutes on a 7-second recording and then
            # wedged the JACK server itself: an orphaned client can stop the
            # server accepting new ones, which outlives killing the orphan.
            # Never trust `-d` to terminate the process.
            rec.wait(timeout=total + 10.0)
        except subprocess.TimeoutExpired:
            rec.kill()
            rec.wait(timeout=5)
            if ours:
                # We created this path; nothing downstream will read or remove
                # it now, and a run that dies here leaves it behind for ever.
                # Five such files survived tonight's crashed runs.
                try:
                    os.unlink(out_wav)
                except OSError:
                    pass
            raise RuntimeError(
                f"jack_rec did not exit after {total + 10.0:.0f}s for a "
                f"{total:.1f}s recording -- killed it. If this repeats, the "
                f"JACK server may be wedged; check `jack_lsp` responds."
            )
        finally:
            if rec.poll() is None:
                rec.kill()
                try:
                    rec.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
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
    fit: str = "exp"          # "exp" or "linear"; see summarise()
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
        # MEASURED exponential, 2026-08-11: x2.092 per 10 units, r2 0.9996.
        fit="exp",
        param="FILFRQ", region="keygroup",
        values=tuple(range(0, 100, 2)),
        measure="corner_hz", unit="Hz",
        hold=3.0,
        # Note 24, NOT the default 60. Measured 2026-08-11 (§20): the resident
        # SAWTOOTH sounds at 261.6 Hz at note 60, which puts the reference band
        # entirely BELOW the fundamental -- and a sawtooth has no energy there,
        # so the band defining 0 dB would be noise. At note 24 the fundamental
        # is ~32.7 Hz and its 2nd and 3rd harmonics (65, 98 Hz) fall inside the
        # band. The note and the band have to be chosen together.
        note=24,
        source="broadband: white noise, or failing that a bright saw",
        # An octave low enough that most of the FILFRQ range sits above it.
        # Points whose corner falls below this band come back NaN rather than
        # flooring at the band edge -- see measure.corner_frequency. Confirmed
        # firing on hardware at FILFRQ 30, where the corner drops into it.
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
        # HYPOTHESIS, not measured: the measurement is already in dB, which
        # is logarithmic, so a level control is likely linear in it. If the
        # data disagrees, summarise() will say so.
        fit="linear",
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
        # HYPOTHESIS, not measured: balance in dB against a position that
        # runs symmetrically about centre, so linear is the guess. A pan law
        # is often sin/cos, which is neither shape -- expect disagreement.
        fit="linear",
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
        # MEASURED linear, 2026-08-11: 0.11867 Hz per unit, r2 0.9995, so
        # 0..99 spans about 0..11.7 Hz. The exponential manages 0.897 -- the
        # one sweep whose shape the old single-model harness got outright
        # wrong. RESOLUTION_NOTES §24.
        fit="linear",
        prepare=_MAIN_OUT + _ENV1_OPEN + (
            ("program", "LFODEP", 99),
            ("program", "LFODEL", 0),
            ("program", "VELDEP", 0),
            ("program", "PANDEP", 0),
            # LFO1 is wired to PITCH: LFODEP sits beside MWLDEP, PRSDEP and
            # VELDEP -- modwheel, aftertouch and velocity depth -- which is the
            # classic vibrato structure, so there is no destination parameter
            # to choose. But `mod_hz` measures AMPLITUDE modulation, and
            # pointing an amplitude detector at vibrato returns a clean
            # nothing. So route LFO1 into loudness through the assignable
            # matrix instead: source 7 is LFO1 (§15), and an amount without a
            # source is inert -- both halves are required.
            ("program", "MODSAMP1", 7),
            ("program", "MODVAMP1", 50),
        ),
        why="LFORAT is 'speed of LFO1', unitless. Ten seconds of note gives "
            "the envelope FFT enough resolution to resolve rates below 1 Hz.",
    ),
    "tuning": Sweep(
        name="tuning",
        # MEASURED linear, 2026-08-11: 0.39167 cents per unit, r2 0.9998.
        # KGTUNO's low byte is 1/256 semitone, so the scale does not bend.
        fit="linear",
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

def verify_isolation(bridge, rig, program: int, keygroup: int, note: int,
                     hold: float = 1.5, min_drop_db: float = 6.0) -> float:
    """Refuse to measure until the program under test is the one being heard.

    Silences the keygroup by moving its key range off *note*, records, restores
    it, and records again. If the two are indistinguishable, something else is
    making the sound and every number that follows would describe it instead.

    **This is the check whose absence invalidated an entire evening's
    measurements** (RESOLUTION_NOTES §18). Eleven programs shared MIDI channel
    1, so every note sounded the program under test buried beneath ten others;
    the filter appeared inert, three sample types produced identical spectra,
    and a structural theory was built on the sum of ten unrelated programs. The
    instrument had been validated carefully. Nobody had validated what it was
    pointed at.

    The general condition is *any rig where more than one voice can answer a
    note* -- which is most of them. The sibling mpc2emu project reports the
    same check was missing from its E4XT and MPC calibrations too.

    Returns the drop in dB. Raises :class:`RuntimeError` below *min_drop_db*.
    """
    lo = p.lookup(("keygroup", "LONOTE"))
    hi = p.lookup(("keygroup", "HINOTE"))
    was = (bridge.get_parameter(lo, program, keygroup=keygroup),
           bridge.get_parameter(hi, program, keygroup=keygroup))

    def _level():
        wav, t_on, t_off = rig.play_and_record(note, hold)
        try:
            return _measure("rms_db", wav, t_on, t_off, None)[0]
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass

    try:
        # Move the key range so the note cannot sound this keygroup at all.
        silent_lo = 0 if note > 63 else 100
        bridge.set_parameter(lo, program, silent_lo, keygroup=keygroup)
        bridge.set_parameter(hi, program, silent_lo + 27, keygroup=keygroup)
        muted = _level()
    finally:
        bridge.set_parameter(lo, program, was[0], keygroup=keygroup)
        bridge.set_parameter(hi, program, was[1], keygroup=keygroup)

    sounding = _level()
    drop = sounding - muted
    if drop < min_drop_db:
        raise RuntimeError(
            f"isolation check FAILED: silencing program {program} keygroup "
            f"{keygroup} changed the recording by only {drop:.2f} dB "
            f"({sounding:.2f} vs {muted:.2f}). Something else is sounding on "
            f"this MIDI channel, so any measurement would describe that "
            f"instead. Give this program a channel no other resident program "
            f"uses (PMCHAN, program offset 16). See RESOLUTION_NOTES §18."
        )
    return drop


def snapshot_prepare(bridge, sweep: Sweep, program: int, keygroup: int) -> dict:
    """Read every parameter the sweep is about to move, so it can be put back.

    A sweep neutralises twenty-odd parameters and then walks a twenty-third
    across its whole range. Without this it leaves all of them changed: the
    program is measurably not the one the user had, and nothing says so. The
    sibling eosed and mpc2emu rigs work on scratch presets built for the
    purpose; this one runs against whatever program it is pointed at.
    """
    touched = list(sweep.prepare) + [(sweep.region, sweep.param, None)]
    saved = {}
    for region, name, _value in touched:
        param = p.lookup(name, region)
        saved[(region, name)] = bridge.get_parameter(
            param, program, keygroup=keygroup)
    return saved


def restore_prepare(bridge, saved: dict, program: int, keygroup: int,
                    verbose: bool = True) -> list:
    """Put back everything :func:`snapshot_prepare` recorded. Returns failures."""
    failed = []
    for (region, name), value in saved.items():
        param = p.lookup(name, region)
        try:
            bridge.set_parameter(param, program, value, keygroup=keygroup)
            if bridge.get_parameter(param, program, keygroup=keygroup) != value:
                raise RuntimeError("read-back differs")
        except Exception as exc:
            failed.append(f"{region}.{name} -> {value!r} ({exc})")
    if verbose:
        print(f"  restored {len(saved) - len(failed)}/{len(saved)} parameters")
    for line in failed:
        print(f"  !! NOT RESTORED: {line}")
    return failed


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
        print(f"  reading {len(sweep.prepare) + 1} parameters so they can be put back")
    saved = None
    # `finally` alone is not enough: SIGTERM -- which `timeout(1)`, a job
    # scheduler and Ctrl-C-then-kill all send -- terminates CPython without
    # unwinding, so the restore never runs. That is not hypothetical; it is
    # how this probe left a program with OUTPUT=0, ATTAK1=0 and a mid-sweep
    # FILFRQ on 2026-08-10. Turning the signal into an exception lets the
    # `finally` below do its job.
    def _bail(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, _bail)
        except (ValueError, OSError):
            pass  # not the main thread, or the platform lacks it

    try:
        # Inside the guard: this writes LONOTE/HINOTE and restores them, so a
        # signal here must unwind like any other part of the sweep.
        if verbose:
            print("  verifying the program under test is the one being heard")
        drop = verify_isolation(bridge, rig, program, keygroup, sweep.note)
        if verbose:
            print(f"  isolation ok -- silencing it drops the recording "
                  f"{drop:.1f} dB")

        saved = snapshot_prepare(bridge, sweep, program, keygroup)
        if verbose:
            print(f"  preparing ({len(sweep.prepare)} parameters neutralised)")
        apply_prepare(bridge, sweep, program, keygroup, verbose=False)

        return _sweep_points(bridge, rig, sweep, param, program, keygroup,
                             keep_dir, verbose)
    finally:
        if saved is not None:
            restore_prepare(bridge, saved, program, keygroup, verbose=verbose)
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


def _sweep_points(bridge, rig, sweep: Sweep, param, program: int, keygroup: int,
                  keep_dir: Optional[str], verbose: bool) -> List[dict]:
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
        try:
            got, reference = _measure(sweep.measure, path, t_on, t_off, reference,
                                      ref_band=sweep.ref_band)
        finally:
            # A sweep is one recording per point and they are large. Only the
            # ones the caller asked to keep survive; the rest go, even if the
            # measurement raised. Leaving them behind filled /tmp once.
            if wav is None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
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
    """Fit the curve and say honestly how well it fits.

    **Both shapes are fitted, every time, and the data picks.** The harness
    used to assume `a*exp(b*x)` for every sweep. That is right for filter
    frequency, which really does double every so many units, and wrong for
    tuning, which is a straight line -- and it reported
    `cents = 1.05*exp(0.067*KGTUNO)` for a relationship measured at r2 0.9998
    as `0.39167*KGTUNO` (RESOLUTION_NOTES §21). A forced model does not fail
    loudly; it produces a plausible equation of the wrong shape.

    A sweep still declares the shape it expects, because disagreeing with an
    expectation is more informative than having none. When the data prefers the
    other one, ``fit_model_disagrees`` says so rather than quietly switching.
    """
    xs = [r["value"] for r in rows]
    ys = [r[sweep.measure] for r in rows]
    finite = [(x, y) for x, y in zip(xs, ys)
              if isinstance(y, float) and math.isfinite(y)]
    positive = [(x, y) for x, y in finite if y > 0]

    out = {"sweep": sweep.name, "parameter": sweep.param, "unit": sweep.unit,
           "points": len(rows), "usable": len(finite),
           "expected_model": sweep.fit}

    fits = {}
    if len(positive) >= 3:
        a, b, r2 = ms.fit_exponential([x for x, _ in positive],
                                      [y for _, y in positive])
        if not math.isnan(r2):
            fits["exp"] = {
                "expr": f"{sweep.unit} = {a:.6g} * exp({b:.6g} * {sweep.param})",
                "r2": r2, "a": a, "b": b, "n": len(positive)}
    if len(finite) >= 3:
        m, c, r2 = ms.fit_linear([x for x, _ in finite], [y for _, y in finite])
        if not math.isnan(r2):
            fits["linear"] = {
                "expr": f"{sweep.unit} = {m:.6g} * {sweep.param} {c:+.6g}",
                "r2": r2, "m": m, "c": c, "n": len(finite)}
    if not fits:
        return out

    # r2 is comparable here only because the linear fit is in the measured
    # unit and the exponential's is in log space -- so this compares "how much
    # of the variation each shape explains, on its own terms". Treat a small
    # difference as no difference.
    best = max(fits, key=lambda k: fits[k]["r2"])
    chosen = sweep.fit if sweep.fit in fits else best
    picked = fits[chosen]

    out.update(fit=picked["expr"], r2=picked["r2"], model=chosen,
               fits={k: {"expr": v["expr"], "r2": round(v["r2"], 6)}
                     for k, v in fits.items()})
    out.update({k: v for k, v in picked.items()
                if k in ("a", "b", "m", "c")})
    out["fit_trustworthy"] = bool(picked["r2"] > 0.99)
    if best != chosen and fits[best]["r2"] > picked["r2"] + 0.01:
        out["fit_model_disagrees"] = (
            f"declared {chosen} (r2 {picked['r2']:.5f}) but the data prefers "
            f"{best} (r2 {fits[best]['r2']:.5f}) -- check which shape the "
            f"parameter really has before trusting either")
    return out


# ==========================================================================
# Dry run: a fake machine, so the pipeline can be exercised with no hardware
# ==========================================================================

class _SyntheticBridge:
    """Accepts every write and remembers it. Verifies nothing.

    It reads back too, which matters more than it sounds: the dry run is the
    only place the snapshot-and-restore path gets exercised without hardware,
    and a write-only fake would let a broken restore through unnoticed.
    """

    def __init__(self):
        self.writes: List[Tuple[str, str, int]] = []
        # Seeded with a full-range keygroup. Unset parameters otherwise read
        # back as their MINIMUM, which for HINOTE is 21 -- so the isolation
        # check would "restore" the keygroup to spanning 21..21 and silence
        # the rest of the sweep.
        self.state: Dict[str, int] = {"LONOTE": 21, "HINOTE": 127}

    def set_parameter(self, param, index, value, *, keygroup=0, **_kw):
        self.writes.append((param.region, param.name, int(value)))
        self.state[param.name] = int(value)

    def get_parameter(self, param, index, *, keygroup=0, **_kw):
        return self.state.get(param.name, param.default
                              if param.default is not None else param.minimum)


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

        # Honour the key range, so --dry-run actually exercises
        # verify_isolation. A fake that sounds regardless of whether the
        # keygroup can play the note cannot fail the check that exists to
        # catch a program which is not the one being heard -- and a check
        # nothing can fail is decoration (RESOLUTION_NOTES §18).
        audible = (self.state.get("LONOTE", 0) <= note
                   <= self.state.get("HINOTE", 127))

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
        if not audible:
            gain *= 1e-4                      # silenced: 80 dB down
        mono = env * src * gain

        pan = s.get("PANPOS", 0) / 50.0
        left = mono * math.cos((pan + 1) * math.pi / 4)
        right = mono * math.sin((pan + 1) * math.pi / 4)

        # mkstemp, not mktemp: mktemp only invents a name, and nothing here
        # ever deleted the file it named. A dry run writes one WAV per sweep
        # point, so a test suite run leaked dozens into /tmp and eventually
        # filled the volume. The caller owns the file and removes it.
        if out_wav:
            path = out_wav
        else:
            handle, path = tempfile.mkstemp(suffix=".wav", prefix="s3ked-cal-")
            os.close(handle)
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

    try:
        rows = run_sweep(bridge, rig, sweep, program=args.program,
                         keygroup=args.keygroup, keep_dir=args.keep_wavs)
    finally:
        if hasattr(rig, "close"):
            rig.close()
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
