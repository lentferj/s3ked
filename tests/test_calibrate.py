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

import inspect
import json
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


# --- the recorder's block assembly -----------------------------------------
#
# These exist because the naive assembly raced JACK's own process callback and
# took a sweep down four takes in: it counted blocks once per channel, so a
# callback landing between channel 0 and channel 1 left them a period apart.


def test_blocks_become_one_array_per_channel():
    import numpy as np

    blocks = [[np.ones(4), np.zeros(4)] for _ in range(3)]
    data = cal._stack_blocks(np, blocks, 2)

    assert data.shape == (12, 2)
    assert (data[:, 0] == 1).all() and (data[:, 1] == 0).all()


def test_a_trailing_block_missing_a_channel_is_dropped_not_padded():
    """The exact shape of the race: a partial append seen mid-read."""
    import numpy as np

    blocks = [[np.ones(4), np.zeros(4)], [np.ones(4)]]
    data = cal._stack_blocks(np, blocks, 2)

    assert data.shape == (4, 2), "the half-written block must not reach the wav"


def test_every_channel_is_cut_at_the_same_place():
    import numpy as np

    blocks = [[np.ones(4), np.zeros(4)], [np.ones(4), np.zeros(2)]]
    data = cal._stack_blocks(np, blocks, 2)

    assert data.shape[0] == 6
    assert data.shape == (6, 2)


def test_no_blocks_at_all_is_an_empty_capture_not_a_crash():
    import numpy as np

    data = cal._stack_blocks(np, [], 2)
    assert data.shape == (0, 2)


# --- the response check ----------------------------------------------------
#
# verify_isolation asks "is this the program I am hearing?"; this asks "is this
# parameter connected to what I am measuring?". The second question cost three
# sweeps: a dropped MODSFILT1 line meant the depth parameter modulated whatever
# source the program already held, and eleven SUSTN2 values came back within
# 1 Hz of each other.


def test_a_connected_parameter_passes_and_reports_its_swing():
    seen = {}
    change = cal.verify_responds(
        lambda: seen["v"] * 10.0, 0, 99, lambda v: seen.__setitem__("v", v),
        min_change=100.0,
    )
    assert change == 990.0


def test_a_parameter_that_does_not_move_the_measurement_is_refused():
    with pytest.raises(RuntimeError, match="response check FAILED"):
        cal.verify_responds(lambda: 1141.0, 0, 99, lambda _v: None,
                            label="SUSTN2", min_change=100.0)


def test_the_refusal_names_the_routing_as_the_thing_to_check():
    """The failure is almost never the depth; it is the unrouted source."""
    with pytest.raises(RuntimeError, match="modulation SOURCE is routed"):
        cal.verify_responds(lambda: 0.0, 0, 99, lambda _v: None,
                            min_change=1.0)


def test_a_change_exactly_at_the_threshold_passes():
    vals = iter([0.0, 5.0])
    assert cal.verify_responds(lambda: next(vals), 0, 99, lambda _v: None,
                               min_change=5.0) == 5.0


def test_a_nan_measurement_is_a_failure_not_a_pass():
    """`not (nan >= x)` is True; that is deliberate, and worth pinning."""
    with pytest.raises(RuntimeError):
        cal.verify_responds(lambda: float("nan"), 0, 99, lambda _v: None,
                            min_change=1.0)


def test_the_direction_of_the_change_does_not_matter():
    vals = iter([900.0, 100.0])
    assert cal.verify_responds(lambda: next(vals), 0, 99, lambda _v: None,
                               min_change=500.0) == 800.0


# --- the snapshot on disk --------------------------------------------------
#
# snapshot_prepare keeps originals in memory, which is enough until the process
# does not reach its finally -- and then the only record of what the program
# used to be dies with it. That happened: a second SIGTERM interrupted a
# restore, and fourteen fields could not be put back because nothing outside
# the process knew what they had been.


def test_a_snapshot_survives_the_process(tmp_path):
    saved = {("keygroup", "FILFRQ"): 63, ("program", "PMCHAN"): 0}
    path = cal.write_snapshot(str(tmp_path / "snap.json"), saved,
                              note="before the filter sweep")

    assert cal.read_snapshot(path) == saved


def test_a_snapshot_records_when_and_why(tmp_path):
    path = cal.write_snapshot(str(tmp_path / "s.json"), {("program", "X"): 1},
                              note="before the filter sweep")
    payload = json.loads(Path(path).read_text())

    assert payload["note"] == "before the filter sweep"
    assert payload["written"]


def test_text_values_survive_the_round_trip(tmp_path):
    """Sample names are the one non-numeric thing a sweep moves."""
    saved = {("keygroup", "SNAME1"): "SINE"}
    path = cal.write_snapshot(str(tmp_path / "s.json"), saved)

    assert cal.read_snapshot(path) == saved


def test_no_partial_file_is_left_behind(tmp_path):
    cal.write_snapshot(str(tmp_path / "s.json"), {("program", "X"): 1})

    assert [f.name for f in tmp_path.iterdir()] == ["s.json"], (
        "a truncated snapshot looks authoritative and is worse than none"
    )


def test_writing_over_an_old_snapshot_replaces_it(tmp_path):
    path = str(tmp_path / "s.json")
    cal.write_snapshot(path, {("program", "X"): 1})
    cal.write_snapshot(path, {("program", "Y"): 2})

    assert cal.read_snapshot(path) == {("program", "Y"): 2}


def test_the_isolation_failure_message_does_not_claim_a_single_cause():
    """Two recordings matching means either a collision OR a silent keygroup.

    The check cannot tell them apart, and once said the wrong thing with
    total confidence: a genuinely inaudible keygroup at FILFRQ 40 was
    reported as something else sounding on the channel.
    """
    source = inspect.getdoc(cal.verify_isolation)
    assert "AFTER neutralising" in source
    assert "already inaudible" in source


# --- replication -----------------------------------------------------------
#
# Every sweep in this project measured each condition once, so no result ever
# carried an error bar. Three separate runs at one question returned
# "inconclusive" for that reason alone; three captures per condition settled
# it, the within-condition scatter proving to be 0.65%.


def test_a_replicated_measurement_carries_its_own_scatter():
    r = cal.replicate(iter([2.215, 2.210, 2.230]).__next__, repeats=3)

    assert len(r) == 3
    assert r.mean == pytest.approx(2.2183, abs=1e-3)
    assert 0.005 < r.sd < 0.015
    assert r.relative_sd < 0.01


def test_a_single_reading_has_no_uncertainty_to_report():
    """One measurement is a number, not a measurement with an error bar."""
    r = cal.replicate(lambda: 1.0, repeats=1)

    assert r.mean == 1.0
    assert r.sd != r.sd, "sd of one sample must be NaN, not zero"


def test_nan_readings_are_dropped_rather_than_poisoning_the_mean():
    r = cal.replicate(iter([2.0, float("nan"), 2.2]).__next__, repeats=3)

    assert len(r) == 2
    assert r.mean == pytest.approx(2.1)


# --- telling signal from noise ---------------------------------------------


def _group(*values):
    return cal.Replicated(list(values))


def test_a_real_difference_is_called_a_difference():
    groups = [_group(10.0, 10.1, 9.9),
              _group(20.0, 20.1, 19.9),
              _group(30.0, 30.1, 29.9)]

    _between, _pooled, ratio, verdict = cal.beyond_noise(groups)

    assert verdict == "varies"
    assert ratio > 3


def test_the_attak2_depth_residual_is_marginal_not_established():
    """The real ATTAK2 data, and it does NOT clear a 3x bar.

    Written expecting "varies" -- the run that produced it used F > 4, i.e.
    a ratio above 2, and reported the residual span-dependence as real. At a
    3x bar the same data is undecidable at 2.68.

    Kept as a test because the finding was committed before this tool existed
    to check it: the rejection of constant-RATE is overwhelming (ratio ~30),
    but the small residual that survives is not itself established.
    """
    groups = [_group(2.215, 2.210, 2.230),
              _group(2.280, 2.245, 2.240),
              _group(2.305, 2.290, 2.295)]

    _between, _pooled, ratio, verdict = cal.beyond_noise(groups)

    assert verdict == "undecidable"
    assert 2.0 < ratio < 3.0


def test_scatter_smaller_than_the_noise_is_called_noise():
    groups = [_group(1.00, 1.30, 0.70),
              _group(1.02, 0.72, 1.28),
              _group(0.98, 1.31, 0.69)]

    _b, _p, _r, verdict = cal.beyond_noise(groups)

    assert verdict == "within noise"


def test_the_middle_ground_is_undecidable_rather_than_decided():
    """The fault this replaces: a rule that always produced a verdict.

    An earlier version picked whichever spread was smaller and announced it,
    so 72.2% against 71.9% was reported as a finding.
    """
    groups = [_group(1.00, 1.10, 0.90),
              _group(1.15, 1.25, 1.05),
              _group(1.30, 1.40, 1.20)]

    _b, _p, ratio, verdict = cal.beyond_noise(groups)

    assert verdict == "undecidable"
    assert 1 / 3 < ratio < 3


def test_one_condition_cannot_be_compared_with_itself():
    _b, _p, _r, verdict = cal.beyond_noise([_group(1.0, 1.1, 0.9)])
    assert verdict == "undecidable"


def test_unreplicated_groups_cannot_settle_anything():
    """Single readings give no within-condition scatter, so no yardstick."""
    _b, _p, _r, verdict = cal.beyond_noise([_group(1.0), _group(2.0)])
    assert verdict == "undecidable"


def test_a_degenerate_repeat_is_documented_as_a_trap():
    """sd=0 means the repeat resampled nothing, not that the rig is perfect.

    A FILQ sweep returned sd = 0.000 dB on fifteen of sixteen conditions --
    a fixed digital sample, unchanged settings, an aligned recorder. The
    comparison then called a ratio of 3645 "varies", which was arithmetic
    rather than evidence.
    """
    doc = " ".join(inspect.getdoc(cal.replicate).split())
    assert "only measures the noise sources the repeat actually re-runs" in doc
    assert "worse than none" in doc


def test_identical_repeats_are_now_refused_rather_than_believed():
    """This used to assert the failure; now it asserts the guard.

    A zero error bar makes any difference infinitely significant, so the
    comparison refuses the input instead of returning a verdict on it. The
    replicated value itself still reports sd 0.0 -- that is a true statement
    about the numbers it was given, and the refusal belongs at the point of
    comparison, where the consequence is.
    """
    r = cal.replicate(lambda: 42.0, repeats=3)
    assert r.sd == 0.0

    with pytest.raises(ValueError, match="identical replicates"):
        cal.beyond_noise([cal.Replicated([1.0, 1.0, 1.0]),
                          cal.Replicated([1.1, 1.1, 1.1])])


def test_one_frozen_condition_is_refused_even_when_the_pool_is_healthy():
    """A pooled floor is not a guard.

    mpc2emu fed the §33 FILQ shape -- fifteen frozen conditions and one
    genuine -- to their version of this and got "differ" at a ratio of 952.
    Mine returned "varies" at 276. The single genuine condition keeps the
    pooled scatter above zero, so a pooled check never fires.
    """
    groups = [cal.Replicated([1.0 + i * 0.3] * 3) for i in range(15)]
    groups.append(cal.Replicated([5.0, 5.02, 4.98]))

    with pytest.raises(ValueError, match="identical replicates"):
        cal.beyond_noise(groups)


def test_the_refusal_says_to_re_capture_not_to_re_analyse():
    with pytest.raises(ValueError, match="RE-CAPTURE"):
        cal.beyond_noise([cal.Replicated([1.0, 1.0, 1.0]),
                          cal.Replicated([2.0, 2.1, 1.9])])


def test_genuine_replicates_still_pass():
    groups = [cal.Replicated([1.0, 1.1, 0.9]), cal.Replicated([5.0, 5.1, 4.9])]
    _b, _p, ratio, verdict = cal.beyond_noise(groups)
    assert verdict == "varies" and ratio > 3


def test_the_bar_is_documented_as_an_effect_size():
    """It converges with n, so more replicates cannot resolve an undecidable."""
    doc = " ".join(inspect.getdoc(cal.beyond_noise).split())
    assert "EFFECT SIZE, not a significance test" in doc
    assert "adding replicates will not resolve" in doc


# --- the corner tracker -----------------------------------------------------
#
# Built for RESOLUTION_NOTES §57. Every test here is a failure the real
# instrument produced against hardware before it worked, rebuilt synthetically
# so it cannot come back quietly.

def _two_pole_db(np, freqs, fc, zeta):
    x = np.asarray(freqs) / float(fc)
    return -10 * np.log10((1 - x ** 2) ** 2 + (2 * zeta * x) ** 2)


def _sawtooth_source_db(np, freqs):
    """Falls 6 dB/octave, like the source the tracker actually sees."""
    return -20 * np.log10(np.maximum(np.asarray(freqs), 1.0) / 100.0)


def _spectra(np, freqs, fc, *, frames=20, floor_db=-80.0, q_zeta=0.03):
    """(reference, run) log spectrograms of one static corner."""
    src = _sawtooth_source_db(np, freqs)
    ref = src + _two_pole_db(np, freqs, fc, 0.47)
    run = src + _two_pole_db(np, freqs, fc, q_zeta)
    ref = np.maximum(ref, floor_db)
    run = np.maximum(run, floor_db)
    return np.tile(ref, (frames, 1)), np.tile(run, (frames, 1))


def test_corner_tracker_finds_a_static_corner():
    np = pytest.importorskip("numpy")
    freqs = np.arange(120.0, 9000.0, 25.0)
    for fc in (600.0, 930.0, 1900.0, 2900.0, 4500.0):
        ref, run = _spectra(np, freqs, fc)
        got = cal.corner_from_difference(ref, run, freqs)
        assert abs(float(np.median(got)) / fc - 1) < 0.05, fc
        assert float(np.std(got)) < 0.02 * fc, f"{fc} not steady"


def test_corner_tracker_beats_the_raw_argmax_at_a_high_corner():
    """The failure that started it: 125 Hz reported for a 2875 Hz corner.

    A sawtooth falls 6 dB/octave, so at a high corner the source is further
    below its own fundamental than the resonance is above the local level, and
    the spectrum's maximum stays at the bottom. Differencing removes the slope;
    without it the answer is the fundamental, confidently.
    """
    np = pytest.importorskip("numpy")
    freqs = np.arange(120.0, 9000.0, 25.0)
    ref, run = _spectra(np, freqs, 2900.0)

    naive = float(freqs[int(np.argmax(run[0]))])
    assert naive < 500.0, "the raw argmax should fail here, or this proves nothing"

    tracked = float(np.median(cal.corner_from_difference(ref, run, freqs)))
    assert abs(tracked / 2900.0 - 1) < 0.05


def test_corner_tracker_gate_rejects_the_difference_of_two_noise_floors():
    """Above a low corner both captures are at the floor and their difference
    is random. Ungated, the median stayed right and the scatter was 630%."""
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(11)
    freqs = np.arange(120.0, 9000.0, 25.0)
    ref, run = _spectra(np, freqs, 600.0, frames=40, floor_db=-70.0)
    noise = lambda: rng.normal(0.0, 6.0, size=ref.shape)
    ref_n, run_n = ref + noise(), run + noise()

    gated = cal.corner_from_difference(ref_n, run_n, freqs, gate_db=45.0)
    ungated = cal.corner_from_difference(ref_n, run_n, freqs, gate_db=400.0)

    assert float(np.std(gated)) < float(np.std(ungated)) / 3
    assert abs(float(np.median(gated)) / 600.0 - 1) < 0.10


def test_corner_tracker_follows_a_moving_corner():
    """The whole point: a differenced STATIC measurement cannot do this."""
    np = pytest.importorskip("numpy")
    freqs = np.arange(120.0, 9000.0, 25.0)
    sweep = np.geomspace(700.0, 3500.0, 30)
    ref = np.stack([_spectra(np, freqs, fc, frames=1)[0][0] for fc in sweep])
    run = np.stack([_spectra(np, freqs, fc, frames=1)[1][0] for fc in sweep])

    got = cal.corner_from_difference(ref, run, freqs)
    assert np.all(np.diff(got) >= -50), "should rise monotonically"
    assert abs(float(got[0]) / 700.0 - 1) < 0.08
    assert abs(float(got[-1]) / 3500.0 - 1) < 0.08


def test_running_median_keeps_an_excursion_a_percentile_span_would_lose():
    """A corner that moves briefly and then sits still.

    max-minus-min of the smoothed track recovers the excursion; the 5th-to-95th
    percentile puts both ends on the plateau and reports a fraction of it.
    """
    np = pytest.importorskip("numpy")
    trace = np.concatenate([np.linspace(0.0, 2.0, 20), np.full(180, 2.0)])
    trace[57] = 9.0                                   # one stray frame
    smoothed = cal.running_median(trace, 5)

    assert smoothed.max() - smoothed.min() == pytest.approx(2.0, abs=0.15)
    assert float(trace.max() - trace.min()) > 8.0, "unsmoothed, the stray wins"
    pct = float(np.percentile(trace, 95) - np.percentile(trace, 5))
    assert pct < 1.0, "the percentile span understates the transient"


def test_frame_spectra_drops_silence_and_returns_matching_shapes():
    np = pytest.importorskip("numpy")
    sr = 44100
    t = np.arange(0, int(sr * 0.5)) / sr
    tone = 0.2 * np.sin(2 * np.pi * 440 * t)
    samples = np.concatenate([np.zeros(sr // 4), tone])

    times, rows, freqs = cal.frame_spectra(samples, sr)
    assert len(times) == len(rows) > 10
    assert rows.shape[1] == len(freqs)
    assert times.min() > 0.2, "the leading silence should be dropped"


# --- refusing a frozen series -----------------------------------------------

def test_verify_varies_accepts_a_real_law():
    np = pytest.importorskip("numpy")
    ok, msg = cal.verify_varies([0.2, 0.29, 0.55, 1.25, 3.01], label="t90")
    assert ok and "5 distinct" in msg


@pytest.mark.parametrize("series,label", [
    ([-0.150, -0.150, -0.150, -0.150, -0.150], "PANDEL onset"),
    ([23.19, 23.19, 23.19, 23.19, 23.19], "resonance peak"),
    ([200, 200, 200, 200, 200, 200, 200, 200], "frames after note-off"),
])
def test_verify_varies_refuses_each_frozen_series_this_project_produced(series, label):
    """All three were reported as findings before being caught by hand."""
    np = pytest.importorskip("numpy")
    ok, msg = cal.verify_varies(series, label=label)
    assert not ok
    assert "FROZEN" in msg and label in msg


def test_verify_varies_refuses_two_distinct_values_as_well():
    """Two values across six settings is nearly as suspicious as one."""
    np = pytest.importorskip("numpy")
    ok, _ = cal.verify_varies([1.0, 1.0, 1.0, 2.0, 2.0, 2.0])
    assert not ok


def test_verify_varies_ignores_nan_rather_than_counting_it():
    """A detector that failed to read is a different fault, not a frozen one."""
    np = pytest.importorskip("numpy")
    ok, _ = cal.verify_varies([float("nan"), 0.2, 0.5, 1.1, 2.4])
    assert ok
    ok, msg = cal.verify_varies([float("nan"), float("nan"), 3.0])
    assert not ok and "nothing to compare" in msg


def test_verify_varies_says_what_it_cannot_catch():
    """The docstring carries the boundary, because that is where it is read.

    Five of six known measurement-fault shapes are mechanical and one is not.
    A wrong-stage failure produces a true measurement of a condition nobody
    wanted, so there is no statistic that finds it -- and a green run from
    this function is not clearance.
    """
    doc = cal.verify_varies.__doc__
    assert "WRONG STAGE" in doc
    assert "not clearance" in doc
