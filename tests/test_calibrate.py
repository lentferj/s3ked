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
