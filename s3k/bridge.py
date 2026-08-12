# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
# The throttled-output queue, the MultiIn merged-input facade, the
# rtmidi-backend-client leak fix and the port-enumeration helpers are ported
# from the sibling eosed project's eos/bridge.py, which ports them from
# k2kremote (k2kremote/midi_bridge.py), which ports them from mpc2emu
# (tests/re_banks/krz_sysex_live.py):
#   Copyright (C) 2025-2026  mpc2emu contributors — GPL-2.0-or-later
#   Copyright (C) 2026  k2kremote contributors — GPL-2.0-or-later
#   Copyright (C) 2026  eosed contributors — GPL-2.0-or-later
# The autodetect strategy follows k2kremote's rather than eosed's, for the
# reason given in the module docstring.
#
# s3ked is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# s3ked is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""MIDI transport and high-level operations for the Akai S1000/S3000 protocol.

**Nothing in this module has been run against hardware.** See DISCLAIMER.md.

Three things here are worth reading before trusting them:

**The throttle is measured, and the two gaps are not the same number.**
``SEND_GAP`` (10 ms) paces *requests*, which are self-pacing anyway since each
blocks for its reply. ``WRITE_GAP`` (75 ms) paces *unacknowledged* writes, and
is the one that matters: the machine consumes writes at ~13.3/s, and a
fire-and-forget sender faster than that has writes dropped **silently**. Both
were walked down against an S3000XL -- RESOLUTION_NOTES §6. k2kremote's 120 ms
was RE'd against a Kurzweil K2000 and never applied here.

The practical advice is to leave ``confirm=True``: an acknowledged write is
paced by the device at exactly the rate a safe fire-and-forget burst achieves,
so going unacknowledged buys no throughput and only removes the guarantee.

**Autodetect follows k2kremote's model, not eosed's, because it has to.**
eosed can probe with a standard Universal Device Inquiry; this protocol has
no such thing, and -- more awkwardly -- **no broadcast address**. A device
answers only on its own exclusive channel, and the only message that reports
what that channel is (``STAT``) can itself only be obtained by addressing the
right channel. So there is no way to ask "who is out there?"; discovery is a
sweep, and by default a sweep of one channel (0, the factory default) across
every port. Widen it with ``channels=`` and expect it to cost proportionally.

The discriminator is that the reply's operation code is ``STAT`` (``0x01``)
while the probe sent ``RSTAT`` (``0x00``). Matching on a *different* opcode is
what tells a real device from a MIDI-Thru loop echoing our own bytes back --
the same trick k2kremote uses with ALLTEXT/SCREENREPLY.

**Writes are acknowledged, so use the acknowledgement.** ``REPLY`` (``0x16``)
returns ok/error. :meth:`S3kBridge.set_header_bytes` waits for it by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import rtmidi  # noqa: E402

from s3k import messages as m
from s3k import params as p

__all__ = [
    "SEND_GAP",
    "WRITE_GAP",
    "BLOCK_IDENT",
    "DEFAULT_TIMEOUT",
    "AUTODETECT_TIMEOUT",
    "DEFAULT_CONFIG_PATH",
    "MidiUnavailable",
    "AmbiguousDevice",
    "DeviceError",
    "list_ports",
    "bidirectional_ports",
    "load_last_ports",
    "save_last_ports",
    "ThrottledOut",
    "MultiIn",
    "S3kBridge",
    "DeviceStatus",
]

# --- defaults --------------------------------------------------------------

#: Gap enforced between outgoing SysEx *requests*.
#:
#: MEASURED on an S3000XL, 2026-08-10 (RESOLUTION_NOTES §6). A request is
#: followed by a blocking wait for its reply, so it is self-pacing: single
#: parameter reads ran 40/40 clean at every gap down to zero, and the rate
#: saturates at ~94/s (10.6 ms round trip) from 10 ms downward. Anything below
#: the round trip is free, because the gap is owed *after* a send and overlaps
#: the wait. 10 ms keeps a little headroom while costing nothing.
SEND_GAP = 0.010

#: Gap enforced between outgoing SysEx *writes* that are not acknowledged.
#:
#: MEASURED, and the only one of the two that bites. The machine consumes
#: writes at ~13.3/s -- roughly 75 ms each, most of it its own recalculation
#: and screen redraw -- so a fire-and-forget sender faster than that grows an
#: unbounded queue and writes are dropped **silently**. A 150-write burst lost
#: 36 at 50 ms and none at 75 ms.
#:
#: The old default was 0.05, which passed a 40-write burst by being short
#: enough for the buffer to absorb, and would have lost a quarter of a longer
#: one. It was a guess, and it was wrong in the dangerous direction.
#:
#: Prefer ``confirm=True`` and this never matters: an acknowledged write waits
#: for ``REPLY`` and so runs at the device's own 13.3/s anyway. Fire-and-forget
#: buys **no** throughput on this family -- pace it safely and it is exactly as
#: fast -- so its only effect is to trade a guarantee for nothing.
WRITE_GAP = 0.075

DEFAULT_TIMEOUT = 2.0
AUTODETECT_TIMEOUT = 1.0

# Bounds on the autodetect reply drain. Neither is a protocol limit -- they
# exist so a port that never runs dry cannot spin or accumulate without end.
# (eosed learned this the expensive way: the equivalent loop there grew to
# 12 GB against a fake that re-answered every call.)
_MAX_PROBE_DRAIN = 64
_MAX_PROBE_REPLIES = 32

DEFAULT_CONFIG_PATH = "config.toml"


class MidiUnavailable(RuntimeError):
    """This host has no MIDI backend at all.

    Deliberately distinct from "no ports": a machine with an ALSA sequencer
    and nothing plugged in enumerates cleanly and returns empty lists. This
    is the harder failure -- no sequencer whatsoever -- and a caller wants to
    tell the user something different in each case.
    """


class DeviceError(RuntimeError):
    """The device answered REPLY/error, or answered something unusable."""


class AmbiguousDevice(RuntimeError):
    """More than one device answered the status probe.

    Exclusive channel is how this protocol expects machines to be told apart,
    and :data:`s3k.messages.Command.SETEX` exists to assign them. When two
    machines answer there is no basis for choosing, so autodetect refuses
    rather than binding to whichever replied first -- which would be decided
    by MIDI port enumeration order and could change between reboots.

    Two machines left on the *same* exclusive channel are indistinguishable
    here, exactly as one machine heard on two input ports is; that
    configuration is a user-side mistake this cannot detect.

    NOT VERIFIED AGAINST HARDWARE -- like everything else in this project,
    but worth singling out, since no two real machines have ever been
    connected at once.
    """

    def __init__(self, devices):
        self.devices = devices  # [(channel, version, recv_port), ...]
        # The version field is carried in `devices` but deliberately not
        # shown: it does not mean what the document says (§10), and channel
        # plus port already identify which machine to choose.
        listing = "\n".join(
            f"  exclusive channel {ch} on {port}" for ch, _ver, port in devices
        )
        super().__init__(
            f"{len(devices)} samplers answered:\n{listing}\n"
            "Pass --exclusive-channel N to choose one, or pin the ports "
            "explicitly with send_port/recv_port in the config file."
        )


# --- config.toml: a flat, local, gitignored key/value store -----------------
# Read-modify-write, never a blind overwrite, so unrelated keys survive each
# other's saves -- this file holds more than one independent setting.


def _read_config_dict(path: str) -> dict:
    import os
    import tomllib

    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except Exception:
        return {}


def _write_config_dict(data: dict, path: str) -> None:
    lines = ["# s3ked local config — gitignored, safe to delete."]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    try:
        with open(path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # the cache is a convenience, not required for correctness


def load_last_ports(path: str = DEFAULT_CONFIG_PATH) -> Optional[Tuple[str, str]]:
    """The send/receive pair that answered last time, if any.

    A full sweep tries every output port at up to a second each; on a host
    with two dozen ports that is tens of seconds. Trying the remembered pair
    first turns the common case into one round trip.
    """
    data = _read_config_dict(path)
    send_port = data.get("send_port")
    recv_port = data.get("recv_port")
    if isinstance(send_port, str) and isinstance(recv_port, str):
        return send_port, recv_port
    return None


def save_last_ports(
    send_port: str, recv_port: str, path: str = DEFAULT_CONFIG_PATH
) -> None:
    data = _read_config_dict(path)
    data["send_port"] = send_port
    data["recv_port"] = recv_port
    _write_config_dict(data, path)


def load_exclusive_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    value = _read_config_dict(path).get("exclusive_channel")
    return value if isinstance(value, int) else None


def save_exclusive_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    data = _read_config_dict(path)
    data["exclusive_channel"] = int(channel)
    _write_config_dict(data, path)


# --- MIDI port enumeration --------------------------------------------------
# python-rtmidi's close_port() does not tear down the backend ALSA sequencer
# client; only delete() does. Every transient MidiIn/MidiOut -- even one built
# solely to call get_ports() -- would otherwise orphan a client until process
# exit, and on a host with many MIDI ports a single autodetect sweep could
# exhaust the sequencer's client slots.


def _delete_quiet(port) -> None:
    try:
        port.delete()
    except Exception:
        pass


def _probe(factory, what: str):
    """Construct a transient rtmidi probe, or say why we couldn't.

    rtmidi raises out of the *constructor* when the backend is missing, so
    this cannot be handled at the get_ports() call.
    """
    try:
        return factory()
    except Exception as exc:  # rtmidi raises SystemError/RuntimeError here
        raise MidiUnavailable(
            f"no MIDI backend available on this host ({what}: {exc})"
        ) from exc


def _enum_in() -> List[str]:
    probe = _probe(rtmidi.MidiIn, "input")
    try:
        return probe.get_ports()
    finally:
        _delete_quiet(probe)


def _enum_out() -> List[str]:
    probe = _probe(rtmidi.MidiOut, "output")
    try:
        return probe.get_ports()
    finally:
        _delete_quiet(probe)


def list_ports() -> Tuple[List[str], List[str]]:
    """``(input_port_names, output_port_names)`` available on this host."""
    return _enum_in(), _enum_out()


def bidirectional_ports() -> List[str]:
    """Names present as both an input and an output."""
    ins, outs = list_ports()
    in_set = set(ins)
    return [name for name in outs if name in in_set]


def _open_out(port_name: str) -> "rtmidi.MidiOut":
    out = rtmidi.MidiOut()
    names = out.get_ports()
    if port_name not in names:
        _delete_quiet(out)
        raise RuntimeError(f"no output port named {port_name!r}; have {names}")
    out.open_port(names.index(port_name))
    return out


def _open_in(port_name: str) -> "rtmidi.MidiIn":
    in_port = rtmidi.MidiIn(queue_size_limit=8192)
    names = in_port.get_ports()
    if port_name not in names:
        _delete_quiet(in_port)
        raise RuntimeError(f"no input port named {port_name!r}; have {names}")
    in_port.open_port(names.index(port_name))
    in_port.ignore_types(sysex=False)
    return in_port


class ThrottledOut:
    """Wrap an ``rtmidi.MidiOut`` so SysEx never floods the device.

    Only ``0xF0``-leading messages are gapped; ordinary MIDI passes straight
    through.

    Two gaps, because the two kinds of send need different protection:

    * A **request** is followed by a blocking wait for its reply, so the round
      trip already separates it from the next send.
    * A **write** may be fire-and-forget, with no reply to pace against, so
      the gap is the only thing between us and an overrun input buffer -- and
      the failure mode is silent, since a lost write raises nothing and is
      found only by reading back.

    So ``write_gap`` should be the conservative one and ``gap`` may be cut.
    ``write_gap`` defaults to ``gap``.

    The gap is applied as time owed *after* a send -- how long the device is
    given to digest what it was handed -- so the wait before any send is
    determined by what preceded it, not by what is about to go out.
    """

    def __init__(
        self,
        port,
        gap: float = SEND_GAP,
        *,
        write_gap: Optional[float] = None,
    ):
        self._port = port
        self._gap = gap
        # Falls back to WRITE_GAP, deliberately *not* to `gap`. Since the two
        # were measured apart (§6) -- 10 ms for a self-pacing request, 75 ms
        # for an unacknowledged write -- inheriting a small read gap would
        # silently hand a fire-and-forget caller an unsafe one.
        self._write_gap = WRITE_GAP if write_gap is None else write_gap
        self._last = 0.0
        self._owed = 0.0

    def send_message(self, message, *, write: bool = False) -> None:
        is_sysex = len(message) > 0 and message[0] == 0xF0
        if not is_sysex:
            self._port.send_message(message)
            return
        wait = self._owed - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._port.send_message(message)
        self._last = time.time()
        self._owed = self._write_gap if write else self._gap

    def __getattr__(self, name):
        return getattr(self._port, name)


class MultiIn:
    """An ``rtmidi.MidiIn``-compatible facade polling one or more ports, merged.

    With ``exact=False`` every input whose name *contains* ``name`` is opened
    and polled in turn; with ``exact=True`` (used after autodetect, where the
    cabling is known) only the input whose name *equals* ``name`` is opened.
    """

    def __init__(self, name: str, *, exact: bool = False):
        self.ports: List = []
        for index, port_name in enumerate(_enum_in()):
            matches = (
                (port_name == name) if exact else (name.lower() in port_name.lower())
            )
            if matches:
                port = rtmidi.MidiIn(queue_size_limit=8192)
                port.open_port(index)
                port.ignore_types(sysex=False)
                self.ports.append(port)
        if not self.ports:
            raise RuntimeError(f"no input port matching {name!r}")

    def get_message(self):
        for port in self.ports:
            message = port.get_message()
            if message is not None:
                return message
        return None

    def get_ports(self) -> List[str]:
        return _enum_in()

    def close_port(self) -> None:
        for port in self.ports:
            port.close_port()
            _delete_quiet(port)
        self.ports = []


# --- the bridge -------------------------------------------------------------


class DeviceStatus:
    """A decoded ``STAT`` reply, with the arithmetic a caller actually wants."""

    def __init__(self, status: m.Status):
        self.raw = status
        self.version = status.version
        self.max_blocks = status.max_blocks
        self.free_blocks = status.free_blocks
        self.max_words = status.max_words
        self.free_words = status.free_words
        self.exclusive_channel = status.exclusive_channel_setting

    @property
    def used_blocks(self) -> int:
        return self.max_blocks - self.free_blocks

    @property
    def used_words(self) -> int:
        return self.max_words - self.free_words

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DeviceStatus v{self.version} channel={self.exclusive_channel} "
            f"blocks={self.used_blocks}/{self.max_blocks} "
            f"words={self.used_words}/{self.max_words}>"
        )


#: The block-identifier byte every structure carries at offset 0.
#:
#: Confirmed on hardware 2026-08-10, and already half-documented in
#: ``params.py`` as ``KGIDENT``/``SHIDENT`` ("Block identifier"). It is the
#: cheapest possible defence against §11 Finding A: an out-of-range extended
#: read returns the *previous* read's buffer rather than an error, so a reply
#: can be well-formed, plausible, and from an entirely different structure.
#: Checking one byte catches that.
#:
#: ``multipart`` also reads ``0x01``, consistent with a multi part being a
#: program header, so it cannot be told apart from ``program`` this way and is
#: deliberately not listed.
BLOCK_IDENT: Dict[str, int] = {
    "program": 0x01,
    "keygroup": 0x02,
    "sample": 0x03,
}


#: Which extended operation reads each region, and which writes it.
_REGION_OPS: Dict[str, Tuple[int, int]] = {
    "program": (m.Command.RPHEADER, m.Command.PHEADER),
    "keygroup": (m.Command.RKHEADER, m.Command.KHEADER),
    "sample": (m.Command.RSHEADER, m.Command.SHEADER),
    "multi": (m.Command.RMULTIDATA, m.Command.MULTIDATA),
    "multipart": (m.Command.RMULTIDATA, m.Command.MULTIDATA),
}

#: Regions whose selector byte is fixed by the region rather than the caller.
#:
#: The multi file's two sections share one opcode pair and are told apart by
#: the selector alone (0 = file header, 1 = part), so the caller must not be
#: able to pass the wrong one. For ``keygroup`` the selector is the keygroup
#: number and genuinely belongs to the caller; for ``program`` and ``sample``
#: the byte is documented as reserved and sent as zero.
_REGION_SELECTOR: Dict[str, int] = {
    "program": 0,
    "sample": 0,
    "multi": 0,
    "multipart": 1,
}


#: Bytes per volume-list record: 12 of name, then type and three reserved.
_VOLUME_RECORD = 16
#: Records to ask for per round trip. 16 is the most the device answered with.
_VOLUMES_PER_READ = 16


#: Bytes per harddisk-directory record, per the S3000 spec.
_DIRECTORY_RECORD = 24

#: Bytes 12-15 of a real directory record. The device keeps answering past the
#: end of the list, so this is what separates an entry from an echo.
_DIRECTORY_BLANK_EXTENSION = b"\x20\x20\x20\x20"

#: `ss` in RHDDIR: which kind of item the listing starts at.
DIRECTORY_KINDS = {
    0: "volume data",
    1: "program",
    2: "sample",
    3: "cue list",
    4: "take list",
    5: "effects file",
    6: "drum file",
}


#: Bytes of file header an Akai sample file carries on disk, on top of its
#: audio. Measured as exactly 150 on all 60 samples that could be compared
#: against their loaded `SLNGTH`, with no exceptions (§69).
SAMPLE_FILE_OVERHEAD = 150

#: `raw[16]` of a directory record.
ITEM_PROGRAM = 0x70
ITEM_SAMPLE = 0x73


@dataclass(frozen=True)
class _DirectoryEntry:
    """One item in the loaded volume's directory.

    The 24 bytes decode as (§69):

    ===========  ====================================================
    ``[0:12]``   name, in the device's charset
    ``[12:16]``  extension: four spaces on every real entry, and the
                 marker that the list has ended when it is not
    ``[16]``     item type -- 0x70 program, 0x73 sample
    ``[17:20]``  file size on disk in BYTES, little-endian
    ``[20:22]``  location, increasing down the listing
    ``[22:24]``  0x1e 0x09 on every entry seen
    ===========  ====================================================
    """

    index: int
    name: str
    raw: bytes

    @property
    def item_type(self) -> int:
        return self.raw[16]

    @property
    def is_sample(self) -> bool:
        return self.item_type == ITEM_SAMPLE

    @property
    def is_program(self) -> bool:
        return self.item_type == ITEM_PROGRAM

    @property
    def size_bytes(self) -> int:
        """The file's size on disk."""
        return int.from_bytes(self.raw[17:20], "little")

    @property
    def audio_words(self) -> int:
        """Sample words this file will occupy in memory once loaded.

        ``size_bytes`` is the file including its 150-byte header, and the
        machine reports memory in 16-bit words -- so this is what a bank
        builder has to sum to know whether a volume fits. Zero for a program,
        whose size is header data rather than audio.
        """
        if not self.is_sample:
            return 0
        return max(0, self.size_bytes - SAMPLE_FILE_OVERHEAD) // 2


@dataclass(frozen=True)
class _Volume:
    """One volume on the attached disk."""

    index: int
    name: str
    kind: int


def _selector_for(region: str, keygroup: int) -> int:
    """The selector byte to send for *region*."""
    fixed = _REGION_SELECTOR.get(region)
    return keygroup if fixed is None else fixed


class S3kBridge:
    """Talk to one Akai S1000/S3000-family sampler.

    Not thread-safe: nothing may issue two requests on one connection
    concurrently. Callers with a UI thread should serialise every call
    through a single lock or worker.
    """

    def __init__(
        self,
        midi_out,
        midi_in,
        description: str,
        *,
        exclusive_channel: int = m.DEFAULT_EXCLUSIVE_CHANNEL,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.out = midi_out
        self.inp = midi_in
        self.description = description
        self.exclusive_channel = exclusive_channel
        self.timeout = timeout

    # -- construction -------------------------------------------------------

    @classmethod
    def standard(
        cls,
        port_name: str,
        *,
        gap: float = SEND_GAP,
        write_gap: Optional[float] = None,
        exclusive_channel: int = m.DEFAULT_EXCLUSIVE_CHANNEL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "S3kBridge":
        """Connect to one bidirectional port by name."""
        out = ThrottledOut(_open_out(port_name), gap, write_gap=write_gap)
        inp = MultiIn(port_name, exact=True)
        return cls(
            out,
            inp,
            port_name,
            exclusive_channel=exclusive_channel,
            timeout=timeout,
        )

    @classmethod
    def autodetect(
        cls,
        *,
        gap: float = SEND_GAP,
        write_gap: Optional[float] = None,
        channels: Sequence[int] = (m.DEFAULT_EXCLUSIVE_CHANNEL,),
        timeout: float = AUTODETECT_TIMEOUT,
        on_try: Optional[Callable[[str], None]] = None,
        config_path: Optional[str] = DEFAULT_CONFIG_PATH,
    ) -> "S3kBridge":
        """Find a sampler by sweeping output ports with a harmless RSTAT.

        ``channels`` defaults to the factory channel alone. Widening it
        multiplies the sweep cost by its length, because this protocol offers
        no broadcast address -- see the module docstring.
        """
        cached = load_last_ports(config_path) if config_path else None
        if cached:
            found = cls._try_pair(
                cached[0], cached[1], channels, timeout, gap, write_gap
            )
            if found is not None:
                return found

        outs = _enum_out()
        ins = _enum_in()
        if not outs or not ins:
            raise RuntimeError(
                f"need at least one MIDI input and output; "
                f"have {len(ins)} in, {len(outs)} out"
            )

        # Open every input up front, then sweep the outputs. A device's reply
        # can arrive on an input with a quite different name from the output
        # that reached it, so there is no shortcut here.
        listeners = []
        try:
            for name in ins:
                try:
                    listeners.append((name, _open_in(name)))
                except Exception:
                    continue  # a port we cannot open is not a failure of the sweep

            found: List[Tuple[int, str, str, str]] = []  # (chan, ver, send, recv)
            for send_name in outs:
                if on_try is not None:
                    on_try(send_name)
                try:
                    out = _open_out(send_name)
                except Exception:
                    continue
                try:
                    for channel in channels:
                        for name, port in listeners:
                            _drain_port(port)
                        out.send_message(
                            list(m.RequestStatus(exclusive_channel=channel).encode())
                        )
                        found.extend(
                            (ch, ver, send_name, recv)
                            for ch, ver, recv in _collect_status(listeners, timeout)
                        )
                finally:
                    out.close_port()
                    _delete_quiet(out)

            # One machine heard on several input ports is one machine.
            by_channel: Dict[int, Tuple[int, str, str, str]] = {}
            for entry in found:
                by_channel.setdefault(entry[0], entry)

            if not by_channel:
                raise RuntimeError(
                    "no sampler answered RSTAT on any port"
                    + (
                        ""
                        if len(channels) > 1
                        else " (only exclusive channel "
                        f"{channels[0]} was probed; the machine may be on "
                        "another one -- there is no broadcast address in this "
                        "protocol, so widen the sweep or set it explicitly)"
                    )
                )
            if len(by_channel) > 1:
                raise AmbiguousDevice(
                    sorted((ch, ver, recv) for ch, ver, _s, recv in by_channel.values())
                )

            channel, _version, send_name, recv_name = next(iter(by_channel.values()))
        finally:
            for _name, port in listeners:
                port.close_port()
                _delete_quiet(port)

        # Reopen the winning pair now that every probe port is closed --
        # holding one open across the teardown above would leak the very ALSA
        # client _delete_quiet exists to reclaim.
        bridge = cls(
            ThrottledOut(_open_out(send_name), gap, write_gap=write_gap),
            MultiIn(recv_name, exact=True),
            f"{send_name} -> {recv_name}",
            exclusive_channel=channel,
            timeout=max(timeout, DEFAULT_TIMEOUT),
        )
        if config_path:
            save_last_ports(send_name, recv_name, config_path)
            save_exclusive_channel(channel, config_path)
        return bridge

    @classmethod
    def _try_pair(
        cls,
        send_name: str,
        recv_name: str,
        channels: Sequence[int],
        timeout: float,
        gap: float,
        write_gap: Optional[float],
    ) -> Optional["S3kBridge"]:
        """Try one remembered send/receive pair; None if it does not answer."""
        try:
            out = _open_out(send_name)
        except Exception:
            return None
        try:
            port = _open_in(recv_name)
        except Exception:
            _delete_quiet(out)
            return None
        try:
            for channel in channels:
                _drain_port(port)
                out.send_message(
                    list(m.RequestStatus(exclusive_channel=channel).encode())
                )
                answers = _collect_status([(recv_name, port)], timeout)
                if answers:
                    found_channel = answers[0][0]
                    break
            else:
                return None
        finally:
            port.close_port()
            _delete_quiet(port)
            out.close_port()
            _delete_quiet(out)

        return cls(
            ThrottledOut(_open_out(send_name), gap, write_gap=write_gap),
            MultiIn(recv_name, exact=True),
            f"{send_name} -> {recv_name}",
            exclusive_channel=found_channel,
            timeout=max(timeout, DEFAULT_TIMEOUT),
        )

    # -- low-level I/O ------------------------------------------------------

    def _send(self, frame: bytes, *, write: bool = False) -> None:
        self.out.send_message(list(frame), write=write)

    def _drain(self) -> None:
        while self.inp.get_message() is not None:
            pass

    def _receive(self, timeout: Optional[float] = None) -> bytes:
        """Wait for the next SysEx frame addressed to us."""
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while time.time() < deadline:
            message = self.inp.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            data = bytes(message[0])
            if not data or data[0] != m.SOX:
                continue
            try:
                channel, _command, _payload = m.parse_frame(data)
            except ValueError:
                continue  # somebody else's SysEx on a shared input
            if channel != self.exclusive_channel:
                continue
            return data
        raise TimeoutError(f"no reply within {timeout or self.timeout}s")

    def send_and_receive(
        self, frame: bytes, *, timeout: Optional[float] = None
    ) -> bytes:
        self._drain()
        self._send(frame)
        return self._receive(timeout)

    def close(self) -> None:
        for port in (self.out, self.inp):
            try:
                port.close_port()
            except Exception:
                pass
        for port in (self.out, self.inp):
            _delete_quiet(port)

    # -- high-level operations ---------------------------------------------

    def status(self, *, timeout: Optional[float] = None) -> DeviceStatus:
        """RSTAT -> STAT. Harmless, read-only; also the liveness check."""
        reply = self.send_and_receive(
            m.RequestStatus(exclusive_channel=self.exclusive_channel).encode(),
            timeout=timeout,
        )
        return DeviceStatus(m.Status.decode(reply))

    def is_connected(self, *, timeout: float = 1.0) -> bool:
        try:
            self.status(timeout=timeout)
            return True
        except (TimeoutError, ValueError):
            return False

    def program_list(self, *, timeout: Optional[float] = None) -> List[str]:
        """RPLIST -> PLIST. Index in this list is the program number to use.

        The specification is explicit that these positions, not the programs'
        own MIDI program numbers, are what addresses a program: "The machine
        holds sequential numbers, starting at zero for items in this list and
        these numbers should be used to identify a specific program."
        """
        reply = self.send_and_receive(
            m.RequestProgramList(exclusive_channel=self.exclusive_channel).encode(),
            timeout=timeout,
        )
        return m.ProgramList.decode(reply).names

    #: One entry of the disk's volume list. ``kind`` is the record's type byte;
    #: every volume on the machine measured so far reports 3.
    Volume = _Volume

    def volume_list(self, *, limit: int = 512,
                    timeout: Optional[float] = None) -> List["_Volume"]:
        """RVOLLIST -> VOLLIST. The volumes on the attached SCSI disk.

        The reply is a run of **16-byte records**: a 12-character name in the
        device's own charset, then a 4-byte tail whose first byte is the
        volume type. ``index`` is the volume to start at and ``count`` decides
        how many records come back, so this pages rather than asking one at a
        time -- 100 volumes take 7 round trips at 16 records each, about 1.2 s.

        **The end is marked by the TYPE byte, not by an empty name.** Past the
        last volume the record is all zeroes, and an all-zero name decodes to
        ``000000000000`` rather than to blank, because index 0 of the Akai
        charset is the character ``0``. Stopping on a blank name would run
        forever; stopping on a name of zeros would truncate a disk that
        happens to have a volume called that.

        Read-only. There is no counterpart operation that loads a volume: the
        documented protocol can enumerate the disk and nothing more, so this
        shows what is there and the front panel still has to load it.
        """
        out: List[_Volume] = []
        start = 0
        while start < limit:
            frame = m.HeaderRequest(
                command=m.Command.RVOLLIST,
                index=start,
                offset=0,
                count=_VOLUME_RECORD * _VOLUMES_PER_READ,
                exclusive_channel=self.exclusive_channel,
            ).encode()
            reply = self.send_and_receive(frame, timeout=timeout)
            _channel, command, _payload = m.parse_frame(reply)
            if command == m.Command.REPLY:
                self._raise_for_reply(reply, "reading the volume list")
            data = m.HeaderData.decode(reply).data
            if not data:
                break
            for at in range(0, len(data) - _VOLUME_RECORD + 1, _VOLUME_RECORD):
                record = data[at:at + _VOLUME_RECORD]
                if record[12] == 0:
                    return out
                out.append(_Volume(
                    index=start + at // _VOLUME_RECORD,
                    name=m.decode_name(record[:12]).rstrip(),
                    kind=record[12],
                ))
            start += len(data) // _VOLUME_RECORD
        return out

    DirectoryEntry = _DirectoryEntry

    # -- the load source ----------------------------------------------------
    #
    # Miscellaneous BYTE-bank indices, found by changing each on the front
    # panel and seeing which moved (§70). The spec documents the addressing
    # and not the meanings, so every one of these is measured.
    _MISC_DEVICE_TYPE = 0        # floppy / hard / flash
    _MISC_PARTITION = 2          # 0 = A. Writable, and the machine re-reads.
    _MISC_SCSI_DRIVE_ID = 11
    _MISC_SCSI_LOCAL_ID = 12
    #: NOT the volume, despite reading as one. It is the value of whichever
    #: field the panel's cursor is on: 3 while the LOAD page showed volume 3,
    #: and 0 in SINGLE, which has no such field. That is why writing it never
    #: moved anything -- the panel writes it, the machine does not read it.
    _MISC_CURSOR_VALUE = 49
    #: The current main-menu page. An internal enumeration with gaps -- not
    #: the button positions: GLOBAL is the second button of the second row and
    #: reads 8, where its position would be 5.
    _MISC_MODE = 91
    _MISC_SELECTION_HELD = 4     # 1 suppresses the re-read. See below.

    #: Bytes 6-9 are the LOAD TYPE, mirrored -- writing one moves all four.
    #: The panel's values are 1 (ALL PROGS + SAMPLES) and 2 (ENTIRE VOLUME);
    #: 0 is the power-on default.
    #:
    #: **They are not exposed for writing, and that is deliberate.** Writing
    #: one while the LOAD page had a partition and volume selected started an
    #: actual disk load, and the machine then sat at "BUSY" until it was power
    #: cycled. Whether the load was clean and disrupted by concurrent RSTAT
    #: probing, or the write left the machine in a bad state, is NOT
    #: established -- and those have different consequences, so neither is
    #: assumed. See RESOLUTION_NOTES §71.
    #:
    #: A load also CLEARS RAM, so if this is ever exposed it belongs behind
    #: the arm-then-fire treatment that DELP/DELK/DELS get, not a keypress.
    _MISC_LOAD_TYPE = (6, 7, 8, 9)

    def _misc_byte(self, index: int, value: Optional[int] = None, *,
                   timeout: Optional[float] = None) -> int:
        """Read or write one byte of the miscellaneous byte bank."""
        if value is None:
            frame = m.HeaderRequest(
                command=m.Command.RMISCDATA, index=index, selector=1,
                offset=0, count=1, exclusive_channel=self.exclusive_channel,
            ).encode()
            reply = self.send_and_receive(frame, timeout=timeout)
            _c, command, _p = m.parse_frame(reply)
            if command == m.Command.REPLY:
                self._raise_for_reply(reply, f"reading misc byte {index}")
            return m.HeaderData.decode(reply).data[0]
        frame = m.HeaderData(
            command=m.Command.MISCDATA, index=index, selector=1, offset=0,
            data=bytes([value]), exclusive_channel=self.exclusive_channel,
        ).encode()
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(self._receive(timeout),
                              f"writing misc byte {index}")
        return self._misc_byte(index, timeout=timeout)

    #: Main-menu pages, by the value :attr:`_MISC_MODE` takes. Only the three
    #: that have been observed are named; the rest of the eight buttons have
    #: not been pressed with a probe running.
    MODES = {0: "SINGLE", 8: "GLOBAL", 10: "LOAD"}

    def mode(self, *, timeout: Optional[float] = None) -> int:
        """Which main-menu page the machine is showing."""
        return self._misc_byte(self._MISC_MODE, timeout=timeout)

    def select_mode(self, mode: int, *, timeout: Optional[float] = None) -> int:
        """Move the machine to a main-menu page. **This writes.**

        There is no button injection in this protocol -- the specification has
        no keypress message and no panel echo. This is not that: the current
        page is a variable, and writing it moves the machine.

        **The device's acknowledgement cannot be trusted here.** Writing 0
        (SINGLE) answers with REPLY error code 1 and switches the page anyway;
        the other values answer OK and also switch. The reply is wrong in one
        direction here and wrong in the other for `byte[4]`, which accepts a
        write and ignores it. So this returns what the register READS BACK,
        and callers should compare against what they asked for rather than
        trusting either the ack or this method's success.
        """
        try:
            self._misc_byte(self._MISC_MODE, mode, timeout=timeout)
        except DeviceError:
            pass          # the write may well have taken; the read decides
        return self._misc_byte(self._MISC_MODE, timeout=timeout)

    def trigger_load(self, load_type: int = 1, *,
                     timeout: Optional[float] = None) -> None:
        """Load the selected volume into the machine. **This writes and acts.**

        ``load_type`` is the LOAD page's own setting: 1 is ALL PROGS +
        SAMPLES, which pulls each program together with the samples it uses,
        and 2 is ENTIRE VOLUME.

        The machine goes busy and stops answering while it works -- seconds
        for a few megabytes. **Do not poll it.** A 58.7 MB load that was
        probed every 8 seconds ran in stop-start bursts and eventually sat at
        BUSY until it was power cycled; the same trigger on a volume that fits,
        with a quiet bus, completed in seconds (§72).

        A load ADDS to what is resident rather than replacing it, so it is not
        destructive in the way DELP/DELK/DELS are -- but it will fill memory
        and stop with "insufficient waveform memory" if the volume is larger
        than the free space, leaving programs whose samples never arrived.
        Compare the directory's ``audio_words`` against free memory first.
        """
        for index in self._MISC_LOAD_TYPE[:1]:
            frame = m.HeaderData(
                command=m.Command.MISCDATA, index=index, selector=1, offset=0,
                data=bytes([load_type]),
                exclusive_channel=self.exclusive_channel,
            ).encode()
            self._drain()
            self._send(frame, write=True)

    def load_source(self, *, timeout: Optional[float] = None) -> Dict[str, int]:
        """What the front panel's LOAD page currently shows.

        ``partition`` is 0-based (0 = A) and ``volume`` is 1-based, matching
        the panel's "HARD-:C" and "Volume 001". ``device_type`` selects
        floppy, hard disk or flash -- the volume list's ``BOOT SYSTEM#`` and
        ``FLASH VOLnn`` names belong to the flash device.
        """
        return {
            "scsi_drive_id": self._misc_byte(self._MISC_SCSI_DRIVE_ID,
                                             timeout=timeout),
            "scsi_local_id": self._misc_byte(self._MISC_SCSI_LOCAL_ID,
                                             timeout=timeout),
            "device_type": self._misc_byte(self._MISC_DEVICE_TYPE,
                                           timeout=timeout),
            "partition": self._misc_byte(self._MISC_PARTITION, timeout=timeout),
            "cursor_value": self._misc_byte(self._MISC_CURSOR_VALUE,
                                            timeout=timeout),
            "mode": self._misc_byte(self._MISC_MODE, timeout=timeout),
        }

    def select_partition(self, partition: int, *,
                         timeout: Optional[float] = None) -> Dict[str, int]:
        """Move the LOAD selection to a partition. **This writes.**

        ``partition`` is 0-based, so 0 is the panel's "A". The machine
        re-reads the directory from disk: the panel follows and the drive's
        activity light flashes, both confirmed by eye. So
        :meth:`hd_directory` afterwards describes the newly selected volume,
        which is what makes a whole disk enumerable without touching the
        machine.

        **It does not load anything.** The spec is explicit that no SysEx
        operation loads from disk. This moves the selection the front panel
        would act on, and nothing more.

        **The volume cannot be moved remotely at all.** There is no volume
        register: `byte[49]` looked like one because it read 1, 2, 3 as the
        panel stepped through volumes, but it is the value of whatever field
        the cursor is on and reads 0 in a page that has no such field. Writing
        it does nothing, on single- and multi-volume discs alike (§72).

        So remote enumeration reaches whichever volume the panel last selected
        in each partition, and no further.
        **`byte[4]` has to be clear or nothing happens.** The panel sets it
        when the selection lands on a volume that does not exist -- it shows
        "INACTIVE" -- and while it is set the machine accepts a partition
        write and does not re-read, so the directory silently keeps
        describing the previous partition. That cost a confusing half hour:
        partition switching worked, then stopped, and the difference was this
        flag left set by an earlier panel change. It is cleared here.
        """
        self._misc_byte(self._MISC_SELECTION_HELD, 0, timeout=timeout)
        self._misc_byte(self._MISC_PARTITION, partition, timeout=timeout)
        return self.load_source(timeout=timeout)

    def hd_directory(self, kind: int = 1, *, limit: int = 512,
                     timeout: Optional[float] = None) -> List["_DirectoryEntry"]:
        """RHDDIR -> HDDIR. The directory of the volume the machine has LOADED.

        ``kind`` is the spec's selector -- 0 volume data, 1 program, 2 sample,
        3 cue list, 4 take list, 5 effects file, 6 drum file -- and behaves as
        a starting point rather than a filter: selector 1 returns the programs
        and then continues through the samples.

        **This is not a browser for the disk.** It reads the directory the
        machine currently holds, which is empty until a volume is loaded from
        the front panel. The spec is explicit that there is no way to load one
        over MIDI: *"There are no functions within MIDI system exclusive to
        provide direct access to and from disk files."* So the sequence is
        load at the panel, then read here.

        Records are 24 bytes: a 12-character name, a four-byte extension
        field that reads as spaces on every real entry, and eight more whose
        meaning is not documented and are returned raw rather than guessed at.

        **The end is marked by that extension field, and getting it wrong is
        expensive.** Past the last entry this layer keeps answering: first with
        records whose extension is not spaces, then by repeating entry 0 over
        and over -- the same out-of-range behaviour §11 Finding A found in the
        header reads. A stop condition of "all bytes zero" never fires, and
        the first version of this returned 188 entries for a 64-entry
        directory, two thirds of them junk that looked like names.

        A record that repeats one already seen ends the list too. The echo is
        not always of entry 0 -- on the disk here entry 63 came back identical
        to entry 13, including the eight bytes that look like a location, so
        two real files cannot account for it.

        One request per entry, deliberately. Asking for a larger ``count``
        does return more bytes, but they are NOT the following entries: with
        ``count`` 48 the second record is the first SAMPLE rather than the
        second program. It looks like paging and produces a different list.
        """
        out: List[_DirectoryEntry] = []
        seen: set = set()
        for entry in range(limit):
            frame = m.HeaderRequest(
                command=m.Command.RHDDIR,
                index=entry,
                selector=kind,
                offset=0,
                count=_DIRECTORY_RECORD,
                exclusive_channel=self.exclusive_channel,
            ).encode()
            reply = self.send_and_receive(frame, timeout=timeout)
            _channel, command, _payload = m.parse_frame(reply)
            if command == m.Command.REPLY:
                self._raise_for_reply(reply, "reading the disk directory")
            data = m.HeaderData.decode(reply).data
            if len(data) < _DIRECTORY_RECORD:
                break
            if data[12:16] != _DIRECTORY_BLANK_EXTENSION:
                break
            record = bytes(data)
            if record in seen:
                break
            seen.add(record)
            out.append(_DirectoryEntry(
                index=entry,
                name=m.decode_name(data[:12]).rstrip(),
                raw=bytes(data),
            ))
        return out

    def sample_list(self, *, timeout: Optional[float] = None) -> List[str]:
        """RSLIST -> SLIST. Index in this list is the sample number to use."""
        reply = self.send_and_receive(
            m.RequestSampleList(exclusive_channel=self.exclusive_channel).encode(),
            timeout=timeout,
        )
        return m.SampleList.decode(reply).names

    # -- byte-addressable header access -------------------------------------

    def get_header_bytes(
        self,
        region: str,
        index: int,
        offset: int,
        count: int,
        *,
        selector: int = 0,
        timeout: Optional[float] = None,
    ) -> bytes:
        """Read *count* bytes at *offset* from one header.

        ``selector`` is the second-level choice: the keygroup number for the
        keygroup region, and unused (reserved, sent as 0) for the others.
        """
        try:
            request_op, reply_op = _REGION_OPS[region]
        except KeyError:
            raise KeyError(
                f"unknown region {region!r}; expected one of {tuple(_REGION_OPS)}"
            ) from None
        frame = m.HeaderRequest(
            command=request_op,
            index=index,
            selector=selector,
            offset=offset,
            count=count,
            exclusive_channel=self.exclusive_channel,
        ).encode()
        reply = self.send_and_receive(frame, timeout=timeout)
        _channel, command, _payload = m.parse_frame(reply)
        if command == m.Command.REPLY:
            self._raise_for_reply(reply, f"reading {region} {index}")
        if command != reply_op:
            raise DeviceError(
                f"expected {reply_op:#04x} reading {region} header, "
                f"got {command:#04x}"
            )
        data = m.HeaderData.decode(reply)
        if len(data.data) != count:
            raise DeviceError(
                f"asked for {count} bytes at offset {offset}, got {len(data.data)}"
            )
        expect = BLOCK_IDENT.get(region)
        if expect is not None and offset == 0 and data.data:
            got = data.data[0]
            if got != expect:
                # §11 Finding A: this layer answers an out-of-range request
                # with whatever the last valid read left behind, so a wrong
                # index or selector comes back as a plausible-looking header
                # belonging to something else entirely.
                belongs = next(
                    (r for r, v in BLOCK_IDENT.items() if v == got), None
                )
                raise DeviceError(
                    f"reading {region} {index}: block identifier is "
                    f"{got:#04x}, expected {expect:#04x}"
                    + (f" -- this is a {belongs} block" if belongs else "")
                    + ". The device answers an out-of-range read with the "
                    "previous read's buffer instead of an error, so check the "
                    "index and selector exist (RESOLUTION_NOTES §11 Finding A)."
                )
        return data.data

    def set_header_bytes(
        self,
        region: str,
        index: int,
        offset: int,
        data: bytes,
        *,
        selector: int = 0,
        postpone: m.Postpone = m.Postpone.NONE,
        confirm: bool = True,
        timeout: Optional[float] = None,
    ) -> None:
        """Write bytes at *offset* into one header.

        With ``confirm`` (the default) this waits for the device's ``REPLY``
        and raises :class:`DeviceError` if it reports an error -- which is
        cheap insurance the sibling eosed project cannot buy, since EOS
        acknowledges almost nothing.

        ``postpone`` defers the machine's own screen redraw and/or parameter
        recalculation; see :class:`s3k.messages.Postpone` for why leaving it
        clear is usually right and why ``RECALC`` must never be left set.
        """
        try:
            _request_op, write_op = _REGION_OPS[region]
        except KeyError:
            raise KeyError(
                f"unknown region {region!r}; expected one of {tuple(_REGION_OPS)}"
            ) from None
        frame = m.HeaderData(
            command=write_op,
            index=index,
            selector=selector,
            offset=offset,
            data=bytes(data),
            postpone=postpone,
            exclusive_channel=self.exclusive_channel,
        ).encode()
        if not confirm:
            self._send(frame, write=True)
            return
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(
            self._receive(timeout), f"writing {region} {index} at offset {offset}"
        )

    def _raise_for_reply(self, frame: bytes, what: str) -> None:
        _channel, command, _payload = m.parse_frame(frame)
        if command != m.Command.REPLY:
            raise DeviceError(
                f"expected REPLY after {what}, got command {command:#04x}"
            )
        reply = m.Reply.decode(frame)
        if not reply.ok:
            raise DeviceError(f"device reported an error {what} (code {reply.code})")

    # -- parameter access, in terms of s3k.params ---------------------------

    def get_parameter(
        self,
        param,
        index: int,
        *,
        keygroup: int = 0,
        region: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        """Read one named parameter, decoded per its table entry."""
        param = param if isinstance(param, p.Parameter) else p.lookup(param, region)
        raw = self.get_header_bytes(
            param.region,
            index,
            param.offset,
            param.size,
            selector=_selector_for(param.region, keygroup),
            timeout=timeout,
        )
        return p.decode_field(param, raw)

    def set_parameter(
        self,
        param,
        index: int,
        value,
        *,
        keygroup: int = 0,
        region: Optional[str] = None,
        postpone: m.Postpone = m.Postpone.NONE,
        confirm: bool = True,
        timeout: Optional[float] = None,
    ) -> None:
        """Write one named parameter, encoded per its table entry."""
        param = param if isinstance(param, p.Parameter) else p.lookup(param, region)
        if not param.writable:
            why = "read-only" if param.readonly else "an internal block address"
            raise ValueError(f"{param.name} is {why} and must not be written")
        self.set_header_bytes(
            param.region,
            index,
            param.offset,
            p.encode_field(param, value),
            selector=_selector_for(param.region, keygroup),
            postpone=postpone,
            confirm=confirm,
            timeout=timeout,
        )

    def get_header(
        self,
        region: str,
        index: int,
        *,
        keygroup: int = 0,
        timeout: Optional[float] = None,
    ) -> Dict[str, object]:
        """Read a whole header in one request and decode every known field.

        One round trip instead of one per parameter -- which matters at 31250
        baud, where a 192-byte header nibbles up to 384 message bytes and
        still beats 130 separate exchanges by an order of magnitude.
        """
        params = p.region_params(region)
        extent = max(x.end for x in params)
        raw = self.get_header_bytes(
            region,
            index,
            0,
            extent,
            selector=_selector_for(region, keygroup),
            timeout=timeout,
        )
        return {
            x.name: p.decode_field(x, raw[x.offset : x.end])
            for x in params
        }

    # -- destructive operations --------------------------------------------
    # The specification defines no confirmation step for any of these. Callers
    # must never key-bind them -- always an explicit arm-then-fire flow.

    def delete_program(self, program: int, *, confirm: bool = True) -> None:
        """DELP. DESTRUCTIVE, one-shot, no device-side confirmation."""
        self._destructive(
            m.DeleteProgram(
                program=program, exclusive_channel=self.exclusive_channel
            ).encode(),
            f"deleting program {program}",
            confirm,
        )

    def delete_keygroup(
        self, program: int, keygroup: int, *, confirm: bool = True
    ) -> None:
        """DELK. DESTRUCTIVE, one-shot, no device-side confirmation."""
        self._destructive(
            m.DeleteKeygroup(
                program=program,
                keygroup=keygroup,
                exclusive_channel=self.exclusive_channel,
            ).encode(),
            f"deleting keygroup {keygroup} of program {program}",
            confirm,
        )

    def delete_sample(self, sample: int, *, confirm: bool = True) -> None:
        """DELS. DESTRUCTIVE, one-shot, no device-side confirmation."""
        self._destructive(
            m.DeleteSample(
                sample=sample, exclusive_channel=self.exclusive_channel
            ).encode(),
            f"deleting sample {sample}",
            confirm,
        )

    def _destructive(self, frame: bytes, what: str, confirm: bool) -> None:
        if not confirm:
            self._send(frame, write=True)
            return
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(self._receive(), what)

    def set_exclusive_channel(self, channel: int) -> None:
        """SETEX -- move the device to another exclusive channel.

        Updates our own idea of the address too, since otherwise the very next
        message would go to a channel nobody is listening on.
        """
        if not 0 <= channel <= 0x7F:
            raise ValueError(f"exclusive channel {channel} out of range")
        self._send(
            m.SetExclusiveChannel(
                new_channel=channel, exclusive_channel=self.exclusive_channel
            ).encode(),
            write=True,
        )
        self.exclusive_channel = channel


# --- autodetect helpers -----------------------------------------------------


def _drain_port(port) -> None:
    for _ in range(_MAX_PROBE_DRAIN):
        if port.get_message() is None:
            return


def _collect_status(
    listeners: Sequence[Tuple[str, object]], timeout: float
) -> List[Tuple[int, str, str]]:
    """Gather every STAT reply arriving within *timeout*.

    Waits the whole timeout even after the first answer: a second machine on
    the same wire answers too, and we need to know about it rather than bind
    to whichever was quicker.

    Bounded on both axes -- see :data:`_MAX_PROBE_DRAIN` /
    :data:`_MAX_PROBE_REPLIES`.
    """
    deadline = time.time() + timeout
    found: List[Tuple[int, str, str]] = []
    while time.time() < deadline and len(found) < _MAX_PROBE_REPLIES:
        progressed = False
        for name, port in listeners:
            for _ in range(_MAX_PROBE_DRAIN):
                message = port.get_message()
                if message is None:
                    break
                progressed = True
                data = bytes(message[0])
                try:
                    channel, command, _payload = m.parse_frame(data)
                except ValueError:
                    continue
                # The discriminator: we sent RSTAT (0x00) and require STAT
                # (0x01). A MIDI-Thru loop echoing our own probe back would
                # carry 0x00 and is rejected here.
                if command != m.Command.STAT:
                    continue
                try:
                    status = m.Status.decode(data)
                except ValueError:
                    continue
                found.append((channel, status.version, name))
        if not progressed:
            time.sleep(0.005)
    return found
