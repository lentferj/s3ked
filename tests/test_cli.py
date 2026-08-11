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
