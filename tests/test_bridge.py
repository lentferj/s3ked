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
    WRITE_GAP,
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
            # A real machine stamps a block identifier at offset 0 (0x01
            # program, 0x02 keygroup, 0x03 sample). The fake must too, or it
            # is not exercising the check that catches a stale-buffer read.
            blank = bytearray(p.HEADER_SIZE)
            blank[0] = bridge_mod.BLOCK_IDENT[region]
            if region == "program":
                # GROUPS' range is 1..99, so a program with zero keygroups is
                # not a state the device can be in. A blank header left it at
                # 0, which the bounds guard then correctly refused to index
                # into -- a fixture claiming something impossible.
                blank[p.lookup(("program", "GROUPS")).offset] = 4
            raw = store.setdefault(
                (region, request.index, request.selector), blank
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
    """The throttle needs to know; a lost write raises nothing.

    The bounds guard's own program-list read comes first on a cold bridge
    and is correctly flagged as a read, so this warms the cache to leave the
    write as the only frame in flight.
    """
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.program_list()
    device.writes.clear()
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
    bridge.program_list()                      # warm the bounds cache
    device.sent.clear()
    header = bridge.get_header("program", 0)
    assert len(header) == len(p.region_params("program"))
    assert len(device.sent) == 1, "a whole header must cost one round trip"


def test_the_bounds_guard_costs_one_read_per_region_and_then_nothing():
    """Pinned deliberately, because the guard changed a documented cost.

    Refusing an out-of-range read needs to know how many programs exist, and
    that is a round trip. It is taken once per bridge and cached; a walk of
    hundreds of headers pays it once.
    """
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)

    bridge.get_header_bytes("program", 0, 4, 2)
    assert len(device.sent) == 2, "one list read, then the header read"

    device.sent.clear()
    for _ in range(5):
        bridge.get_header_bytes("program", 0, 4, 2)
    assert len(device.sent) == 5, "cached: no further list reads"


def test_a_structural_change_drops_the_cached_counts():
    """A cache that outlived a delete would refuse valid reads."""
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.5)
    bridge.get_header_bytes("program", 0, 4, 2)
    assert bridge._counts["program"] == 2

    bridge.delete_program(0)
    assert bridge._counts["program"] is None, "delete must invalidate"


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
    """confirm=False is fire-and-forget, and must not consume a reply.

    Counted after the bounds cache is warm: the guard costs one program-list
    read the first time it sees a bridge, and nothing thereafter. A burst of
    fire-and-forget writes is the case that matters and it pays nothing.
    """
    device = FakeDevice(_sampler())
    bridge = S3kBridge(device, device, "fake", timeout=0.05)
    bridge.program_list()                      # warm the count
    device.sent.clear()
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


def test_write_gap_does_not_inherit_a_small_read_gap():
    """The two gaps were measured apart and must not be conflated.

    A request is self-pacing -- it blocks for its reply -- so its gap can be
    small. An unacknowledged write has nothing pacing it, and going faster
    than the device consumes drops writes silently (RESOLUTION_NOTES §6).
    Inheriting `gap` here would hand a fire-and-forget caller an unsafe value
    every time someone tuned reads down.
    """
    out = ThrottledOut(_RecordingPort(), gap=0.001)
    assert out._write_gap == WRITE_GAP
    assert out._write_gap > out._gap


def test_write_gap_is_still_explicitly_settable():
    out = ThrottledOut(_RecordingPort(), gap=0.03, write_gap=0.2)
    assert (out._gap, out._write_gap) == (0.03, 0.2)


def test_the_measured_write_floor_is_not_undercut_by_the_default():
    """75 ms was the fastest gap that lost nothing in a 150-write burst."""
    assert WRITE_GAP >= 0.075


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


# --- the disk's volume list -------------------------------------------------

def _volume_record(name: str, kind: int = 3) -> bytes:
    """One 16-byte VOLLIST record: 12 charset bytes, then the type and pad."""
    from s3k import messages as m
    return bytes(m.encode_name(name, 12)) + bytes([kind, 0, 0, 0])


def _requested_index(frame: bytes) -> int:
    """The item index out of an extended request: 14 bits, low 7 first."""
    return frame[5] | (frame[6] << 7)


def _disk(names, per_read=16):
    """A stand-in device that pages a volume list the way the machine does."""
    from s3k import bridge as b, messages as m

    records = [_volume_record(n) for n in names]

    class Fake(b.S3kBridge):
        def __init__(self):
            self.exclusive_channel = 0
            self.reads = 0

        def send_and_receive(self, frame, timeout=None):
            self.reads += 1
            start = _requested_index(frame)
            page = records[start:start + per_read]
            page += [bytes(16)] * (per_read - len(page))   # zeros past the end
            return m.HeaderData(
                command=m.Command.VOLLIST, index=start, selector=0,
                offset=0, data=b"".join(page), exclusive_channel=0,
            ).encode()

    return Fake()


def test_volume_list_stops_on_the_type_byte_not_a_blank_name():
    """Past the last volume every byte is zero -- and a zero NAME is not blank.

    Index 0 of the Akai charset is the character "0", so an empty record
    decodes to "000000000000". Stopping on a falsy name would never stop, and
    stopping on that literal string would truncate a disk that has a volume
    genuinely called that. The type byte is the marker, and this fixture
    includes such a volume so the wrong rule fails here.
    """
    bridge = _disk(["BOOT", "WORK 01", "000000000000"])
    volumes = bridge.volume_list()

    assert [v.name for v in volumes] == ["BOOT", "WORK 01", "000000000000"]
    assert [v.index for v in volumes] == [0, 1, 2]
    assert all(v.kind == 3 for v in volumes)


def test_volume_list_pages_rather_than_asking_one_at_a_time():
    """100 volumes took 7 round trips on hardware, not 100."""
    bridge = _disk([f"VOL {i:02d}" for i in range(100)])
    volumes = bridge.volume_list()

    assert len(volumes) == 100
    assert bridge.reads <= 8, f"{bridge.reads} round trips for 100 volumes"


def test_an_empty_disk_reads_as_no_volumes():
    bridge = _disk([])
    assert bridge.volume_list() == []


def test_volume_indices_are_the_numbers_to_address_a_volume_by():
    """The index is the position in the list, as for programs and samples."""
    bridge = _disk(["A", "B", "C", "D", "E"], per_read=2)
    volumes = bridge.volume_list()
    assert [v.index for v in volumes] == [0, 1, 2, 3, 4]
    assert [v.name for v in volumes] == ["A", "B", "C", "D", "E"]


# --- the loaded volume's directory ------------------------------------------

def _dir_record(name: str, tail: bytes = b"\x73\x96\x60\x10\xb3\x03\x1e\x09") -> bytes:
    """24 bytes: name, a blank four-byte extension, then eight undocumented."""
    from s3k import messages as m
    return bytes(m.encode_name(name, 12)) + b"\x20\x20\x20\x20" + tail


def _directory(records):
    """A device that keeps answering past the end, the way the machine does."""
    from s3k import bridge as b, messages as m

    class Fake(b.S3kBridge):
        def __init__(self):
            self.exclusive_channel = 0

        def send_and_receive(self, frame, timeout=None):
            entry = frame[5] | (frame[6] << 7)
            data = records[entry] if entry < len(records) else records[-1]
            return m.HeaderData(
                command=m.Command.HDDIR, index=entry, selector=1,
                offset=0, data=data, exclusive_channel=0,
            ).encode()

    return Fake()


def test_the_directory_stops_at_a_non_blank_extension():
    """Past the end the extension field stops being four spaces.

    A stop condition of "all bytes zero" never fires -- the device answers
    forever -- and the first version of this returned 188 entries for a
    64-entry directory, two thirds of them junk that decoded as plausible
    names.
    """
    junk = bytes(12) + b"\xff\xff\x00\x00" + bytes(8)
    bridge = _directory([_dir_record("PROG A"), _dir_record("PROG B"), junk])

    entries = bridge.hd_directory(1)
    assert [e.name for e in entries] == ["PROG A", "PROG B"]


def test_the_directory_stops_when_a_record_repeats():
    """The echo is not always of entry 0.

    On the disk here entry 63 came back byte-identical to entry 13, including
    the bytes that look like a location, so two real files cannot explain it.
    """
    a, bb, c = _dir_record("ONE"), _dir_record("TWO"), _dir_record("THREE")
    bridge = _directory([a, bb, c, bb])          # the echo repeats entry 1

    entries = bridge.hd_directory(1)
    assert [e.name for e in entries] == ["ONE", "TWO", "THREE"]


def test_an_unloaded_volume_reads_as_an_empty_directory():
    """Nothing loaded is not an error -- the manual says a volume must be
    loaded at the panel first, and the device answers with empty records."""
    bridge = _directory([bytes(24)])
    assert bridge.hd_directory(1) == []


def test_a_directory_entry_decodes_its_type_and_size():
    """The fields VinSamLib needs to know whether a volume fits in RAM."""
    from s3k import bridge as b, messages as m

    def record(name, kind, size):
        return (bytes(m.encode_name(name, 12)) + b"\x20\x20\x20\x20"
                + bytes([kind]) + int(size).to_bytes(3, "little")
                + b"\x00\x00\x1e\x09")

    entries = _directory([
        record("A PROGRAM", b.ITEM_PROGRAM, 900),
        record("A SAMPLE", b.ITEM_SAMPLE, 250_010),
        bytes(24),
    ]).hd_directory(1)

    prog, samp = entries
    assert prog.is_program and not prog.is_sample
    assert samp.is_sample and not samp.is_program
    assert samp.size_bytes == 250_010

    # a sample file is its audio at two bytes a word, plus a 150-byte header
    assert samp.audio_words == (250_010 - b.SAMPLE_FILE_OVERHEAD) // 2
    assert prog.audio_words == 0, "a program holds no audio"


def test_the_overhead_is_the_measured_one_not_a_guess():
    """150 bytes, from 60 samples compared against their loaded SLNGTH.

    Pinned because the number is the difference between a volume that is
    reported as fitting and one that is not, and it was measured rather than
    derived from any document.
    """
    from s3k import bridge as b
    assert b.SAMPLE_FILE_OVERHEAD == 150


def test_summing_a_volume_answers_whether_it_fits():
    """The whole point: sum audio_words and compare against the machine."""
    from s3k import bridge as b, messages as m

    def sample(name, words):
        size = words * 2 + b.SAMPLE_FILE_OVERHEAD
        return (bytes(m.encode_name(name, 12)) + b"\x20\x20\x20\x20"
                + bytes([b.ITEM_SAMPLE]) + size.to_bytes(3, "little")
                + b"\x00\x00\x1e\x09")

    # 3 bytes of size caps one FILE at ~16.7 MB; a volume exceeds RAM by
    # having many, which is exactly how the 58.7 MB volume did it.
    entries = _directory([sample(f"S{i:02d}", 800_000) for i in range(12)]
                         + [bytes(24)]).hd_directory(1)

    needed = sum(e.audio_words for e in entries)
    assert needed == 9_600_000
    assert needed <= 16_777_216, "this one fits"

    entries = _directory([sample(f"S{i:02d}", 800_000) for i in range(24)]
                         + [bytes(24)]).hd_directory(1)
    assert sum(e.audio_words for e in entries) > 16_777_216, "this one does not"


def test_the_size_field_is_three_bytes_so_a_file_caps_near_16_MB():
    """Confirmed against ground truth rather than assumed.

    Reading bytes 17..20 as the size reproduced SLNGTH*2+150 exactly for all
    60 samples that could be compared, so the field is three bytes and not
    four. That puts a ceiling of 16,777,215 bytes on any single file -- about
    8.4 million sample words, or 3.2 minutes of mono audio at 44.1 kHz.
    """
    from s3k import bridge as b
    assert (1 << 24) - 1 == 16_777_215
    biggest_words = ((1 << 24) - 1 - b.SAMPLE_FILE_OVERHEAD) // 2
    assert 8_388_000 < biggest_words < 8_389_000


# --- the load source --------------------------------------------------------

def _misc_machine(initial):
    """A device whose miscellaneous byte bank can be read and written."""
    from s3k import bridge as b, messages as m

    class Fake(b.S3kBridge):
        def __init__(self):
            self.exclusive_channel = 0
            self.bytes = dict(initial)
            self.writes = []

        def send_and_receive(self, frame, timeout=None):
            index = frame[5] | (frame[6] << 7)
            return m.HeaderData(
                command=m.Command.MISCDATA, index=index, selector=1, offset=0,
                data=bytes([self.bytes.get(index, 0)]), exclusive_channel=0,
            ).encode()

        def _drain(self):
            pass

        def _send(self, frame, write=False):
            index = frame[5] | (frame[6] << 7)
            self.bytes[index] = m.HeaderData.decode(frame).data[0]
            self.writes.append(index)

        def _receive(self, timeout=None):
            return m.Reply(code=m.ReplyCode.OK, exclusive_channel=0).encode()

    return Fake()


def test_load_source_reads_the_panel_fields():
    bridge = _misc_machine({0: 1, 2: 2, 11: 4, 12: 6, 49: 1, 91: 10})
    assert bridge.load_source() == {
        "scsi_drive_id": 4, "scsi_local_id": 6, "device_type": 1,
        "partition": 2, "cursor_value": 1, "mode": 10,
    }


def test_selecting_a_partition_clears_the_hold_flag_first():
    """byte[4] set means the machine accepts the write and does NOT re-read.

    The panel sets it when the selection lands on a volume that does not
    exist. While it is set, a partition change silently leaves the directory
    describing the previous partition -- which looks exactly like the write
    having failed, and cost a confusing half hour on hardware.
    """
    bridge = _misc_machine({2: 2, 4: 1})
    bridge.select_partition(5)

    assert bridge.bytes[4] == 0, "the hold flag must be cleared"
    assert bridge.bytes[2] == 5
    assert bridge.writes.index(4) < bridge.writes.index(2), "cleared FIRST"


def test_there_is_no_volume_register_to_offer():
    """Pinned so nobody adds a volume= argument that lies.

    byte[49] looked like the volume because it read 1, 2, 3 as the panel
    stepped through them -- but it is the value of whatever field the cursor
    is on, and reads 0 on a page that has no such field. Writing it does
    nothing, on single- and multi-volume discs alike.
    """
    from s3k import bridge as b
    import inspect

    assert not hasattr(b.S3kBridge, "select_volume")
    params = inspect.signature(b.S3kBridge.select_partition).parameters
    assert "volume" not in params
    assert "cannot be moved remotely" in b.S3kBridge.select_partition.__doc__


def test_select_mode_reports_what_the_register_reads_not_the_ack():
    """The device's acknowledgement is wrong in both directions.

    Writing 0 answers REPLY error and switches the page anyway; byte[4]
    accepts a write and ignores it. So select_mode swallows the error and
    returns the read-back, which is the only thing that tells the truth.
    """
    from s3k import bridge as b

    class Contrary(_misc_machine({91: 10}).__class__):
        def _receive(self, timeout=None):
            from s3k import messages as m
            return m.Reply(code=m.ReplyCode.ERROR, exclusive_channel=0).encode()

    bridge = Contrary()
    assert bridge.select_mode(0) == 0, "the write took despite the error"


# --- registers whose acknowledgement does not describe what happened --------


def _misc_bank(*, error_on=(), ignore=()):
    """A miscellaneous byte bank that answers like the real machine.

    ``error_on`` indices reply with error code 1 and perform the write
    anyway -- byte[2], byte[4] and the mode all do this. ``ignore`` indices
    reply OK and do nothing, which byte[4] also does in one state. Between
    them there is no reply that can be believed, which is the point.
    """
    from s3k import messages as m

    store = {}

    def handler(frame):
        channel, command, payload = m.parse_frame(frame)
        if command == m.Command.RMISCDATA:
            request = m.HeaderRequest.decode(frame)
            return m.HeaderData(
                command=m.Command.MISCDATA, index=request.index, selector=1,
                offset=0, data=bytes([store.get(request.index, 0)]),
                exclusive_channel=channel,
            ).encode()
        if command == m.Command.MISCDATA:
            data = m.HeaderData.decode(frame)
            if data.index not in ignore:
                store[data.index] = data.data[0]
            code = 1 if data.index in error_on else 0
            return m.Reply(code=code, exclusive_channel=channel).encode()
        return None

    return handler, store


def test_a_selection_write_believes_the_register_not_the_reply():
    """byte[2] answers error code 1 and performs the write regardless."""
    handler, store = _misc_bank(error_on={2, 4})
    bridge = _bridge_with(handler)

    bridge.select_partition(5)          # must not raise
    assert store[2] == 5


def test_a_selection_write_still_raises_when_the_register_did_not_move():
    """Swallowing the error must not swallow a genuine refusal."""
    import pytest
    from s3k.bridge import DeviceError

    handler, store = _misc_bank(error_on={2}, ignore={2})
    bridge = _bridge_with(handler)

    with pytest.raises(DeviceError, match="register reads"):
        bridge.select_partition(5)


def test_choosing_a_drive_forces_the_machine_to_go_and_look():
    """Writing the ID records a choice; it does not act on it.

    Sweeping the ID and reading after each write returned the same volume
    list every time, and reading after a following write returned each
    drive's real list one step behind -- off by exactly one. So the ID write
    must be followed by something that triggers the re-read.
    """
    handler, store = _misc_bank(error_on={4})
    bridge = _bridge_with(handler)
    bridge.select_drive(3)

    assert store[11] == 3
    assert 4 in store, "nothing triggered the re-read"


def test_an_out_of_range_page_is_refused_here_because_the_machine_will_not():
    """Writing 11 to the page register stops the machine answering.

    Not an error reply, not a clamp -- it needs a power cycle. The document's
    "eleven modes available from the eight mode keys" matches the 0-10 the
    register takes, and 11 is past the end of that.
    """
    import pytest

    handler, store = _misc_bank()
    bridge = _bridge_with(handler)

    for good in (0, 8, 10):
        bridge.select_mode(good)
        assert store[91] == good

    for bad in (11, 16, 255, -1):
        with pytest.raises(ValueError, match="power cycle"):
            bridge.select_mode(bad)
    assert store[91] == 10, "nothing out of range may reach the wire"
