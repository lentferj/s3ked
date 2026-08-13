# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""``s3kcli``, driven through ``main()`` against the demo bridge."""

import pytest

from s3ked import cli


def run(capsys, *args):
    assert cli.main(list(args)) == 0
    return capsys.readouterr().out


def test_status(capsys):
    out = run(capsys, "--demo", "status")
    assert "exclusive channel" in out
    assert "sample words" in out
    # Deliberately absent -- the field does not survive contact with a real
    # machine (RESOLUTION_NOTES §10).
    assert "software version" not in out


def test_programs_lists_with_indices(capsys):
    out = run(capsys, "--demo", "programs")
    assert "BASS ROUND" in out
    assert out.splitlines()[0].startswith("num")


def test_samples(capsys):
    assert "KICK 1" in run(capsys, "--demo", "samples")


def test_get_decodes_the_value(capsys):
    out = run(capsys, "--demo", "get", "PRIORT", "0")
    assert "PRIORT = norm" in out


def test_get_shows_transcription_notes(capsys):
    """Prose the table could not reduce is exactly what a user needs here."""
    out = run(capsys, "--demo", "get", "OUTPUT", "0")
    assert "note:" in out


def test_header_dumps_a_whole_region(capsys):
    out = run(capsys, "--demo", "header", "program", "0")
    assert "PRNAME" in out and "PRIORT" in out


def test_header_filters_by_group(capsys):
    out = run(capsys, "--demo", "header", "keygroup", "2", "--group", "keygroup.env.1")
    assert "ATTAK1" in out
    assert "LONOTE" not in out


def test_set_is_refused_without_the_gate(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--demo", "set", "PRIORT", "3", "0"])
    assert "--allow-write" in str(excinfo.value)


def test_set_writes_and_reads_back(capsys):
    out = run(capsys, "--demo", "--allow-write", "set", "PRIORT", "3", "0")
    assert "PRIORT = hold" in out


def test_set_rejects_a_non_numeric_value(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--demo", "--allow-write", "set", "PRIORT", "loud", "0"])
    assert "not a number" in str(excinfo.value)


def test_set_refuses_read_only_parameters(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--demo", "--allow-write", "set", "GROUPS", "4", "0"])
    assert "read-only" in str(excinfo.value)


def test_unknown_parameter_is_an_error_not_a_traceback(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--demo", "get", "NOSUCH", "0"])
    assert "error:" in str(excinfo.value)


def test_params_needs_no_device(capsys):
    """Offline commands must never construct a bridge."""
    out = run(capsys, "params", "--region", "sample")
    assert "SHNAME" in out
    assert "program" not in out


def test_params_search(capsys):
    out = run(capsys, "params", "--search", "PRNAME")
    assert "PRNAME" in out
    assert "PRIORT" not in out


def test_groups_needs_no_device(capsys):
    assert "program.mods" in run(capsys, "groups", "--region", "program")


def test_ports_needs_no_device(capsys, monkeypatch):
    monkeypatch.setattr(
        "s3k.bridge.list_ports", lambda: (["In A"], ["Out B"]), raising=True
    )
    out = run(capsys, "ports")
    assert "In A" in out and "Out B" in out


def test_no_destructive_subcommand_is_exposed():
    """A shell is the wrong place for an irreversible, unconfirmed delete."""
    assert not {"delete", "del", "erase"} & set(cli._COMMANDS)


def test_demo_bridge_is_closed_even_on_error(monkeypatch):
    closed = {}

    from s3ked.demo import DemoBridge

    class Tracking(DemoBridge):
        def close(self):
            closed["yes"] = True
            super().close()

    monkeypatch.setattr("s3ked.demo.DemoBridge", Tracking)
    with pytest.raises(SystemExit):
        cli.main(["--demo", "get", "NOSUCH", "0"])
    assert closed.get("yes")


def test_set_writes_a_whole_temperament(capsys):
    """Twelve values, comma-separated, one per semitone.

    `set TEMPER -5` used to broadcast: C at -5 cents and every other note at
    -1, silently. It is now refused, and the list form is how the field is
    actually written.
    """
    # `set` prints its own read-back, which is the round trip. A separate
    # `get` would start a fresh demo machine and see the defaults.
    out = run(capsys, "--demo", "--allow-write", "set", "TEMPER",
              "0,-14,0,-2,16,0,-12,2,-10,0,-6,14", "0")
    assert "C# -14" in out and "B +14" in out and "cents" in out
    assert "E +16" in out and "G# -10" in out


def test_set_refuses_a_single_number_for_a_twelve_value_field(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--demo", "--allow-write", "set", "TEMPER", "-5", "0"])
    assert "12 values" in str(caught.value)


def test_an_equal_temperament_says_so(capsys):
    out = run(capsys, "--demo", "get", "TEMPER", "0")
    assert "equal temperament" in out
    assert "(raw (0, 0, 0" in out, "the twelve elements are shown as twelve"


def test_the_readme_examples_are_real(capsys):
    """A README with invented output is the staleness it warns about.

    Each of these is copied from the README's units section. If the rendering
    changes, this fails and the README gets updated with it.
    """
    out = run(capsys, "--demo", "get", "TEMPER", "0")
    assert out.startswith("TEMPER = equal temperament  (raw (0, 0, 0")

    out = run(capsys, "--demo", "get", "FILFRQ", "0")
    assert out.startswith("FILFRQ = 0 (?~6.46 Hz)  (raw 0)")

    out = run(capsys, "--demo", "--allow-write", "set", "FILFRQ", "500Hz", "0")
    assert "FILFRQ = 61 (~491 Hz)" in out

    out = run(capsys, "--demo", "--allow-write", "set", "ATTAK1", "250ms", "0")
    assert "ATTAK1 = 66 (~258 ms)" in out


def test_ports_fails_kindly_with_no_midi_backend(monkeypatch, capsys):
    """`ports` is the first thing a new user runs, and on a host with no ALSA
    sequencer -- a container, a headless box, a CI runner -- it answered with
    a traceback.

    MidiUnavailable subclasses RuntimeError, which main()'s offline branch
    does not catch; only its device-using branch did, and `ports` does not go
    through that. Guarded in the command itself rather than in main(), because
    s3k.bridge imports rtmidi at module scope and the CLI's import of it is
    deliberately lazy so `params` works with no MIDI stack at all.
    """
    import pytest
    import s3k.bridge as bridge_mod

    class Dead:
        def __init__(self, *a, **k):
            raise SystemError("error creating ALSA sequencer client object")

    monkeypatch.setattr(bridge_mod.rtmidi, "MidiIn", Dead)
    monkeypatch.setattr(bridge_mod.rtmidi, "MidiOut", Dead)

    with pytest.raises(SystemExit) as caught:
        cli.main(["ports"])

    message = str(caught.value)
    assert "no MIDI backend" in message
    assert "ALSA sequencer" in message, "must say what to do about it"
    assert "Traceback" not in message


def test_offline_commands_still_work_without_any_midi(monkeypatch, capsys):
    """The lazy import is the point: params must not need a MIDI stack."""
    import s3k.bridge as bridge_mod

    class Dead:
        def __init__(self, *a, **k):
            raise SystemError("no sequencer")

    monkeypatch.setattr(bridge_mod.rtmidi, "MidiIn", Dead)
    monkeypatch.setattr(bridge_mod.rtmidi, "MidiOut", Dead)

    assert cli.main(["params", "--region", "program"]) == 0
    assert capsys.readouterr().out.strip(), "params printed nothing"
