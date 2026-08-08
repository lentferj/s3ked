# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Transport and high-level operations, against fakes.

Two layers of fake, matching the two things worth testing separately:

* a fake ``rtmidi`` module, monkeypatched onto :mod:`s3k.bridge`, for
  enumeration, autodetect and throttling -- everything that is about *ports*;
* a scripted :class:`FakeDevice` passed as **both** ``midi_out`` and
  ``midi_in``, for the operations -- so the real codec runs on both sides of
  every exchange rather than being asserted against hand-written bytes.
"""

import time

import pytest

from s3k import bridge as bridge_mod
from s3k import messages as m
from s3k import params as p
from s3k.bridge import (
    AmbiguousDevice,
    DeviceError,
    MidiUnavailable,
    MultiIn,
    S3kBridge,
    ThrottledOut,
)


# --- scripted device --------------------------------------------------------


class FakeDevice:
    """A minimal scripted sampler.

    Pass the same instance as both ``midi_out`` and ``midi_in``:
    ``send_message`` decodes what was sent and enqueues whatever ``handler``
    returns for the next ``get_message``.
    """

    def __init__(self, handler):
        self.handler = handler
        self.sent = []
        self.writes = []
        self.inbox = []

    def send_message(self, message, *, write=False):
        frame = bytes(message)
        self.sent.append(frame)
        self.writes.append(write)
        reply = self.handler(frame)
        if reply is None:
            return
        self.inbox.extend(reply if isinstance(reply, list) else [reply])

    def get_message(self):
        return (list(self.inbox.pop(0)), 0.0) if self.inbox else None

    def close_port(self):
        pass

    def delete(self):
        pass


def _sampler(headers=None, *, channel=0, fail=()):
    """A handler implementing enough of the protocol to drive the bridge."""
    store = headers if headers is not None else {}

    def handler(frame):
        ch, command, _payload = m.parse_frame(frame)
        if ch != channel:
            return None  # the device ignores other exclusive channels
        if command in fail:
            return m.Reply(code=m.ReplyCode.ERROR, exclusive_channel=ch).encode()
        if command == m.Command.RSTAT:
            return m.Status(
                version_major=2,
                version_minor=0,
                max_blocks=1022,
                free_blocks=900,
                max_words=8388608,
                free_words=4194304,
                exclusive_channel_setting=ch,
                exclusive_channel=ch,
            ).encode()
        if command == m.Command.RPLIST:
            return m.ProgramList(
                names=["BASS ROUND", "PAD WIDE"], exclusive_channel=ch
            ).encode()
        if command == m.Command.RSLIST:
            return m.SampleList(names=["KICK 1"], exclusive_channel=ch).encode()
        if command in (
            m.Command.RPHEADER,
            m.Command.RKHEADER,
            m.Command.RSHEADER,
        ):
            request = m.HeaderRequest.decode(frame)
            region = {
                m.Command.RPHEADER: "program",
                m.Command.RKHEADER: "keygroup",
                m.Command.RSHEADER: "sample",
            }[command]
            raw = store.setdefault(
                (region, request.index, request.selector), bytearray(p.HEADER_SIZE)
            )
            return m.HeaderData(
                command=command + 1,
                index=request.index,
                selector=request.selector,
                offset=request.offset,
                data=bytes(raw[request.offset : request.offset + request.count]),
                exclusive_channel=ch,
            ).encode()
        if command in (m.Command.PHEADER, m.Command.KHEADER, m.Command.SHEADER):
            data = m.HeaderData.decode(frame)
            region = {
                m.Command.PHEADER: "program",
                m.Command.KHEADER: "keygroup",
                m.Command.SHEADER: "sample",
            }[command]
            raw = store.setdefault(
                (region, data.index, data.selector), bytearray(p.HEADER_SIZE)
            )
            raw[data.offset : data.offset + len(data.data)] = data.data
            return m.Reply(code=m.ReplyCode.OK, exclusive_channel=ch).encode()
        if command in (m.Command.DELP, m.Command.DELK, m.Command.DELS):
            return m.Reply(code=m.ReplyCode.OK, exclusive_channel=ch).encode()
        return None

    return handler


def _bridge_with(handler, **kwargs):
    device = FakeDevice(handler)
    return S3kBridge(device, device, "fake", timeout=kwargs.pop("timeout", 0.5), **kwargs)


# --- high-level operations --------------------------------------------------


def test_status():
    bridge = _bridge_with(_sampler())
    status = bridge.status()
    assert status.version == "2.00"
    assert status.free_blocks == 900
    assert status.used_blocks == 122
    assert status.used_words == 4194304


def test_is_connected_true_and_false():
    assert _bridge_with(_sampler()).is_connected()
    assert not _bridge_with(lambda frame: None, timeout=0.05).is_connected(timeout=0.05)


def test_program_and_sample_lists():
    bridge = _bridge_with(_sampler())
    assert bridge.program_list() == ["BASS ROUND", "PAD WIDE"]
    assert bridge.sample_list() == ["KICK 1"]


def test_get_and_set_parameter_round_trip():
    bridge = _bridge_with(_sampler())
    bridge.set_parameter("PRIORT", 0, 3)
    assert bridge.get_parameter("PRIORT", 0) == 3


def test_set_parameter_is_flagged_as_a_write():
    """The throttle needs to know; a lost write raises nothing."""
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.set_parameter("PRIORT", 0, 1)
    assert device.writes[0] is True


def test_reads_are_not_flagged_as_writes():
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.status()
    assert device.writes == [False]


def test_text_parameter_round_trip():
    bridge = _bridge_with(_sampler())
    bridge.set_parameter(("program", "PRNAME"), 0, "PAD WIDE")
    assert bridge.get_parameter(("program", "PRNAME"), 0) == "PAD WIDE"


def test_keygroup_parameter_uses_the_selector():
    store = {}
    bridge = _bridge_with(_sampler(store))
    bridge.set_parameter(("keygroup", "LONOTE"), 0, 36, keygroup=2)
    assert bridge.get_parameter(("keygroup", "LONOTE"), 0, keygroup=2) == 36
    # A different keygroup must be untouched -- the selector is what keeps
    # them apart, and dropping it would write every keygroup at once.
    assert bridge.get_parameter(("keygroup", "LONOTE"), 0, keygroup=1) == 0
    assert ("keygroup", 0, 2) in store


def test_get_header_decodes_every_field_in_one_request():
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    header = bridge.get_header("program", 0)
    assert len(header) == len(p.region_params("program"))
    assert len(device.sent) == 1, "a whole header must cost one round trip"


def test_set_parameter_refuses_read_only():
    bridge = _bridge_with(_sampler())
    with pytest.raises(ValueError, match="read-only"):
        bridge.set_parameter(("program", "GROUPS"), 0, 4)


def test_set_parameter_refuses_internal_address():
    bridge = _bridge_with(_sampler())
    with pytest.raises(ValueError, match="internal block address"):
        bridge.set_parameter(("program", "KGRP1@"), 0, 1)


def test_write_surfaces_a_device_error():
    bridge = _bridge_with(_sampler(fail=(m.Command.PHEADER,)))
    with pytest.raises(DeviceError, match="reported an error"):
        bridge.set_parameter("PRIORT", 0, 1)


def test_unconfirmed_write_does_not_wait():
    """confirm=False is fire-and-forget, and must not consume a reply."""
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.05)
    bridge.set_header_bytes("program", 0, 18, b"\x02", confirm=False)
    assert len(device.sent) == 1


def test_deletes_confirm_by_default():
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.delete_program(1)
    bridge.delete_keygroup(0, 1)
    bridge.delete_sample(3)
    assert all(device.writes)


def test_delete_surfaces_a_device_error():
    bridge = _bridge_with(_sampler(fail=(m.Command.DELP,)))
    with pytest.raises(DeviceError):
        bridge.delete_program(0)


def test_unknown_region_is_rejected():
    bridge = _bridge_with(_sampler())
    with pytest.raises(KeyError):
        bridge.get_header_bytes("nope", 0, 0, 1)


def test_set_exclusive_channel_moves_our_address_too():
    """Otherwise the very next message goes somewhere nobody is listening."""
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.set_exclusive_channel(4)
    assert bridge.exclusive_channel == 4
    assert m.parse_frame(device.sent[-1])[0] == 0


def test_replies_on_another_channel_are_ignored():
    """A second machine on the same wire must not answer our questions."""
    bridge = _bridge_with(_sampler(channel=9), timeout=0.05)
    with pytest.raises(TimeoutError):
        bridge.status()


def test_foreign_sysex_is_skipped_not_fatal():
    calls = {"n": 0}
    inner = _sampler()

    def handler(frame):
        calls["n"] += 1
        # An unrelated manufacturer's message arrives first on a shared input.
        return [bytes([0xF0, 0x18, 0x21, 0x00, 0x55, 0x01, 0xF7]), inner(frame)]

    assert _bridge_with(handler).status().version == "2.00"


def test_timeout_when_nothing_answers():
    with pytest.raises(TimeoutError):
        _bridge_with(lambda frame: None, timeout=0.05).status()


def test_close_is_safe_and_idempotent():
    bridge = _bridge_with(_sampler())
    bridge.close()
    bridge.close()


# --- ThrottledOut -----------------------------------------------------------


class _RecordingPort:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append((time.time(), list(message)))

    def close_port(self):
        pass


def test_throttle_gaps_sysex():
    port = _RecordingPort()
    out = ThrottledOut(port, gap=0.05)
    out.send_message([0xF0, 0x47, 0x00, 0x00, 0x48, 0xF7])
    out.send_message([0xF0, 0x47, 0x00, 0x00, 0x48, 0xF7])
    assert port.messages[1][0] - port.messages[0][0] >= 0.045


def test_throttle_passes_non_sysex_straight_through():
    port = _RecordingPort()
    out = ThrottledOut(port, gap=0.2)
    out.send_message([0x90, 60, 100])
    out.send_message([0x90, 60, 0])
    assert port.messages[1][0] - port.messages[0][0] < 0.1


def test_write_gap_is_separate_from_read_gap():
    port = _RecordingPort()
    out = ThrottledOut(port, gap=0.0, write_gap=0.06)
    out.send_message([0xF0, 0xF7], write=True)
    out.send_message([0xF0, 0xF7])
    assert port.messages[1][0] - port.messages[0][0] >= 0.055


def test_write_gap_defaults_to_gap():
    out = ThrottledOut(_RecordingPort(), gap=0.03)
    assert out._write_gap == 0.03


def test_throttle_delegates_unknown_attributes():
    port = _RecordingPort()
    assert ThrottledOut(port).close_port() is None


# --- fake rtmidi: enumeration, autodetect -----------------------------------


class _FakePort:
    """One rtmidi port. Subclasses drive the scenarios."""

    PORTS = []
    deleted = 0

    def __init__(self, *args, **kwargs):
        self.opened = None
        self.sent = []
        self.inbox = []

    def get_ports(self):
        return list(self.PORTS)

    def open_port(self, index):
        self.opened = index

    def ignore_types(self, sysex=True, **kwargs):
        pass

    def send_message(self, message):
        self.sent.append(bytes(message))

    def get_message(self):
        return (list(self.inbox.pop(0)), 0.0) if self.inbox else None

    def close_port(self):
        pass

    def delete(self):
        type(self).deleted += 1


def _install(monkeypatch, cls, ports):
    cls.PORTS = ports
    cls.deleted = 0

    class FakeRtmidi:
        MidiOut = cls
        MidiIn = cls

    monkeypatch.setattr(bridge_mod, "rtmidi", FakeRtmidi)
    return cls


def test_list_ports(monkeypatch):
    _install(monkeypatch, type("P", (_FakePort,), {}), ["A", "B"])
    assert bridge_mod.list_ports() == (["A", "B"], ["A", "B"])


def test_enumeration_deletes_every_transient_port(monkeypatch):
    """close_port() does not free the ALSA client; only delete() does.

    Without this, one autodetect sweep on a busy host can exhaust the
    sequencer's client slots.
    """
    cls = _install(monkeypatch, type("P", (_FakePort,), {}), ["A"])
    bridge_mod.list_ports()
    assert cls.deleted == 2  # one input probe, one output probe


def test_bidirectional_ports_intersects_inputs_and_outputs(monkeypatch):
    # The fake serves one port list to both MidiIn and MidiOut, so every port
    # here is bidirectional; what this pins is that the result is the
    # intersection in *output* order, not one list or the other verbatim.
    class OutOnly(_FakePort):
        def get_ports(self):
            return ["Shared", "OutOnly"]

    class InOnly(_FakePort):
        def get_ports(self):
            return ["Shared", "InOnly"]

    class FakeRtmidi:
        MidiOut = OutOnly
        MidiIn = InOnly

    monkeypatch.setattr(bridge_mod, "rtmidi", FakeRtmidi)
    assert bridge_mod.bidirectional_ports() == ["Shared"]


def test_missing_backend_is_distinct_from_no_ports(monkeypatch):
    class Exploding:
        def __init__(self, *args, **kwargs):
            raise SystemError("no ALSA sequencer")

    class FakeRtmidi:
        MidiOut = Exploding
        MidiIn = Exploding

    monkeypatch.setattr(bridge_mod, "rtmidi", FakeRtmidi)
    with pytest.raises(MidiUnavailable):
        bridge_mod.list_ports()


def test_open_out_rejects_unknown_port(monkeypatch):
    _install(monkeypatch, type("P", (_FakePort,), {}), ["A"])
    with pytest.raises(RuntimeError, match="no output port"):
        bridge_mod._open_out("Z")


class _AnsweringPort(_FakePort):
    """Answers STAT on exclusive channel 0 to anything sent to it."""

    CHANNEL = 0
    ECHO_ONLY = False

    def send_message(self, message):
        super().send_message(message)
        frame = bytes(message)
        try:
            ch, command, _ = m.parse_frame(frame)
        except ValueError:
            return
        if command != m.Command.RSTAT or ch != self.CHANNEL:
            return
        reply = (
            frame
            if self.ECHO_ONLY
            else m.Status(
                version_major=2,
                version_minor=0,
                max_blocks=1,
                free_blocks=1,
                max_words=1,
                free_words=1,
                exclusive_channel_setting=ch,
                exclusive_channel=ch,
            ).encode()
        )
        type(self).PENDING.append(reply)

    def get_message(self):
        if type(self).PENDING:
            return (list(type(self).PENDING.pop(0)), 0.0)
        return None


def test_autodetect_finds_a_device(monkeypatch):
    cls = type("P", (_AnsweringPort,), {"PENDING": []})
    _install(monkeypatch, cls, ["Sampler"])
    bridge = S3kBridge.autodetect(timeout=0.1, config_path=None)
    assert bridge.exclusive_channel == 0
    assert "Sampler" in bridge.description


def test_autodetect_rejects_a_thru_loop_echoing_our_probe(monkeypatch):
    """The discriminator: we send RSTAT and require STAT back.

    A MIDI-Thru loop returns our own bytes, which carry the request opcode --
    accepting those would "find" a device that is really a cable.
    """
    cls = type("P", (_AnsweringPort,), {"PENDING": [], "ECHO_ONLY": True})
    _install(monkeypatch, cls, ["Loopback"])
    with pytest.raises(RuntimeError, match="no sampler answered"):
        S3kBridge.autodetect(timeout=0.1, config_path=None)


def test_autodetect_error_mentions_the_missing_broadcast(monkeypatch):
    """There is no broadcast address, so "nothing answered" is ambiguous."""
    _install(monkeypatch, type("P", (_FakePort,), {}), ["Silent"])
    with pytest.raises(RuntimeError, match="no broadcast address"):
        S3kBridge.autodetect(timeout=0.05, config_path=None)


def test_autodetect_refuses_to_choose_between_two_machines(monkeypatch):
    class P(_AnsweringPort):
        PENDING = []

        def send_message(self, message):
            _FakePort.send_message(self, message)
            try:
                ch, command, _ = m.parse_frame(bytes(message))
            except ValueError:
                return
            if command != m.Command.RSTAT:
                return
            for channel in (0, 5):
                type(self).PENDING.append(
                    m.Status(
                        version_major=2,
                        version_minor=0,
                        max_blocks=1,
                        free_blocks=1,
                        max_words=1,
                        free_words=1,
                        exclusive_channel_setting=channel,
                        exclusive_channel=channel,
                    ).encode()
                )

    _install(monkeypatch, P, ["Sampler"])
    with pytest.raises(AmbiguousDevice) as excinfo:
        S3kBridge.autodetect(timeout=0.1, config_path=None)
    assert "--exclusive-channel" in str(excinfo.value)


def test_autodetect_needs_ports(monkeypatch):
    _install(monkeypatch, type("P", (_FakePort,), {}), [])
    with pytest.raises(RuntimeError, match="at least one MIDI"):
        S3kBridge.autodetect(timeout=0.05, config_path=None)


# --- config.toml ------------------------------------------------------------


def test_port_cache_round_trip(tmp_path):
    path = str(tmp_path / "config.toml")
    assert bridge_mod.load_last_ports(path) is None
    bridge_mod.save_last_ports("Out A", "In B", path)
    assert bridge_mod.load_last_ports(path) == ("Out A", "In B")


def test_config_writes_preserve_unrelated_keys(tmp_path):
    """Two independent settings share this file; neither may clobber the other."""
    path = str(tmp_path / "config.toml")
    bridge_mod.save_exclusive_channel(7, path)
    bridge_mod.save_last_ports("Out", "In", path)
    assert bridge_mod.load_exclusive_channel(path) == 7
    assert bridge_mod.load_last_ports(path) == ("Out", "In")


def test_unreadable_config_is_not_fatal(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not valid toml {{{")
    assert bridge_mod.load_last_ports(str(path)) is None


def test_multi_in_requires_a_match(monkeypatch):
    _install(monkeypatch, type("P", (_FakePort,), {}), ["Something"])
    with pytest.raises(RuntimeError, match="no input port matching"):
        MultiIn("Nothing")
