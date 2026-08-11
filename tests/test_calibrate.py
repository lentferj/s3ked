# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the calibration sweeps.

A sweep is a list of parameter writes aimed at a real machine, so the ways it
can be wrong are the ways a bench session gets wasted: a name that is not in
the table (the write goes nowhere, or worse, somewhere), a value outside the
parameter's range (rejected, or silently truncated), a neutraliser that
contradicts the thing being swept.

None of this can tell whether the *resulting numbers* are right -- only
hardware can. What it can do is make sure that when hardware finally arrives,
the evening is not spent finding typos.
"""

import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes"))

np = pytest.importorskip("numpy", reason="calibration is bench tooling")

import calibrate as cal                                    # noqa: E402

from s3k import params as p                                # noqa: E402


ALL = sorted(cal.SWEEPS)


@pytest.mark.parametrize("name", ALL)
def test_the_swept_parameter_exists_and_is_writable(name):
    sweep = cal.SWEEPS[name]
    param = p.lookup(sweep.param, sweep.region)
    assert param.writable, f"{sweep.param} cannot be written"


@pytest.mark.parametrize("name", ALL)
def test_every_swept_value_is_inside_the_parameter_range(name):
    """A value the machine will reject wastes the point; one it truncates
    silently produces a curve with a flat spot that looks like a finding."""
    sweep = cal.SWEEPS[name]
    param = p.lookup(sweep.param, sweep.region)
    for value in sweep.values:
        assert param.minimum <= value <= param.maximum, (
            f"{sweep.name}: {sweep.param}={value} is outside "
            f"[{param.minimum}..{param.maximum}]"
        )


@pytest.mark.parametrize("name", ALL)
def test_every_neutralised_parameter_exists_and_is_in_range(name):
    """The `prepare` list is where a sweep's real knowledge lives, and it is
    written from the spec by hand -- exactly the place a name gets misspelled
    and then silently does nothing."""
    sweep = cal.SWEEPS[name]
    for region, pname, value in sweep.prepare:
        param = p.lookup(pname, region)
        assert param.writable, f"{sweep.name}: {pname} is not writable"
        assert param.minimum <= value <= param.maximum, (
            f"{sweep.name}: neutraliser {pname}={value} is outside "
            f"[{param.minimum}..{param.maximum}]"
        )


@pytest.mark.parametrize("name", ALL)
def test_a_sweep_never_neutralises_the_parameter_it_sweeps(name):
    """Setting FILFRQ to a fixed value in `prepare` and then sweeping it would
    still 'work' -- the last write wins -- but any reader of the sweep would
    be misled about what is held still."""
    sweep = cal.SWEEPS[name]
    assert (sweep.region, sweep.param) not in {(r, n) for r, n, _v in sweep.prepare}


@pytest.mark.parametrize("name", ALL)
def test_the_reference_value_is_a_settable_value(name):
    """The reference take need NOT be one of the swept values -- the filter
    sweep references at FILFRQ 99 (wide open) while sweeping in steps of two,
    so 99 is an extra point rather than a measured one. What it must be is a
    value the parameter accepts."""
    sweep = cal.SWEEPS[name]
    if sweep.reference_value is None:
        return
    param = p.lookup(sweep.param, sweep.region)
    assert param.minimum <= sweep.reference_value <= param.maximum


@pytest.mark.parametrize("name", ALL)
def test_every_measurement_kind_is_implemented(name):
    """A typo here fails only after the first note has been recorded, which on
    a 6-minute sweep is a slow way to learn about it."""
    assert cal.SWEEPS[name].measure in cal._MEASUREMENTS


def test_an_unknown_measurement_is_refused_before_anything_is_read():
    """It must raise on the NAME, not fail later opening the recording: the
    point is to fail before a 6-minute sweep has played its first note."""
    with pytest.raises(KeyError):
        cal._measure("definitely-not-a-measurement", "/nonexistent", 0.0, 1.0)


def test_pan_is_the_only_sweep_that_demands_stereo():
    """…and it must demand it: balance_db raises on a mono recording, so a
    stereo flag left off is a run that fails at the first point."""
    assert [n for n in ALL if cal.SWEEPS[n].stereo] == ["pan"]


# --- the dry run is the end-to-end test -------------------------------------


def test_dry_run_recovers_the_curve_the_fake_machine_was_given():
    """The strongest check available without hardware.

    The synthetic machine is built with a filter corner of 30*e^(0.062*FILFRQ)
    hertz. If the sweep drives it, the recording is measured and the fit comes
    back with those coefficients, then every stage between "write a parameter"
    and "report a calibration curve" is doing its job -- and the only thing
    left untested is the sampler itself.
    """
    bridge = cal._SyntheticBridge()
    rig = cal._SyntheticRig(bridge.state)
    sweep = cal.SWEEPS["filter"]
    rows = cal.run_sweep(bridge, rig, sweep, verbose=False)
    summary = cal.summarise(sweep, rows)

    assert summary["usable"] >= 25
    assert summary["a"] == pytest.approx(30.0, rel=0.15)
    assert summary["b"] == pytest.approx(0.062, rel=0.10)
    assert summary["r2"] > 0.99
    assert summary["fit_trustworthy"] is True


def test_dry_run_applies_every_neutraliser_before_the_first_note():
    """Order matters: neutralising after the first point silently contaminates
    it, and one bad point at the end of a range is hard to spot in a fit."""
    bridge = cal._SyntheticBridge()
    rig = cal._SyntheticRig(bridge.state)
    sweep = cal.SWEEPS["amp-attack"]
    cal.run_sweep(bridge, rig, sweep, verbose=False)

    prepared = {(r, n) for r, n, _v in sweep.prepare}
    first_swept = next(i for i, (_r, name, _v) in enumerate(bridge.writes)
                       if name == sweep.param)
    written_before = {(r, n) for r, n, _v in bridge.writes[:first_swept]}
    assert prepared <= written_before


def test_a_sweep_reports_nan_rather_than_a_floor_when_it_cannot_measure():
    """The filter sweep's low end sits below the reference band, and those
    points must come back NaN. A floor value there would fit as a bend in the
    curve and be written up as a feature of the machine."""
    bridge = cal._SyntheticBridge()
    rig = cal._SyntheticRig(bridge.state)
    rows = cal.run_sweep(bridge, rig, cal.SWEEPS["filter"], verbose=False)
    low = [r["corner_hz"] for r in rows if r["value"] <= 10]
    assert low and all(math.isnan(v) for v in low)


# --- the sweep must put the program back ------------------------------------


class _RecordingBridge:
    """Remembers writes and reads back, so a restore can be checked."""

    def __init__(self, initial=None):
        self.state = dict(initial or {})
        self.writes = []

    def set_parameter(self, param, index, value, *, keygroup=0, **_kw):
        self.writes.append((param.name, value))
        self.state[param.name] = value

    def get_parameter(self, param, index, *, keygroup=0, **_kw):
        return self.state.get(param.name, 0)


def _sweep_touches(sweep):
    return [(r, n) for r, n, _v in sweep.prepare] + [(sweep.region, sweep.param)]


@pytest.mark.parametrize("name", ALL)
def test_snapshot_covers_every_parameter_the_sweep_moves(name):
    sweep = cal.SWEEPS[name]
    bridge = _RecordingBridge()
    saved = cal.snapshot_prepare(bridge, sweep, 0, 0)

    assert set(saved) == set(_sweep_touches(sweep)), name
    # The swept parameter itself must be in there, not just the neutralisers.
    assert (sweep.region, sweep.param) in saved


@pytest.mark.parametrize("name", ALL)
def test_restore_puts_every_touched_parameter_back(name):
    sweep = cal.SWEEPS[name]
    original = {n: 7 for _r, n, _v in sweep.prepare}
    original[sweep.param] = 7
    bridge = _RecordingBridge(original)

    saved = cal.snapshot_prepare(bridge, sweep, 0, 0)
    cal.apply_prepare(bridge, sweep, 0, 0, verbose=False)
    bridge.set_parameter(p.lookup(sweep.param, sweep.region), 0, 42)
    assert bridge.state != original, "the sweep must actually have changed things"

    failed = cal.restore_prepare(bridge, saved, 0, 0, verbose=False)

    assert failed == []
    for key, value in original.items():
        assert bridge.state[key] == value, f"{name}: {key} not restored"


def test_a_refused_restore_is_reported_not_swallowed():
    class _Stubborn(_RecordingBridge):
        """Accepts the neutralising write and silently drops the restore.

        Dropping both would leave the value untouched throughout, and the
        restore would trivially "succeed" -- which is how the first version of
        this test passed against a bridge that was refusing everything.
        """

        seen = 0

        def set_parameter(self, param, index, value, *, keygroup=0, **_kw):
            if param.name == "PANPOS":
                self.seen += 1
                if self.seen > 1:
                    return
            super().set_parameter(param, index, value, keygroup=keygroup)

    sweep = cal.SWEEPS["filter"]
    bridge = _Stubborn({n: 7 for _r, n, _v in sweep.prepare})
    saved = cal.snapshot_prepare(bridge, sweep, 0, 0)
    cal.apply_prepare(bridge, sweep, 0, 0, verbose=False)

    failed = cal.restore_prepare(bridge, saved, 0, 0, verbose=False)

    assert any("PANPOS" in f for f in failed)


def test_an_ambiguous_port_name_raises_rather_than_guessing():
    """A multi-port interface enumerates as several ports sharing a prefix."""
    rig = cal.Rig(midi_port="M4U XT")

    class _FakeOut:
        def get_ports(self):
            return [f"ESI M4U XT:ESI M4U XT MIDI {i} 64:{i-1}" for i in (1, 2, 3, 4)]

    import types
    fake = types.SimpleNamespace(MidiOut=lambda: _FakeOut())
    sys.modules["rtmidi"], real = fake, sys.modules.get("rtmidi")
    try:
        with pytest.raises(SystemExit) as excinfo:
            rig._midi_out()
        assert "matches 4 ports" in str(excinfo.value)
    finally:
        if real is not None:
            sys.modules["rtmidi"] = real


def test_a_terminating_signal_still_runs_the_restore():
    """`finally` does not survive SIGTERM; a handler that raises does.

    `timeout(1)` sends SIGTERM, and CPython exits on it without unwinding, so
    the restore is skipped and the program is left mid-sweep. That happened on
    real hardware before this guard existed.
    """
    import signal as _signal

    sweep = cal.SWEEPS["filter"]
    original = {n: 7 for _r, n, _v in sweep.prepare}
    original[sweep.param] = 7
    bridge = _RecordingBridge(original)

    class _Rig:
        def play_and_record(self, *a, **kw):
            os.kill(os.getpid(), _signal.SIGTERM)   # mid-sweep termination
            raise AssertionError("unreachable")

    with pytest.raises(KeyboardInterrupt):
        cal.run_sweep(bridge, _Rig(), sweep, program=0, keygroup=0, verbose=False)

    for key, value in original.items():
        assert bridge.state[key] == value, f"{key} not restored after SIGTERM"


def test_the_recorder_is_bounded_and_reaped():
    """An unbounded wait on jack_rec wedged the JACK server once already."""
    import subprocess as sp

    class _Hung:
        returncode = None
        killed = False

        def wait(self, timeout=None):
            if not self.killed:
                raise sp.TimeoutExpired("jack_rec", timeout or 0)
            return 0

        def poll(self):
            return None if not self.killed else 0

        def kill(self):
            self.killed = True

    hung = _Hung()
    rig = cal.Rig(midi_port="x")
    object.__setattr__(rig, "_cached_out", type("O", (), {
        "send_message": lambda *a, **k: None, "close_port": lambda *a: None})())

    import unittest.mock as mock
    with mock.patch.object(cal.subprocess, "Popen", return_value=hung), \
         mock.patch.object(cal.time, "sleep", lambda *_a: None):
        with pytest.raises(RuntimeError, match="did not exit"):
            rig.play_and_record(60, 0.1, out_wav="/tmp/never-written.wav")

    assert hung.killed, "a hung recorder must be killed, not left running"


def test_the_midi_port_is_opened_once_not_per_note():
    opened = []
    rig = cal.Rig(midi_port="x")
    rig._midi_out = lambda: (opened.append(1),
                             type("O", (), {"close_port": lambda *a: None})())[1]

    for _ in range(5):
        rig._port()

    assert len(opened) == 1, f"opened {len(opened)} ports for 5 notes"
    rig.close()


# --- isolation: is the program under test the one being heard? ---------------


class _LevelRig:
    """Returns a level that depends on whether the keygroup can sound."""

    def __init__(self, bridge, leak_db=None):
        self.bridge = bridge
        self.leak_db = leak_db      # level heard when the keygroup is silenced

    def play_and_record(self, note, hold, **_kw):
        lo = self.bridge.state.get("LONOTE", 0)
        hi = self.bridge.state.get("HINOTE", 127)
        audible = lo <= note <= hi
        self._level = -20.0 if audible else (self.leak_db if self.leak_db
                                             is not None else -80.0)
        return "/nonexistent.wav", 0.0, hold


def _patch_measure(monkeypatch, rig):
    monkeypatch.setattr(cal, "_measure",
                        lambda kind, wav, a, b, ref, **kw: (rig._level, ref))


def test_isolation_passes_when_silencing_the_keygroup_silences_the_sound(monkeypatch):
    bridge = _RecordingBridge({"LONOTE": 0, "HINOTE": 127})
    rig = _LevelRig(bridge)
    _patch_measure(monkeypatch, rig)

    drop = cal.verify_isolation(bridge, rig, 0, 0, note=60)

    assert drop >= 6.0
    # key range must be put back
    assert bridge.state["LONOTE"] == 0 and bridge.state["HINOTE"] == 127


def test_isolation_fails_when_something_else_is_sounding(monkeypatch):
    """The §18 case: another program answers the same note."""
    bridge = _RecordingBridge({"LONOTE": 0, "HINOTE": 127})
    rig = _LevelRig(bridge, leak_db=-20.3)      # silencing barely changes it
    _patch_measure(monkeypatch, rig)

    with pytest.raises(RuntimeError, match="isolation check FAILED"):
        cal.verify_isolation(bridge, rig, 0, 0, note=60)

    assert bridge.state["LONOTE"] == 0, "key range restored even on failure"


def test_isolation_error_names_the_fix():
    bridge = _RecordingBridge({"LONOTE": 0, "HINOTE": 127})
    rig = _LevelRig(bridge, leak_db=-20.0)
    import unittest.mock as mock
    with mock.patch.object(cal, "_measure",
                           side_effect=lambda *a, **k: (rig._level, None)):
        with pytest.raises(RuntimeError) as excinfo:
            cal.verify_isolation(bridge, rig, 0, 0, note=60)
    assert "PMCHAN" in str(excinfo.value)
    assert "§18" in str(excinfo.value) or "18" in str(excinfo.value)


def test_the_filter_sweep_note_and_reference_band_agree():
    """A reference band below the fundamental contains no source energy.

    The band defines 0 dB, so it must sit where the source actually has
    harmonics. The resident SAWTOOTH sounds at 261.6 Hz at note 60 and
    ~32.7 Hz at note 24; only the latter puts harmonics inside 50-100 Hz.
    Measured on hardware, RESOLUTION_NOTES §20.
    """
    sweep = cal.SWEEPS["filter"]
    lo, hi = sweep.ref_band
    f0 = 261.6 * (2 ** ((sweep.note - 60) / 12.0))
    assert f0 < hi, (
        f"note {sweep.note} sounds at {f0:.1f} Hz, at or above the top of the "
        f"reference band {lo}-{hi} Hz, which would leave the band with no "
        f"harmonic in it"
    )
    # At least one harmonic of f0 must land inside the band.
    assert any(lo <= f0 * k <= hi for k in range(1, 12)), (
        f"no harmonic of {f0:.1f} Hz falls inside {lo}-{hi} Hz")


# --- the fit model belongs to the sweep, not to the harness ------------------


def _rows(sweep, pairs):
    return [{"value": x, sweep.measure: y} for x, y in pairs]


def test_a_linear_relationship_is_reported_as_linear():
    """Forcing an exponential onto a straight line produced a plausible,
    wrong equation for KGTUNO. RESOLUTION_NOTES §21."""
    sweep = cal.SWEEPS["tuning"]
    pairs = [(1, -0.021), (2, 0.468), (4, 1.245), (8, 3.027),
             (16, 5.922), (32, 12.18), (50, 19.29)]

    out = cal.summarise(sweep, _rows(sweep, pairs))

    assert out["model"] == "linear"
    assert out["r2"] > 0.999
    assert abs(out["m"] - 0.39167) < 0.001
    assert "exp(" not in out["fit"]


def test_an_exponential_relationship_is_reported_as_exponential():
    sweep = cal.SWEEPS["filter"]
    pairs = [(50, 281.2), (55, 404.3), (60, 606.4), (65, 852.5),
             (70, 1187.0), (75, 1755.0), (80, 2566.0), (85, 3700.0)]

    out = cal.summarise(sweep, _rows(sweep, pairs))

    assert out["model"] == "exp"
    assert out["r2"] > 0.99
    assert "exp(" in out["fit"]


def test_both_shapes_are_always_reported():
    """The alternative is shown so a reader can see what was rejected."""
    sweep = cal.SWEEPS["filter"]
    out = cal.summarise(sweep, _rows(sweep, [(50, 281.2), (60, 606.4),
                                             (70, 1187.0), (80, 2566.0)]))
    assert set(out["fits"]) == {"exp", "linear"}
    assert all("r2" in v for v in out["fits"].values())


def test_a_declared_model_the_data_rejects_is_flagged_not_silently_swapped():
    """Declaring exp for a straight line must produce a complaint."""
    sweep = cal.SWEEPS["filter"]          # declares exp
    straight = [(10, 10.0), (20, 20.0), (30, 30.0), (40, 40.0), (50, 50.0)]

    out = cal.summarise(sweep, _rows(sweep, straight))

    assert out["model"] == "exp", "the declaration is honoured, not overridden"
    assert "fit_model_disagrees" in out
    assert "linear" in out["fit_model_disagrees"]


def test_the_linear_fit_keeps_non_positive_points():
    """Tuning and pan pass through zero; dropping those loses the intercept."""
    sweep = cal.SWEEPS["tuning"]
    pairs = [(-16, -6.2), (-8, -3.1), (0, 0.0), (8, 3.1), (16, 6.2)]

    out = cal.summarise(sweep, _rows(sweep, pairs))

    assert out["usable"] == 5, "negative and zero points must be kept"
    assert out["model"] == "linear"
    assert abs(out["c"]) < 0.1


def test_the_recorder_fires_note_off_inside_the_capture():
    """A release cannot be measured if note-off lands after the recording.

    The release sweep returned NaN at all fourteen points because the
    in-process recorder sent note-off once the capture had already finished.
    RESOLUTION_NOTES §22.
    """
    events = []

    class _Rec(cal._InProcessRecorder):
        def __init__(self):                      # no JACK server needed
            self._frames, self._armed = [], False

        def record(self, seconds, during=None, then=None, after=0.0):
            events.append(("start", 0.0))
            if during:
                during()
            if then:
                events.append(("then", after))
                then()
            events.append(("end", seconds))
            return None

    rec = _Rec()
    rec.record(5.0, during=lambda: events.append(("on", 0.0)),
               then=lambda: events.append(("off", 3.0)), after=3.0)

    names = [e[0] for e in events]
    assert names.index("off") < names.index("end"), \
        "note-off must be sent before the capture ends"
    assert names.index("on") < names.index("off")


def test_a_killed_recorder_does_not_leave_its_temp_file_behind():
    """A run that dies on a hung recorder used to leak the file it created."""
    import subprocess as sp
    import unittest.mock as mock

    created = []

    class _Hung:
        killed = False

        def wait(self, timeout=None):
            if not self.killed:
                raise sp.TimeoutExpired("jack_rec", timeout or 0)
            return 0

        def poll(self):
            return 0 if self.killed else None

        def kill(self):
            self.killed = True

    rig = cal.Rig(midi_port="x")
    object.__setattr__(rig, "_rec_failed", True)      # force the jack_rec path
    object.__setattr__(rig, "_cached_out", type("O", (), {
        "send_message": lambda *a, **k: None, "close_port": lambda *a: None})())

    real_mkstemp = cal.tempfile.mkstemp

    def _spy(*a, **kw):
        handle, path = real_mkstemp(*a, **kw)
        created.append(path)
        return handle, path

    with mock.patch.object(cal.subprocess, "Popen", return_value=_Hung()), \
         mock.patch.object(cal.time, "sleep", lambda *_a: None), \
         mock.patch.object(cal.tempfile, "mkstemp", _spy):
        with pytest.raises(RuntimeError, match="did not exit"):
            rig.play_and_record(60, 0.1)

    assert created, "the test must have exercised the mkstemp path"
    for path in created:
        assert not os.path.exists(path), f"leaked {path}"
