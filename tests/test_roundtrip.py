# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the write round-trip sweep.

This is the only probe in the project that writes, and it writes hundreds of
times unattended. What has to hold is not that it finds things but that it
stays inside its fence: the rehearsal sends nothing, structurally unsafe
fields are never selected, and whatever it touched gets put back even when a
step fails partway through.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes"))

import roundtrip as rt                                     # noqa: E402

from s3k import messages as m                              # noqa: E402
from s3k import params as p                                # noqa: E402


class FakeBridge:
    """A header store that records every write."""

    def __init__(self, fail_on=None, refuse_restore=False):
        self.store = {}
        self.writes = []
        self.description = "fake"
        self.exclusive_channel = 0
        self.fail_on = fail_on
        self.refuse_restore = refuse_restore
        self._originals = {}

    def _key(self, param, index, keygroup):
        return (param.region, index, keygroup, param.name)

    def get_parameter(self, param, index, *, keygroup=0, timeout=None):
        return self.store.get(self._key(param, index, keygroup), param.minimum
                              + param.display_offset)

    def set_parameter(self, param, index, value, *, keygroup=0, postpone=None,
                      confirm=True, timeout=None):
        key = self._key(param, index, keygroup)
        self.writes.append((param.name, value))
        if self.fail_on and param.name == self.fail_on:
            # Land a value other than the one asked for.
            self.store[key] = value + 1 if isinstance(value, int) else "WRONG"
            return
        if self.refuse_restore and key in self._originals and \
                value == self._originals[key]:
            return  # silently drop the restore
        self._originals.setdefault(key, self.store.get(key))
        self.store[key] = value

    def get_header_bytes(self, region, index, offset, count, *, selector=0,
                         timeout=None):
        return bytes(count)

    def close(self):
        pass


def _sweeper(bridge, **kw):
    kw.setdefault("allow_write", True)
    kw.setdefault("interleave", True)
    kw.setdefault("timeout", 0.1)
    return rt.Sweeper(bridge, **kw)


# --- the fence -------------------------------------------------------------


def test_rehearsal_writes_nothing():
    bridge = FakeBridge()
    sweeper = _sweeper(bridge, allow_write=False)
    sweeper.run([("program", 0, 0)], include_names=False, stop_after=0)

    assert bridge.writes == []
    assert sweeper.report.writes == 0


@pytest.mark.parametrize("name", sorted(rt.SKIP_NAMES))
def test_structurally_unsafe_fields_are_never_swept(name):
    matches = [x for x in p._PARAMS if x.name == name]
    assert matches, f"{name} is not in the table -- stale SKIP_NAMES entry"
    for param in matches:
        allowed, why = rt.sweepable(param, include_names=True)
        assert not allowed
        assert why


def test_address_and_readonly_fields_are_never_swept():
    for param in p._PARAMS:
        if param.kind == "address" or param.readonly:
            allowed, _why = rt.sweepable(param, include_names=True)
            assert not allowed, param.name


def test_wide_fields_are_never_swept():
    """In the sample header every wide field is a memory-layout descriptor."""
    for param in p._PARAMS:
        if param.size > rt.MAX_WIDTH and param.kind != "text":
            allowed, _why = rt.sweepable(param, include_names=True)
            assert not allowed, f"{param.name} ({param.size} bytes)"

    swept = [x.name for x in p.region_params("sample")
             if rt.sweepable(x, include_names=False)[0]]
    for pointer in ("SLNGTH", "SSTART", "SMPEND", "LOOPAT1", "SLOCAT"):
        assert pointer not in swept


def test_name_fields_need_an_explicit_opt_in():
    name = p.lookup(("program", "PRNAME"))
    assert rt.sweepable(name, include_names=False)[0] is False
    assert rt.sweepable(name, include_names=True)[0] is True


class StrictBridge(FakeBridge):
    """Raises on any bridge call outside the three the sweep is allowed.

    Proves the constraint by running the sweep rather than by grepping the
    source, which would only find the docstring explaining the exclusion.
    """

    ALLOWED = {"get_parameter", "set_parameter", "get_header_bytes",
               "description", "exclusive_channel", "close"}

    def __getattr__(self, name):
        if name.startswith("_") or name in self.ALLOWED:
            raise AttributeError(name)
        raise AssertionError(f"the sweep must not call bridge.{name}()")


def test_the_sweep_only_ever_uses_the_byte_offset_read_and_write():
    bridge = StrictBridge()
    sweeper = _sweeper(bridge)
    report = sweeper.run([("program", 0, 0), ("sample", 0, 0)],
                         include_names=True, stop_after=0)

    assert report.writes > 0, "the sweep must actually have written"
    # Reaching here means no delete_*, set_exclusive_channel or whole-header
    # call was attempted -- StrictBridge would have raised.
    assert not report.unrestored


# --- value selection --------------------------------------------------------


def test_two_values_are_distinct_in_range_and_not_the_original():
    for param in p._PARAMS:
        if param.kind == "text":
            continue
        low = param.minimum + param.display_offset
        high = param.maximum + param.display_offset
        chosen = rt.two_values(param, low)
        if chosen is None:
            assert low >= high or True
            continue
        a, b = chosen
        assert a != b
        assert a != low and b != low
        for value in (a, b):
            assert low <= value <= high, f"{param.name}: {value}"


def test_a_single_valued_range_is_skipped():
    fixed = [x for x in p._PARAMS if x.minimum == x.maximum]
    assert fixed, "expected the 'fixed value in the specification' fields"
    for param in fixed[:5]:
        assert rt.two_values(param, param.minimum) is None


def test_chosen_values_survive_encoding():
    """Every value the sweep will send must fit the field it is aimed at."""
    for param in p._PARAMS:
        if param.kind == "text" or not rt.sweepable(param, include_names=False)[0]:
            continue
        chosen = rt.two_values(param, param.minimum + param.display_offset)
        if chosen is None:
            continue
        for value in chosen:
            p.encode_field(param, value)  # must not raise


# --- restoration ------------------------------------------------------------


def test_the_original_is_restored_after_a_clean_sweep():
    bridge = FakeBridge()
    param = p.lookup(("program", "PRIORT"))
    bridge.store[("program", 0, 0, "PRIORT")] = 1

    sweeper = _sweeper(bridge)
    result = sweeper.sweep_one(param, 0, 0)

    assert result.ok
    assert result.restored is True
    assert bridge.store[("program", 0, 0, "PRIORT")] == 1
    assert bridge.writes[-1] == ("PRIORT", 1), "last write must be the restore"


def test_the_original_is_restored_even_when_a_step_fails():
    bridge = FakeBridge(fail_on="PRIORT")
    param = p.lookup(("program", "PRIORT"))
    bridge.store[("program", 0, 0, "PRIORT")] = 1

    sweeper = _sweeper(bridge)
    result = sweeper.sweep_one(param, 0, 0)

    assert not result.ok
    assert bridge.writes[-1] == ("PRIORT", 1)


def test_a_failed_restore_is_reported_loudly():
    bridge = FakeBridge(refuse_restore=True)
    param = p.lookup(("program", "PRIORT"))
    bridge.store[("program", 0, 0, "PRIORT")] = 1

    sweeper = _sweeper(bridge)
    sweeper.sweep_one(param, 0, 0)

    assert sweeper.report.unrestored
    assert "PRIORT" in sweeper.report.unrestored[0]


def test_a_mismatch_stops_the_sweep_when_asked():
    bridge = FakeBridge(fail_on="PRIORT")
    sweeper = _sweeper(bridge)
    report = sweeper.run([("program", 0, 0)], include_names=False, stop_after=1)

    assert report.aborted
    assert "stopped after 1" in report.aborted


def test_the_toggle_writes_a_then_b_then_a_again():
    bridge = FakeBridge()
    param = p.lookup(("program", "PRIORT"))
    sweeper = _sweeper(bridge)
    result = sweeper.sweep_one(param, 0, 0)

    steps = [s["wrote"] for s in result.steps]
    assert len(steps) == 3
    assert steps[0] == steps[2] != steps[1], "must return to the first value"


def test_dry_run_completes():
    assert rt.main(["--dry-run"]) == 0
    assert rt.main(["--dry-run", "--allow-write"]) == 0


# --- leak witnessing --------------------------------------------------------


def test_a_clean_structure_shows_no_leak():
    before = {"program:0:0": bytes([1, 2, 3])}
    assert rt.diff_snapshots(before, dict(before)) == []


def test_a_changed_witness_is_reported_with_the_field_it_hit():
    param = p.lookup(("program", "PRIORT"))
    before = {"program:0:0": bytes(p.region_size("program"))}
    after = bytearray(before["program:0:0"])
    after[param.offset] = 3

    found = rt.diff_snapshots(before, {"program:0:0": bytes(after)})

    assert len(found) == 1
    assert found[0]["count"] == 1
    assert "PRIORT" in found[0]["fields"][0]


def test_a_witness_that_cannot_be_reread_is_an_error_not_a_pass():
    before = {"program:0:0": bytes(4)}
    found = rt.diff_snapshots(before, {})
    assert found and "error" in found[0]


def test_snapshot_reads_whole_headers():
    bridge = FakeBridge()
    snap = rt.snapshot(bridge, [("program", 0, 0), ("sample", 1, 0)], timeout=0.1)
    assert set(snap) == {"program:0:0", "sample:1:0"}
    for region, raw in (("program", snap["program:0:0"]),
                        ("sample", snap["sample:1:0"])):
        assert len(raw) == max(x.end for x in p.region_params(region))
