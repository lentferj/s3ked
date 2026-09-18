# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
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
"""`probes/jcap.py` -- the capture client, against a fake JACK.

These exist because a leaked client wedged the shared server on 2026-09-18 and
nobody could tell from outside: to jackd a client whose owner has died and one
that is merely busy look identical, so the leak is invisible until the whole
rig stops answering.

Synthetic throughout -- a fake `jack` module is installed before import, so no
server is needed and the failure paths can be provoked on demand.
"""
import importlib, sys, types, warnings
import numpy as np
import pytest


class _FakePort:
    def __init__(self, n): self.name = n
    def get_array(self): return np.ones(4, dtype=np.float32)


class _FakeInports:
    def __init__(self, owner): self._owner = owner
    def register(self, name):
        if self._owner.fail_register:
            raise RuntimeError("register refused")
        p = _FakePort(name); self._owner.registered.append(p); return p


class _FakeClient:
    def __init__(self, name, no_start_server=False):
        self.name = name
        self.samplerate = 48000
        self.blocksize = 512
        self.inports = _FakeInports(self)
        self.registered = []
        self.activated = False
        self.closed = False
        self.deactivated = False
        self.fail_register = False
        self.fail_connect = False
        self.process_cb = None
        self.xrun_cb = None
        _FakeClient.last = self
    def set_process_callback(self, fn): self.process_cb = fn; return fn
    def set_xrun_callback(self, fn): self.xrun_cb = fn; return fn
    def activate(self): self.activated = True
    def connect(self, a, b):
        if self.fail_connect:
            raise RuntimeError("no such port")
    def deactivate(self): self.deactivated = True; self.activated = False
    def close(self): self.closed = True


def _load(monkeypatch, **flags):
    fake = types.ModuleType("jack")

    def _ctor(name, no_start_server=False):
        c = _FakeClient(name, no_start_server)
        for k, v in flags.items():
            setattr(c, k, v)
        return c

    fake.Client = _ctor
    monkeypatch.setitem(sys.modules, "jack", fake)
    sys.path.insert(0, "probes")
    try:
        mod = importlib.import_module("jcap")
        importlib.reload(mod)
    finally:
        sys.path.pop(0)
    return mod


def test_a_failed_connect_does_not_leave_the_client_activated(monkeypatch):
    """THE BUG THIS FILE EXISTS FOR.

    `activate()` ran before `connect()` and nothing caught a failure between
    them, so a raising connect left a registered, activated client with the
    half-built object discarded -- unreachable, so `close()` could never be
    called on it. jackd then holds a socket for a client nobody owns.
    """
    jcap = _load(monkeypatch, fail_connect=True)
    with pytest.raises(RuntimeError):
        jcap.Capture()
    c = _FakeClient.last
    assert c.deactivated, "client was left ACTIVATED after a failed connect"
    assert c.closed, "client was left OPEN after a failed connect"


def test_a_failed_port_register_also_tears_down(monkeypatch):
    jcap = _load(monkeypatch, fail_register=True)
    with pytest.raises(RuntimeError):
        jcap.Capture()
    assert _FakeClient.last.closed


def test_a_keyboardinterrupt_during_construction_tears_down(monkeypatch):
    """A killed probe is how the leak was found, and KeyboardInterrupt is not
    an Exception -- an `except Exception` here would have caught nothing."""
    jcap = _load(monkeypatch)
    orig = _FakeClient.connect
    def boom(self, a, b): raise KeyboardInterrupt
    monkeypatch.setattr(_FakeClient, "connect", boom)
    with pytest.raises(KeyboardInterrupt):
        jcap.Capture()
    monkeypatch.setattr(_FakeClient, "connect", orig)
    assert _FakeClient.last.closed


def test_close_is_idempotent_and_safe_twice(monkeypatch):
    jcap = _load(monkeypatch)
    cap = jcap.Capture()
    cap.close(); cap.close()
    assert _FakeClient.last.closed


def test_the_context_manager_closes_on_the_way_out(monkeypatch):
    jcap = _load(monkeypatch)
    with jcap.Capture():
        assert not _FakeClient.last.closed
    assert _FakeClient.last.closed


def test_an_xrun_is_counted_and_warned_about(monkeypatch):
    """A dropout silently shortens a capture, and every timing measured through
    this class assumes the frames are contiguous."""
    jcap = _load(monkeypatch)
    with jcap.Capture() as cap:
        cap.start()
        _FakeClient.last.process_cb(4)
        _FakeClient.last.xrun_cb(0.0)
        assert cap.xruns == 1
        with pytest.warns(UserWarning, match="NOT contiguous"):
            cap.stop_channels()


def test_a_clean_capture_warns_about_nothing(monkeypatch):
    """The negative control: the warning must not fire on a good run, or it
    carries no information."""
    jcap = _load(monkeypatch)
    with jcap.Capture() as cap:
        cap.start()
        _FakeClient.last.process_cb(4)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = cap.stop_channels()
    assert len(out) == 2 and len(out[0]) == 4


def test_the_queue_is_bounded_and_overflow_is_counted_not_silent(monkeypatch):
    """Unbounded, a capture nobody drains grew until the box ran out."""
    jcap = _load(monkeypatch)
    with jcap.Capture(max_seconds=0.02) as cap:   # ~1 block at 48k/512
        cap.start()
        for _ in range(50):
            _FakeClient.last.process_cb(4)
        assert cap.overflows > 0, "queue accepted more than its stated bound"
        with pytest.warns(UserWarning, match="NOT contiguous"):
            cap.stop_channels()


def test_start_resets_the_counters(monkeypatch):
    jcap = _load(monkeypatch)
    with jcap.Capture() as cap:
        cap.xruns = 7; cap.overflows = 3
        cap.start()
        assert cap.xruns == 0 and cap.overflows == 0
