# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the throttle-floor probe.

The probe's job is to find the gap at which a device starts dropping writes,
so the things that must hold are about the *search*: that it walks downward,
stops at the first failure rather than pressing on into a flood, and counts
acknowledgements rather than inferring success from a final value -- which
only ever reveals a lost last write.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes"))

import throttle as th                                      # noqa: E402

from s3k import messages as m                              # noqa: E402


class _Out:
    def __init__(self):
        self._gap = self._write_gap = self._owed = 0.0


class FakeBridge:
    """Fails every exchange once the gap drops below `breaks_below`."""

    def __init__(self, breaks_below=0.0, replies=None):
        self.out = _Out()
        self.description = "fake"
        self.exclusive_channel = 0
        self.breaks_below = breaks_below
        self.value = 1
        self.queue = list(replies or [])

    def _too_fast(self):
        return self.out._gap < self.breaks_below

    def get_parameter(self, param, index, *, keygroup=0, timeout=None):
        if self._too_fast():
            raise RuntimeError("timeout")
        return self.value

    def set_parameter(self, param, index, value, *, keygroup=0, confirm=True,
                      postpone=None, timeout=None):
        if self._too_fast():
            raise RuntimeError("timeout")
        self.value = value

    def status(self, *, timeout=None):
        return object()

    def _drain(self):
        pass

    class _In:
        def __init__(self, outer):
            self.outer = outer

        def get_message(self):
            return self.outer.queue.pop(0) if self.outer.queue else None

    @property
    def inp(self):
        return FakeBridge._In(self)

    def close(self):
        pass


def _probe(bridge, count=3):
    return th.Throttle(bridge, program=0, timeout=0.05, count=count)


# --- the search ------------------------------------------------------------


def test_the_ladder_descends():
    """A walk that went upward could never find a floor."""
    assert list(th.LADDER) == sorted(th.LADDER, reverse=True)
    assert th.LADDER[-1] == 0.0


def test_the_health_check_gap_is_slower_than_anything_tried():
    assert th.SAFE_GAP > max(th.LADDER)


def test_the_walk_stops_at_the_first_failure():
    """It must not keep flooding a device that has already started dropping."""
    bridge = FakeBridge(breaks_below=0.010)
    probe = _probe(bridge)
    param = type("P", (), {"region": "program", "offset": 0, "size": 1})()

    floor = probe.walk("read", probe.stage_reads, param, 1)

    assert floor == 0.010
    tried = [level.gap for level in probe.report.levels]
    assert tried == [0.050, 0.025, 0.010, 0.005]
    assert probe.report.levels[-1].failures > 0


def test_a_walk_that_never_fails_reaches_zero():
    bridge = FakeBridge(breaks_below=0.0)
    probe = _probe(bridge)
    param = type("P", (), {"region": "program", "offset": 0, "size": 1})()

    assert probe.walk("read", probe.stage_reads, param, 1) == 0.0
    assert len(probe.report.levels) == len(th.LADDER)


def test_a_level_is_not_ok_if_the_device_stopped_answering():
    level = th.Level(0.01, "x", attempts=10, failures=0, seconds=1.0)
    assert level.ok
    level.healthy_after = False
    assert not level.ok


def test_rate_is_attempts_per_second():
    level = th.Level(0.01, "x", attempts=40, seconds=2.0)
    assert level.rate == 20.0
    assert th.Level(0.01, "x").rate == 0.0


# --- acknowledgement counting ----------------------------------------------


def _reply(code=0):
    return ([b for b in m.Reply(code=code, exclusive_channel=0).encode()], 0.0)


def test_drain_counts_every_acknowledgement():
    """N writes must produce N replies; the count is the drop measurement."""
    bridge = FakeBridge(replies=[_reply() for _ in range(5)])
    seen, errors, _elapsed = _probe(bridge)._drain_replies(expect=5, quiet=0.05)

    assert (seen, errors) == (5, 0)


def test_drain_reports_a_shortfall_rather_than_waiting_for_ever():
    bridge = FakeBridge(replies=[_reply() for _ in range(2)])
    seen, _errors, _elapsed = _probe(bridge)._drain_replies(expect=5, quiet=0.05)

    assert seen == 2, "a dropped write shows up as a missing acknowledgement"


def test_drain_counts_error_replies_separately():
    bridge = FakeBridge(replies=[_reply(0), _reply(1), _reply(0)])
    seen, errors, _elapsed = _probe(bridge)._drain_replies(expect=3, quiet=0.05)

    assert (seen, errors) == (3, 1)


def test_drain_ignores_frames_that_are_not_replies():
    status = ([b for b in m.RequestStatus(exclusive_channel=0).encode()], 0.0)
    bridge = FakeBridge(replies=[status, _reply(), status])
    seen, _errors, _elapsed = _probe(bridge)._drain_replies(expect=1, quiet=0.05)

    assert seen == 1


def test_dry_run_completes():
    assert th.main(["--dry-run", "--count", "2"]) == 0
