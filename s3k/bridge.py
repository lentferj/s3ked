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

import sys
import signal
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
    "BoardNotFitted",
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


class BoardNotFitted(RuntimeError):
    """A field or page needing an expansion board that is not declared fitted.

    Separate from :class:`DeviceError` deliberately: nothing went wrong on the
    wire, and the caller can fix it by declaring the board rather than by
    retrying.
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


#: Set by the first save that had to leave an unreadable config alone.
_warned_unreadable = False


# --- config.toml: a flat, local, gitignored key/value store -----------------
# Read-modify-write, never a blind overwrite, so unrelated keys survive each
# other's saves -- this file holds more than one independent setting.


def _read_config(path: str) -> Tuple[dict, str]:
    """``(settings, status)`` where status is ok / missing / unreadable.

    The distinction matters because saving is read-modify-write. A file that
    cannot be parsed and a file that does not exist both yield no settings,
    and collapsing them turns the next save into a blind overwrite of a file
    this code never understood -- so one stray bracket costs the user every
    other setting in it, silently.

    Found in the sibling eosed (its §24) and present here identically: the
    write used the locale codec, which on Windows is cp1252, and the em dash
    in the header line below then lands as a byte `tomllib` refuses. That was
    one cause; the masking is the bug, and it fires for any parse failure.
    """
    import os
    import tomllib

    if not os.path.exists(path):
        return {}, "missing"
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}, "unreadable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # written by a pre-fix build under a non-UTF-8 locale; decode
        # leniently so hand-edited keys survive, and the next save repairs it
        text = raw.decode("cp1252", errors="replace")
    try:
        return tomllib.loads(text), "ok"
    except Exception:
        return {}, "unreadable"


def _read_config_dict(path: str) -> dict:
    """Just the settings, for readers that cannot act on a failure."""
    return _read_config(path)[0]


def _update_config(path: str, **changes) -> None:
    """Read-modify-write, or leave an unreadable file alone and say so."""
    global _warned_unreadable

    data, status = _read_config(path)
    if status == "unreadable":
        if not _warned_unreadable:
            _warned_unreadable = True
            print(f"s3ked: {path} could not be parsed, so settings are not "
                  f"being saved. Fix or delete it; nothing has been "
                  f"overwritten.", file=sys.stderr)
        return
    data.update(changes)
    _write_config_dict(data, path)


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
        # encoding= is not optional: without it Python uses the locale codec,
        # cp1252 on Windows, and the em dash above becomes a byte tomllib
        # cannot read back. Both ends must say UTF-8; TOML is UTF-8 by spec.
        with open(path, "w", encoding="utf-8") as handle:
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
    _update_config(path, send_port=send_port, recv_port=recv_port)


def load_exclusive_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    value = _read_config_dict(path).get("exclusive_channel")
    return value if isinstance(value, int) else None


def load_boards(path: str = DEFAULT_CONFIG_PATH) -> set:
    """Expansion boards declared fitted in config.toml.

    One boolean per board rather than a list: the flat writer here handles
    bools and not sequences, and a user editing the file by hand should not
    have to guess list syntax.
    """
    data = _read_config_dict(path)
    return {name for name, key in (("IB304F", "ib304f_fitted"),
                                   ("EB16", "eb16_fitted"))
            if data.get(key) is True}


def save_boards(boards, path: str = DEFAULT_CONFIG_PATH) -> None:
    fitted = {str(b).upper() for b in boards}
    _update_config(path, ib304f_fitted="IB304F" in fitted,
                   eb16_fitted="EB16" in fitted)


def save_exclusive_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, exclusive_channel=int(channel))


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


#: A write is answered by REPLY and by nothing else, so a data frame arriving
#: where an acknowledgement belongs is somebody else's answer -- see
#: S3kBridge._receive.
_ONLY_REPLY = frozenset({int(m.Command.REPLY)})


def install_clean_exit(signals=(signal.SIGTERM, signal.SIGHUP)) -> None:
    """Turn termination signals into :class:`SystemExit`, so ports close.

    Ctrl-C already unwinds: it raises ``KeyboardInterrupt`` and any
    ``finally`` that closes the bridge runs. **SIGTERM does not** -- the
    default action ends the process where it stands, leaving the MIDI port
    open and, worse, a request outstanding on the wire that the sampler is
    still composing an answer to.

    That is not hypothetical. Running this project under ``timeout`` while
    diagnosing a display problem killed it mid-exchange several times in a
    row, and the machine stopped answering RSTAT on any port until it was
    power cycled -- a wedge with a cause this project had not recorded: the
    *client* dying mid-transfer rather than the machine being over-polled.

    Raising from the handler is safe with respect to frame integrity, and
    that is the point rather than an accident: Python delivers signals
    between bytecodes, so a ``send_message`` already inside the C call
    finishes before the handler runs. The frame on the wire is whole; only
    the conversation is abandoned.

    Idempotent, and it leaves any handler the caller has already installed
    for a signal alone -- a host application that has its own shutdown is
    better at this than we are.
    """
    for number in signals:
        try:
            existing = signal.getsignal(number)
        except (ValueError, OSError):        # not available on this platform
            continue
        if existing not in (signal.SIG_DFL, None):
            continue                          # somebody else owns it
        try:
            signal.signal(
                number,
                lambda signum, _frame: (_ for _ in ()).throw(
                    SystemExit(128 + signum)),
            )
        except (ValueError, OSError):
            # signal() only works on the main thread of the main interpreter
            continue


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
        bounds_check: bool = True,
        boards: Optional[Iterable[str]] = None,
    ):
        self.out = midi_out
        self.inp = midi_in
        self.description = description
        self.exclusive_channel = exclusive_channel
        self.timeout = timeout
        #: Refuse header reads and writes the device would answer with
        #: somebody else's data. See :meth:`_check_bounds`. Set False only to
        #: probe the device's own behaviour, which is the one job the guard
        #: gets in the way of.
        self.bounds_check = bounds_check
        #: Expansion boards declared fitted, upper-cased. Fields and
        #: operations that need one are refused unless it is in here.
        #:
        #: **This is crash prevention, not tidiness.** The panel gates these
        #: pages on a machine without the board -- EFFECTS will not open at
        #: all, and neither will ENV3 -- and an S3000XL was crashed twice in
        #: one session with the same flooding-display signature while this
        #: area was being exercised (§85, §90). The machine is not merely
        #: ignoring absent hardware; something reachable here can take it
        #: down, and nothing in the protocol says which.
        #:
        #: The device cannot be asked: no reply carries a fitted-options
        #: field, and the mode register happily opens a page the panel
        #: refuses (§86). So it has to be declared, and the safe default is
        #: to assume nothing is fitted.
        self.boards = {b.upper() for b in (boards or ())}
        self._counts: Dict[str, Optional[int]] = {"program": None,
                                                  "sample": None}
        self._groups: Dict[int, int] = {}

    # -- bounds -------------------------------------------------------------

    def invalidate_structure(self) -> None:
        """Forget cached program/sample/keygroup counts.

        Called by every operation here that can change them. It cannot know
        about changes made at the front panel, so the cache is a guess about
        a device somebody else may be touching -- which is why being wrong
        about it must be survivable. It is: a stale count produces a refusal
        with a message naming the counts it used, not a silent wrong answer.
        """
        self._counts = {"program": None, "sample": None}
        self._groups = {}

    def _recount(self, region: str, *, timeout: Optional[float] = None) -> int:
        """Drop this region's cached count and read it again."""
        self._counts[region] = None
        return self._count(region, timeout=timeout)

    def _count(self, region: str, *, timeout: Optional[float] = None) -> int:
        if self._counts.get(region) is None:
            if region == "program":
                self._counts[region] = len(self.program_list(timeout=timeout))
            elif region == "sample":
                self._counts[region] = len(self.sample_list(timeout=timeout))
        return self._counts.get(region) or 0

    def _keygroup_count(self, program: int, *,
                        timeout: Optional[float] = None) -> int:
        if program not in self._groups:
            self._groups[program] = int(
                self.get_parameter(p.lookup(("program", "GROUPS")), program,
                                   timeout=timeout, _bounds=False))
        return self._groups[program]

    def _check_bounds(self, region: str, index: int, offset: int, count: int,
                      selector: int, *, timeout: Optional[float] = None) -> None:
        """Refuse what the device would answer with somebody else's data.

        The extended layer does not bounds-check and does not error. An
        out-of-range read returns **the previous valid read's buffer**, which
        is the dangerous failure because it looks entirely plausible --
        measured again 2026-08-13:

            program 42 of 2      -> byte-identical to the primed read
            keygroup 31 of 1     -> byte-identical
            sample 50 of 10      -> byte-identical
            offset 200 of 192    -> real-looking data from past the header

        The block-identifier check further down catches only cross-region
        confusion, and only at offset 0: a bad program index answers with a
        *program* block, so the identifier is correct and the guard cannot
        see it. That was measured too, and is why this exists rather than
        relying on the identifier.

        So the refusal has to happen here, before the frame is sent. The
        size check is free. The index checks cost a round trip once per
        region and are cached.
        """
        size = p.REGION_SIZES.get(region)
        if size is not None and (offset < 0 or count < 0
                                 or offset + count > size):
            raise ValueError(
                f"{region} header is {size} bytes; asked for {count} at "
                f"offset {offset}. The device answers this with data from "
                f"past the header rather than an error (§11).")
        if not self.bounds_check:
            return
        # A refusal is re-checked against a FRESH count before it stands. The
        # cache cannot see the front panel, so a stale entry would otherwise
        # refuse a read that is perfectly valid -- a program or keygroup added
        # at the machine, or by another session, is invisible until something
        # here happens to invalidate. That failure has no recourse but a
        # restart, which makes it worse than the silent stale-buffer read the
        # check exists to prevent.
        #
        # The re-read costs one round trip and only on the refusal path, which
        # is the right way round: refusing is rare, and being wrong about it is
        # expensive.
        if region in ("program", "sample"):
            held = self._count(region, timeout=timeout)
            if not 0 <= index < held:
                held = self._recount(region, timeout=timeout)
            if not 0 <= index < held:
                raise ValueError(
                    f"{region} {index} does not exist; the machine holds "
                    f"{held}. Reading it would return the previous read's "
                    f"buffer, not an error (§11).")
        elif region == "keygroup":
            held = self._count("program", timeout=timeout)
            if not 0 <= index < held:
                held = self._recount("program", timeout=timeout)
            if not 0 <= index < held:
                raise ValueError(
                    f"program {index} does not exist; the machine holds "
                    f"{held} (§11).")
            groups = self._keygroup_count(index, timeout=timeout)
            if not 0 <= selector < groups:
                self._groups.pop(index, None)
                groups = self._keygroup_count(index, timeout=timeout)
            if not 0 <= selector < groups:
                raise ValueError(
                    f"program {index} has {groups} keygroup(s); asked for "
                    f"keygroup {selector}. Reading it would return the "
                    f"previous read's buffer, not an error (§11).")

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
            # Declared boards come from config.toml, so a machine that has
            # the hardware says so once and every later session honours it.
            boards=load_boards(config_path) if config_path else (),
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

    #: Frames skipped by :meth:`_receive` because they answered a question
    #: nobody here asked. Non-zero means a previous session died with a
    #: request outstanding; see :meth:`_receive`.
    stale_replies: int = 0

    def _receive(self, timeout: Optional[float] = None,
                 accept: Optional[frozenset] = None) -> bytes:
        """Wait for the next SysEx frame addressed to us.

        ``accept`` is the set of operation codes that could legitimately
        answer what was just sent. Frames outside it are **skipped, not
        returned** -- because a reply on our channel is not necessarily a
        reply to *us*.

        The case that forced this: a client killed mid-exchange leaves the
        machine's answer still in flight. The next process opens the port,
        drains (catching nothing, because the answer has not arrived yet),
        sends its own request, and is handed the dead session's reply. It
        decodes as the wrong message and the error names the wrong cause --
        observed as "extended data: expected at least a 7-byte body, got 1"
        on the first read of a fresh connection.

        Draining before a send cannot fix that; only checking what came back
        can.
        """
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
                channel, command, _payload = m.parse_frame(data)
            except ValueError:
                continue  # somebody else's SysEx on a shared input
            if channel != self.exclusive_channel:
                continue
            if accept is not None and command not in accept:
                self.stale_replies += 1
                continue
            return data
        raise TimeoutError(f"no reply within {timeout or self.timeout}s")

    @staticmethod
    def _answers_to(frame: bytes) -> frozenset:
        """Which operation codes may answer *frame*.

        The pairing is entirely regular: every response is its request's
        opcode **plus one** -- RSTAT 0x00 / STAT 0x01, RPLIST 0x02 / PLIST
        0x03, RPHEADER 0x27 / PHEADER 0x28, RMISCDATA 0x33 / MISCDATA 0x34,
        RMULTIDATA 0x41 / MULTIDATA 0x42. REPLY is always allowed, since any
        operation can answer with an error instead.
        """
        try:
            _channel, command, _payload = m.parse_frame(frame)
        except ValueError:
            return frozenset({int(m.Command.REPLY)})
        return frozenset({int(m.Command.REPLY), command + 1})

    def send_and_receive(
        self, frame: bytes, *, timeout: Optional[float] = None
    ) -> bytes:
        self._drain()
        self._send(frame)
        return self._receive(timeout, accept=self._answers_to(frame))

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
        names = m.ProgramList.decode(reply).names
        # The bounds guard needs this count; taking it here means ordinary
        # use warms the cache and the guard costs nothing extra.
        self._counts["program"] = len(names)
        return names

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
    #: **The selected VOLUME**, 0-based; the panel shows it 1-based. Watched
    #: while a person stepped the volume: it read 4, 5, 1, 2 against a panel
    #: reading 005, 006, 002, 003, and writing it moves both the panel and
    #: the directory (§96).
    #:
    #: This was `_MISC_SELECTION_HELD` until 2026-08-14, on §70's reading that
    #: it was a hold flag whose clearing forced a re-read. That reading was
    #: built on real behaviour and named it wrongly: writing 0 selects the
    #: FIRST volume, which always exists, and the re-read that follows is the
    #: machine loading that volume's directory. The panel's "INACTIVE" state
    #: §70 describes is a volume index past the end of the partition -- which
    #: is also why a partition write appeared to stop working, and why
    #: writing 0 appeared to fix it.
    _MISC_VOLUME = 4
    #: The name it went by while it was misunderstood. Kept so that a reader
    #: coming from §70 or §72 finds the register rather than a missing symbol.
    _MISC_SELECTION_HELD = _MISC_VOLUME

    #: Bytes 6-9 mirror one another -- writing any one moves all four -- and
    #: they are the LOAD page's "type of load" field. **Writing value n
    #: PERFORMS load type n** (§94): all eight work, not just 1.
    #:
    #: There is no select-without-firing. The panel gets that from its own
    #: `GO` key; this register does not have one, so setting the type IS
    #: triggering the load. Anything writing here must treat the write as the
    #: load itself.
    #:
    #: §74 swept 0 and 2-7, saw no memory move, and recorded them as inert.
    #: That sweep ran against a volume that was already entirely resident, so
    #: every type reloaded what was in memory and netted zero words -- exactly
    #: the useless experiment §73 had thrown out one section earlier. It stood
    #: for two days and cost an unintended load to disprove.
    #:
    #: The value is not persisted: it read 7 before a power cycle and 0 after,
    #: so 0 is the power-on default (§77).
    #:
    #: The load **appends**. §73 loaded a 15.30 MB volume onto 3.70 MB of
    #: resident data and finished with 19.00 MB, the sum to within 630 words.
    #:
    #: **The panel's CLR softkey is not in this register.** CLR erases
    #: waveform memory and then loads, and it is a panel chain with its own
    #: on-screen confirmation (§75). The effect is reachable: see
    #: :meth:`clear_memory`, which deletes what is resident.
    _MISC_LOAD_TYPE = (6, 7, 8, 9)

    #: Miscellaneous-data banks, by selector byte: the spec lists 1 byte,
    #: 2 word, 3 dword, 4 smpte, 5 signed smpte, 6 name, 7 16-byte flag (§5).
    #: Only the byte bank was ever swept until 2026-08-14, which is how the
    #: volume register stayed lost in plain sight (§96) and how the disk
    #: cursor stayed unknown until the sweep was widened (§97).
    _MISC_BANK_BYTE = 1
    _MISC_BANK_WORD = 2

    def _misc_word(self, index: int, value: Optional[int] = None, *,
                   timeout: Optional[float] = None) -> int:
        """Read or write one entry of the miscellaneous WORD bank."""
        if value is None:
            frame = m.HeaderRequest(
                command=m.Command.RMISCDATA, index=index,
                selector=self._MISC_BANK_WORD, offset=0, count=2,
                exclusive_channel=self.exclusive_channel,
            ).encode()
            reply = self.send_and_receive(frame, timeout=timeout)
            _c, command, _p = m.parse_frame(reply)
            if command == m.Command.REPLY:
                self._raise_for_reply(reply, f"reading misc word {index}")
            return int.from_bytes(bytes(m.HeaderData.decode(reply).data)[:2],
                                  "little")
        frame = m.HeaderData(
            command=m.Command.MISCDATA, index=index,
            selector=self._MISC_BANK_WORD, offset=0,
            data=int(value).to_bytes(2, "little"),
            exclusive_channel=self.exclusive_channel,
        ).encode()
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(
            self._receive(timeout, accept=_ONLY_REPLY),
            f"writing misc word {index}")
        return self._misc_word(index, timeout=timeout)

    #: How many entries the selected volume's directory holds, as the machine
    #: counts them. Bounding the walk with this rather than with a guess at
    #: what junk looks like is what stopped `hd_directory` returning one
    #: phantom entry per volume (§99).
    _MISC_DIRECTORY_ENTRIES = 6

    #: Which entry of the loaded volume's directory the panel is highlighting,
    #: 0-based, matching :meth:`hd_directory`'s ``index``. Found by watching
    #: the word bank while a person stepped the panel's cursor down an item
    #: list -- it read 2, 3, 4, 5 for the 3rd item onwards -- and **writing it
    #: moves the highlight on the LCD**, confirmed by eye (§97).
    #:
    #: That is what makes `Cursor Prog+Samps` and `Cursor Item only` usable
    #: remotely. Read-only they would be useless to an editor, which can name
    #: the item it wants and could not select it.
    _MISC_ITEM_CURSOR = 7

    def item_cursor(self, *, timeout: Optional[float] = None) -> int:
        """Which directory entry the panel is highlighting, 0-based."""
        return self._misc_word(self._MISC_ITEM_CURSOR, timeout=timeout)

    def select_item(self, index: int, *,
                    timeout: Optional[float] = None) -> int:
        """Move the panel's highlight to a directory entry. **This writes.**

        Nothing is loaded by this. It is the selection the two cursor load
        types act on -- see :meth:`trigger_load`, which sets it for you.

        Unlike ``byte[49]``, which the panel writes and the machine ignores
        (§70), this one really moves the machine: the LCD's highlight follows
        it. Verified by eye rather than by read-back, because a register that
        reads back what was written proves only that something stored it.
        """
        if index < 0:
            raise ValueError(f"item index {index} is negative")
        return self._misc_word(self._MISC_ITEM_CURSOR, index, timeout=timeout)

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
        self._raise_for_reply(
            self._receive(timeout, accept=_ONLY_REPLY),
            f"writing misc byte {index}")
        return self._misc_byte(index, timeout=timeout)

    #: Main-menu pages, by the value :attr:`_MISC_MODE` takes. All eleven
    #: read off the panel 2026-08-13 (§84); no eyes-free discriminator was
    #: ever found, and RMULTIDATA answering in every mode killed the only
    #: candidate (§78).
    #:
    #: **EDIT is a modifier lamp, not a page**, which is what makes the
    #: document's "eleven modes available from the eight mode keys" add up:
    #: the eight buttons are SINGLE, MULTI, SAMPLE, EFFECTS, EDIT, GLOBAL,
    #: SAVE and LOAD -- seven modes plus a modifier that combines with four
    #: of them, so 7 + 4 = 11. The enumeration is base/edit pairs in order,
    #: then the three disk-and-system pages.
    MODES = {
        0: "SINGLE",       1: "SINGLE EDIT",
        2: "MULTI",        3: "MULTI EDIT",
        4: "SAMPLE",       5: "SAMPLE EDIT",
        6: "EFFECTS",      7: "EFFECTS EDIT",
        8: "GLOBAL",
        9: "SAVE",
        10: "LOAD",
    }

    #: The highest page value the machine survives. The S2000/S3000XL
    #: document says "eleven modes available from the eight mode keys" --
    #: SINGLE, MULTI, SAMPLE and EFFECTS, an EDIT variant of each, plus LOAD,
    #: SAVE and GLOBAL -- and the register takes 0-10, which is exactly
    #: eleven.
    #:
    #: **11 is not refused. It CRASHES the machine.** The write gets no
    #: reply at all, and the LCD floods with a repeating pattern and keeps
    #: flooding -- a runaway loop, not a page that failed to draw (§85). It
    #: needs a power cycle, and the front panel dies with it. So the range
    #: check below is not defensive tidiness standing in for a device that
    #: would have said no; it is the only thing between a caller's typo and a
    #: reboot.
    #:
    #: Whether 11 is past the end or a page for an expansion board this
    #: machine does not have is **open** -- a page crashing on init against
    #: absent hardware looks identical from here (§85). Either way the guard
    #: stands: a page that only exists with a board fitted is not reachable
    #: on a machine without one.
    _MAX_MODE = 10

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
        if not 0 <= mode <= self._MAX_MODE:
            raise ValueError(
                f"mode {mode} is outside 0-{self._MAX_MODE}; the machine does "
                f"not refuse an out-of-range page, it stops answering and "
                f"needs a power cycle (§79)"
            )
        try:
            self._misc_byte(self._MISC_MODE, mode, timeout=timeout)
        except DeviceError:
            pass          # the write may well have taken; the read decides
        return self._misc_byte(self._MISC_MODE, timeout=timeout)

    #: The LOAD page's "type of load" list. See :data:`s3k.messages.LOAD_TYPES`.
    LOAD_TYPES = m.LOAD_TYPES

    def load_type(self, *, timeout: Optional[float] = None) -> int:
        """Which kind of load the panel's LOAD page is set to.

        The panel writes its selection into the trigger register, so this
        reads what is on screen (§93). :attr:`LOAD_TYPES` names the values.

        Reading is free of side effects. Setting is not offered: writing 1
        fires a load, and whether writing another value moves the panel's
        displayed selection or merely stores a number the machine ignores is
        untested -- the same trap as ``byte[49]``, which the panel writes and
        the machine never reads back (§70).
        """
        return self._misc_byte(self._MISC_LOAD_TYPE[0], timeout=timeout)

    def load_type_name(self, *, timeout: Optional[float] = None) -> str:
        """:meth:`load_type`, rendered. Unknown values say so rather than lie."""
        value = self.load_type(timeout=timeout)
        name = self.LOAD_TYPES.get(value)
        return f"{value} ({name})" if name else f"{value} (unnamed)"

    #: Load types this method refuses without ``force``. 6 loads an operating
    #: system off the disc over the running one; it is a legitimate operation
    #: and not one to reach by a keypress or a typo.
    _GUARDED_LOAD_TYPES = frozenset({6})

    #: The load types that act on the panel's highlighted directory entry
    #: rather than on the whole volume (§93). :meth:`trigger_load` will place
    #: that highlight for you.
    CURSOR_LOAD_TYPES = frozenset({4, 5})

    def trigger_load(self, load_type: int = 1, *, item: Optional[int] = None,
                     force: bool = False,
                     timeout: Optional[float] = None) -> None:
        """Load the selected volume. **This writes and it loads.**

        ``load_type`` is one of :data:`s3k.messages.LOAD_TYPES`, and the
        machine performs it: 0 `ENTIRE VOLUME`, 1 `ALL PROGS+SAMPLES`,
        2 `programs only`, 3 `all samples`, and so on (§94).

        ``item`` aims the two cursor types at a specific directory entry,
        0-based, matching :meth:`hd_directory`. It is written to the machine's
        own highlight first (§97); passing it with a type that ignores the
        cursor is refused rather than silently dropped.

        **The write IS the load.** There is no way to select a type and fire
        it separately -- that is what the panel's `GO` key does and this
        register has no equivalent. So this cannot be used to set up a load
        for later, and any code that writes bytes 6-9 for any reason has
        started one.

        The load **appends** to what is already in memory, for the types that
        add. That is not the safe-and-boring option it sounds like: a bank
        built from several volumes needs the SUM to fit, so three 12 MB
        volumes each fit a 32 MB machine and the third load is the one that
        fails. And the failure is quiet -- the machine says "insufficient
        waveform memory" once and then carries on, leaving programs whose
        samples never arrived resident, selectable, and silent. Check the
        directory's ``audio_words`` against ``status().free_words`` first.

        The panel's CLR softkey is not reachable through this register (§75);
        :meth:`clear_memory` is the remote stand-in for its effect.

        **Do not poll the machine while it loads.** A 58.7 MB load probed
        with ``RSTAT`` every 8 seconds ran in stop-start bursts and finally
        sat at "BUSY" until it was power cycled; the same trigger on a quiet
        bus finished in seconds. Whether the probing caused it or merely
        coincided with it is NOT established -- but the two have different
        consequences and neither is worth assuming, so this fires and returns
        and leaves the machine alone. Read it again when the display settles
        (§71, §72).

        The machine stops acknowledging while it works, so a write that takes
        long enough will raise a timeout from the transport rather than
        return. That is the load running, not a failure; this sends without
        waiting for the reply for exactly that reason.
        """
        if load_type not in m.LOAD_TYPES:
            raise ValueError(
                f"load type {load_type} is not one of "
                f"{sorted(m.LOAD_TYPES)}; the register performs what it is "
                f"given, so an unknown value is an unknown operation (§94)"
            )
        if item is not None:
            if load_type not in self.CURSOR_LOAD_TYPES:
                raise ValueError(
                    f"load type {load_type} ({m.LOAD_TYPES[load_type]}) does "
                    f"not act on the cursor, so item={item} would be ignored"
                )
            self.select_item(item, timeout=timeout)
        if load_type in self._GUARDED_LOAD_TYPES and not force:
            raise ValueError(
                f"load type {load_type} ({m.LOAD_TYPES[load_type]}) is "
                f"guarded; pass force=True to mean it"
            )
        self.invalidate_structure()      # a load replaces the whole bank
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
        the panel's "HARD-:C". **The volume is not in here** -- there is no
        volume register (§72), and ``cursor_value`` is not one: it holds
        whatever field the panel's cursor sits on (§70). ``device_type`` selects
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
            # 0-based, as the register holds it. The panel displays it 1-based
            # and so does anything user-facing; see select_volume (§96).
            "volume": self._misc_byte(self._MISC_VOLUME, timeout=timeout),
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

        **This lands on the first volume**, because the write that makes the
        machine act is a write to the volume register (§96). Follow with
        :meth:`select_volume` to go somewhere else; the returned
        ``load_source`` says where you ended up.

        The volume must be written FIRST, and the reason is the one §70
        described without knowing what it was describing: an index past the
        end of the old partition leaves the panel showing "INACTIVE", and
        while it is there the machine accepts a partition write and does not
        re-read, so the directory silently keeps describing the previous
        partition. That cost a confusing half hour on hardware -- partition
        switching worked, then stopped -- and writing volume 0, which always
        exists, is what fixes it.
        """
        self._force_reread(timeout=timeout)
        self._misc_write_verify(self._MISC_PARTITION, partition,
                                "selecting partition", timeout=timeout)
        return self.load_source(timeout=timeout)

    def _misc_write_verify(self, index: int, value: int, what: str, *,
                           timeout: Optional[float] = None) -> int:
        """Write a miscellaneous byte and believe the READ, not the reply.

        Three of these registers now answer with error code 1 and perform the
        write regardless -- ``byte[2]``, ``byte[4]`` and the mode -- and
        ``byte[4]`` additionally answers OK and ignores the write in one
        state. There is no state in which the acknowledgement is a reliable
        account of what happened, so this stops asking it: write, read back,
        and raise only if the register did not end up holding the value.

        That is not a workaround for an unreliable machine. The machine is
        consistent; the acknowledgement simply does not mean what the frame
        layout implies, and an editor that raises on it aborts work that has
        already succeeded. A bus map died that way.
        """
        try:
            self._misc_byte(index, value, timeout=timeout)
        except DeviceError:
            pass
        got = self._misc_byte(index, timeout=timeout)
        if got != value:
            raise DeviceError(
                f"{what}: asked for {value}, register reads {got}")
        return got

    def select_volume(self, volume: int, *,
                      timeout: Optional[float] = None) -> Dict[str, int]:
        """Choose which volume the LOAD page is pointing at. **This writes.**

        ``volume`` is 0-based, matching :meth:`volume_list`'s ``index``; the
        panel displays it one higher, so volume 0 shows as `001`.

        Nothing is loaded by this -- it moves the selection, and the machine
        re-reads that volume's directory, which :meth:`hd_directory` then
        returns. Loading is :meth:`trigger_load`.

        **§72 said this was impossible** and this project told its users so
        for six days. It was looking for a register it had already found and
        misnamed (§96). Selecting a volume past the end of the partition
        leaves the panel showing "INACTIVE", so the value is checked against
        the volume list rather than trusted.
        """
        available = len(self.volume_list(timeout=timeout))
        if available and not 0 <= volume < available:
            raise ValueError(
                f"volume {volume} is outside 0-{available - 1} on this "
                f"partition; the machine would show INACTIVE rather than "
                f"refuse"
            )
        self._misc_write_verify(self._MISC_VOLUME, volume,
                                "selecting volume", timeout=timeout)
        return self.load_source(timeout=timeout)

    def _force_reread(self, *, timeout: Optional[float] = None) -> None:
        """Make the machine act on a selection it has only recorded.

        **This selects the first volume as it goes**, because the way it
        makes the machine act is by writing the volume register (§96). That
        is not a side effect to be tidied away: after a drive, device or
        partition change the previous volume index means nothing anyway, and
        the panel lands somewhere too. Callers that want a specific volume
        should follow with :meth:`select_volume`, which is what the returned
        ``load_source`` reports so they can see where they ended up.


        Writing the SCSI ID or the device type stores the choice and does
        **not** send the machine to look at it: sweeping the ID and reading
        the volume list after each write returned the same list every time,
        and reading after a following write returned each drive's real list
        one step behind. Off by exactly one, which is the signature of a read
        taken before the machine had gone anywhere.

        A write to ``byte[4]`` or to the partition both trigger it, equally
        and immediately.

        **It answers with error code 1 and performs the write anyway**, in
        every state tested, on both the SINGLE and LOAD pages. That is the
        third register on this machine whose acknowledgement is wrong in one
        direction or the other, so the error is swallowed and the caller is
        expected to check ``load_source`` rather than trust the ack. Not
        swallowing it is what made a whole bus map read the wrong discs and
        then abort: the exception fired on a device that was working
        perfectly.
        """
        try:
            self._misc_byte(self._MISC_SELECTION_HELD, 0, timeout=timeout)
        except DeviceError:
            pass

    #: What ``byte[0]`` means: the panel's device selector, in its own order,
    #: reading **FLOPPY | HARDDISK | FLASH (when available)**.
    #:
    #: HARD is confirmed by eye (the machine read 1 sitting on it) and so is
    #: FLASH (writing 2 put the panel there). FLOPPY is 0, supported by two
    #: independent readings rather than by elimination: 0 is the power-on
    #: default (§77), and moving to the SAVE page while byte[0] held 0 made
    #: the machine fail on absent media, which is what a save page opening on
    #: an empty floppy drive would do (§78).
    #:
    #: **The register taking a value is not evidence the device is there**,
    #: and a caller that needs to know must look at what comes back.
    #:
    #: Selecting FLASH and selecting FLOPPY return byte-identical volume
    #: listings -- the same 100 entries, `BOOT SYSTEM#` then `AUTOLOAD 01`
    #: onward. This was first written up as "that is what an absent device
    #: looks like", which was wrong: the machine has 8 MB of flash fitted.
    #: The identical listings are **unexplained**. Candidates not
    #: distinguished: a listing that is not per-device at all, a re-read that
    #: did not fire, or a genuine shared boot-volume namespace. Do not build
    #: on it either way.
    DEVICE_TYPES = {0: "FLOPPY", 1: "HARD", 2: "FLASH"}

    def select_device(self, kind: int, *,
                      timeout: Optional[float] = None) -> Dict[str, int]:
        """Choose floppy / hard / flash. **This writes.**

        Changing the device changes what the directory describes, and if
        nothing is mounted on the new device the directory reads empty. That
        is a correct answer and not a failure -- it cost a wrong conclusion
        once, when an empty listing was read as a broken partition write
        rather than as a switch to a device with no media (§70).
        """
        self._misc_write_verify(self._MISC_DEVICE_TYPE, kind,
                                "selecting device", timeout=timeout)
        self._force_reread(timeout=timeout)
        return self.load_source(timeout=timeout)

    def select_drive(self, scsi_id: int, *,
                     timeout: Optional[float] = None) -> Dict[str, int]:
        """Point the LOAD page at another SCSI device. **This writes.**

        Takes effect immediately -- no reboot. §71 concluded the opposite,
        that the ID bound at boot, because changing it never altered the
        volume list; that was measured on a bus carrying a single disc, where
        "switched to an empty ID" and "did not switch" produce the same empty
        listing. With five discs at IDs 0-4 the same write walks through five
        different volume lists (§72).

        The machine's own ID is a different register (``byte[12]``, read as
        ``scsi_local_id``) and is not written here -- changing what the
        sampler answers to, over the bus it is answering on, is not something
        this offers.
        """
        self._misc_write_verify(self._MISC_SCSI_DRIVE_ID, scsi_id,
                                "selecting SCSI drive", timeout=timeout)
        self._force_reread(timeout=timeout)
        return self.load_source(timeout=timeout)

    def hd_directory(self, kind: int = 1, *, limit: int = 512,
                     timeout: Optional[float] = None) -> List["_DirectoryEntry"]:
        """RHDDIR -> HDDIR. The directory of the volume the machine has LOADED.

        ``kind`` is the spec's selector -- 0 volume data, 1 program, 2 sample,
        3 cue list, 4 take list, 5 effects file, 6 drum file -- and behaves as
        a starting point rather than a filter: selector 1 returns the programs
        and then continues through the samples.

        **This is not a browser for the disk.** It reads the directory of
        whichever volume the LOAD page has selected -- which is why
        :meth:`select_drive`, :meth:`select_device` and :meth:`select_partition`
        make a whole disk enumerable without touching the machine, and why the
        volume, having no register, does not.

        The spec says *"There are no functions within MIDI system exclusive to
        provide direct access to and from disk files."* That is true as
        written and was read too broadly here for a while: there is no
        file-transfer operation, but the LOAD page's own controls are
        miscellaneous-data registers like any other, so the selection and the
        trigger are both writable. See :meth:`trigger_load`.

        Records are 24 bytes: a 12-character name, a four-byte extension
        field that reads as spaces on every real entry, and eight more whose
        meaning is not documented and are returned raw rather than guessed at.

        **The length comes from the machine, not from inspecting the data.**
        `word[6]` holds the entry count for the selected volume. Everything
        below about extension fields is a fallback and a floor.

        The history is worth keeping because the fallback was wrong in a way
        that survived for weeks. Past the last entry this layer keeps
        answering: first with records whose extension is not spaces, then by
        repeating entry 0 over and over -- the same out-of-range behaviour
        §11 Finding A found in the header reads. A stop condition of "all
        bytes zero" never fires, and the first version of this returned 188
        entries for a 64-entry directory, two thirds of them junk that looked
        like names.

        Tightening it to "extension must be blank" cut that to exactly **one**
        phantom per volume, which is far harder to notice than 124 of them:
        the record immediately past the end usually has a blank extension, so
        it sailed through. Measured across 100 volumes, 98 carried the
        phantom and it was the final entry every time (§99).

        A record that repeats one already seen ends the list too. The echo is
        not always of entry 0 -- on the disk here entry 63 came back identical
        to entry 13, including the eight bytes that look like a location, so
        two real files cannot account for it.

        One request per entry, deliberately. Asking for a larger ``count``
        does return more bytes, but they are NOT the following entries: with
        ``count`` 48 the second record is the first SAMPLE rather than the
        second program. It looks like paging and produces a different list.
        """
        # The machine knows how long its own directory is, and asking it is
        # not a heuristic. `word[6]` is the entry count for the selected
        # volume; bounding the walk with it removes the phantom entry the
        # stop conditions below let through (§99).
        #
        # The heuristics stay as a floor, not as the authority: they still
        # catch a short directory, and they are all there is if the register
        # cannot be read.
        try:
            counted = self._misc_word(self._MISC_DIRECTORY_ENTRIES,
                                      timeout=timeout)
        except Exception:
            counted = None
        if counted is not None and 0 <= counted < limit:
            limit = counted

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
        names = m.SampleList.decode(reply).names
        self._counts["sample"] = len(names)
        return names

    # -- byte-addressable header access -------------------------------------

    # -- effects and reverb -------------------------------------------------
    #
    # `RFXDATA`/`FXDATA` have opcodes and a five-value selector in this
    # project and no field table anywhere, because no source describes the
    # structure: the Akai scan documents only the MULTI header's fx1..fx4
    # pointers and its `fxfilename`, which reference an effects file rather
    # than describing one. What follows was measured (§88).
    #
    # **All of it works on a machine with no EB16 board fitted.** The panel
    # refuses to open the EFFECTS page there; the data underneath is live and
    # complete (§86). So an editor can author effects for a program destined
    # for a machine that has the board, which is a librarian's job.

    #: One effect or reverb preset. Found by reading index 0 with a long count
    #: and locating where index 1's known name appears -- "where does the next
    #: one start" rather than "where does this one end", because the extended
    #: layer answers past-the-end reads with buffer contents instead of an
    #: error (§11) and so can never tell you the latter.
    FX_ENTRY_SIZE = 128

    #: The effects file's own name, 12 characters at offset 3 -- the same
    #: place the multi header carries its name. Reads ``EFFECTS FILE``.
    FX_NAME_OFFSET = 3

    def fx_bytes(self, selector: "m.FxSelector", index: int = 0,
                 offset: int = 0, count: int = 12, *,
                 timeout: Optional[float] = None) -> bytes:
        """Read *count* bytes from one effects structure. ``RFXDATA``.

        ``selector`` picks which structure (:class:`s3k.messages.FxSelector`)
        and ``index`` which entry within it, for the two selectors that are
        lists. `FX_HEADER` and `FX_ASSIGN` ignore the index -- every value
        returns the same record.

        No bounds check is possible here: there is no documented count of
        entries and no field table, so nothing local knows what is in range.
        Reading past the end returns the previous read's buffer rather than an
        error, exactly as it does for the header regions (§11), which is why
        :meth:`fx_names` stops on a repeat instead of on a failure.
        """
        if "EB16" not in self.boards:
            raise BoardNotFitted(
                "the effects structure needs the EB16 board, which is not "
                "declared fitted. The data is readable and writable without "
                "it (§88) and the panel refuses the page entirely (§86), so "
                "it is fenced by default -- declare boards=['EB16'] to author "
                "effects for a machine that has one.")
        frame = m.HeaderRequest(
            command=m.Command.RFXDATA, index=index, selector=int(selector),
            offset=offset, count=count,
            exclusive_channel=self.exclusive_channel,
        ).encode()
        reply = self.send_and_receive(frame, timeout=timeout)
        _channel, command, _payload = m.parse_frame(reply)
        if command == m.Command.REPLY:
            self._raise_for_reply(reply, f"reading fx {selector!r} {index}")
        if command != m.Command.FXDATA:
            raise DeviceError(
                f"expected FXDATA reading fx {selector!r}, got {command:#04x}")
        data = m.HeaderData.decode(reply).data
        if len(data) != count:
            raise DeviceError(
                f"asked for {count} bytes at offset {offset}, got {len(data)}")
        return bytes(data)

    def set_fx_bytes(self, selector: "m.FxSelector", data: bytes,
                     index: int = 0, offset: int = 0, *,
                     confirm: bool = True,
                     timeout: Optional[float] = None) -> None:
        """Write bytes into one effects structure. ``FXDATA``. **This writes.**

        Same shape as :meth:`set_header_bytes`, and the same warning applies
        with more force: nothing here knows the structure, so an offset is
        only as good as the caller's evidence for it. Read the bytes first.
        """
        if "EB16" not in self.boards:
            raise BoardNotFitted(
                "the effects structure needs the EB16 board, which is not "
                "declared fitted. The data is readable and writable without "
                "it (§88) and the panel refuses the page entirely (§86), so "
                "it is fenced by default -- declare boards=['EB16'] to author "
                "effects for a machine that has one.")
        frame = m.HeaderData(
            command=m.Command.FXDATA, index=index, selector=int(selector),
            offset=offset, data=bytes(data),
            exclusive_channel=self.exclusive_channel,
        ).encode()
        if not confirm:
            self._send(frame, write=True)
            return
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(self._receive(timeout),
                              f"writing fx {selector!r} {index}")

    def fx_names(self, selector: "m.FxSelector", *, limit: int = 128,
                 timeout: Optional[float] = None) -> List[str]:
        """Enumerate a preset list by name. ``FX_ENTRY`` or ``RVB_ENTRY``.

        **The end of the list is not discoverable from the device, and this
        does not pretend otherwise.** There is no count anywhere: the header
        carries none, the entries have no validity marker -- bytes 12-15 read
        `0,0,0,0` for real entries and for garbage alike -- and past the last
        one the machine answers with buffer contents rather than an error
        (§11). Stopping on a repeat does not work either, because the garbage
        keeps changing and so never repeats.

        So this stops at the first name containing a character the device's
        own charset cannot represent, which `decode_name` renders as ``?``.
        That is a **heuristic with a known failure**: on the machine measured
        it ends the reverb list correctly at 51 entries, and the very next
        record decodes as ``001TL1`` -- valid characters, no meaning. A
        caller that knows the count should pass ``limit`` and ignore the rule.

        Counts measured on one S3000XL, offered as data and not as a rule:
        **51 reverb presets, and the effect list ends the same way.** Another
        machine or firmware may differ, and nothing here can check.
        """
        names: List[str] = []
        for index in range(limit):
            raw = self.fx_bytes(selector, index, 0, m.NAME_LENGTH,
                                timeout=timeout)
            name = m.decode_name(list(raw))
            if "?" in name:
                break                 # past the end; see the docstring
            names.append(name)
        return names

    def get_header_bytes(
        self,
        region: str,
        index: int,
        offset: int,
        count: int,
        *,
        selector: int = 0,
        timeout: Optional[float] = None,
        _bounds: bool = True,
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
        if _bounds:
            self._check_bounds(region, index, offset, count, selector,
                               timeout=timeout)
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
        _bounds: bool = True,
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
        if _bounds:
            self._check_bounds(region, index, offset, len(data), selector,
                               timeout=timeout)
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
            self._receive(timeout, accept=_ONLY_REPLY),
            f"writing {region} {index} at offset {offset}"
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

    def _require_board(self, param, what: str) -> None:
        """Refuse a field whose board has not been declared fitted."""
        need = getattr(param, "requires", "")
        if need and need not in self.boards:
            raise BoardNotFitted(
                f"{param.name} needs the {need} expansion board, which is not "
                f"declared fitted. The panel gates these pages on a machine "
                f"without it and this area has crashed an S3000XL (§85, §90), "
                f"so {what} is refused. If the board IS fitted, say so: "
                f"S3kBridge(..., boards=['{need}']), or set it in config.toml.")

    def get_parameter(
        self,
        param,
        index: int,
        *,
        keygroup: int = 0,
        region: Optional[str] = None,
        timeout: Optional[float] = None,
        _bounds: bool = True,
    ):
        """Read one named parameter, decoded per its table entry.

        Refuses fields belonging to an undeclared expansion board; see
        :attr:`boards`.

        ``_bounds`` is private and exists for one caller: reading ``GROUPS``
        to find out how many keygroups a program has cannot itself require
        knowing how many keygroups a program has.
        """
        param = param if isinstance(param, p.Parameter) else p.lookup(param, region)
        self._require_board(param, "reading it")
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
        self._require_board(param, "writing it")
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
        self.invalidate_structure()
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
        self.invalidate_structure()
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
        self.invalidate_structure()
        self._destructive(
            m.DeleteSample(
                sample=sample, exclusive_channel=self.exclusive_channel
            ).encode(),
            f"deleting sample {sample}",
            confirm,
        )

    def clear_memory(self, *, timeout: Optional[float] = None) -> Dict[str, int]:
        """Delete everything resident. **DESTRUCTIVE, and there is no undo.**

        This is the remote half of the panel's CLR. CLR itself is not
        reachable: the manual describes it as F7-CLR raising an on-screen
        prompt, F8-YES answering it and F8-GO then loading, and no value in
        the trigger register reaches any of that (§74). What IS reachable is
        the effect, by deleting what is resident.

        Deleting a sample really does return its waveform memory -- deleting a
        312,257-word sample moved the free figure by 312,272. It is worth
        stating because the first measurement said otherwise: the sample
        deleted was a few-thousand-word calibration tone and the figure did
        not visibly move, which reads exactly like a machine that unlists
        without reclaiming.

        **The last program cannot be deleted.** The delete is acknowledged OK
        and the list stays at one. So this stops when a delete stops making
        progress rather than counting to a guess, and reports what is left.

        Programs are deleted after samples, because a program costs about a
        hundred words and the point of the exercise is the megabytes.
        """
        self.invalidate_structure()
        result = {"samples": 0, "programs": 0}
        for kind, listing, delete in (
            ("samples", self.sample_list, self.delete_sample),
            ("programs", self.program_list, self.delete_program),
        ):
            while True:
                names = listing(timeout=timeout)
                if not names:
                    break
                delete(0)
                result[kind] += 1
                if len(listing(timeout=timeout)) >= len(names):
                    # acknowledged and ignored -- the last program does this.
                    # Stopping here rather than spinning on a fixed guard.
                    break
        result["samples_left"] = len(self.sample_list(timeout=timeout))
        result["programs_left"] = len(self.program_list(timeout=timeout))
        return result

    #: Program header offset of ``PRGNUM``, the MIDI program-change number.
    #: Kept here rather than looked up in :mod:`s3k.params` so the renumber
    #: does not depend on the parameter table being loaded.
    _PRGNUM_OFFSET = 15

    #: The highest value the field takes. **The byte is 0-based and the panel
    #: shows it 1-based** -- measured 2026-08-14 by reading all fifteen
    #: resident programs straight after the panel's SEQU had numbered them
    #: 1…15 on screen: the bytes read 0…14 (§91). So there are 128 numbers,
    #: 0-127, and a machine holding more programs than that cannot give them
    #: all distinct ones.
    #:
    #: This was written as `index + 1` first, from the panel photograph alone.
    #: mpc2emu's S3000 writer had already inferred 0-based from the bytes in
    #: authored volumes, and the two could not both be right; the read above
    #: is what settled it, and it settled it against this project.
    _PRGNUM_MAX = 127

    def renumber_programs(self, *, timeout: Optional[float] = None) -> Dict[str, int]:
        """Give every resident program a distinct MIDI program number.

        This is the remote equivalent of the panel's `RNUM` → `SEQU`, and it
        exists because loading several volumes without clearing leaves
        programs sharing a number. `PRGNUM` is stored *in* the program and
        reloaded verbatim, so volumes authored independently all start at 1;
        four programs numbered 1 was observed after four loads. Programs
        sharing a number **stack** -- one program change fires all of them
        (§91).

        Numbers are assigned in `RPLIST` order, so programs that were already
        resident keep the front of the range and newly loaded ones follow.
        That is deliberately the whole list rather than "the ones that just
        arrived": it needs no assumption about where a load puts new programs
        in the list, which is not established.

        The value written is the list position itself, because the field is
        0-based; the panel adds one for display, so program 0 here is the one
        its screen calls 1. See :attr:`_PRGNUM_MAX`.

        **This is not destructive** -- `PRGNUM` is one byte and writing it
        again undoes it -- but it is not cosmetic either. Anything sending
        program changes at this machine will address different programs
        afterwards.

        **No BTSORT is needed for this particular write, measured.** The
        specification says BTSORT "should be triggered" after writing
        `PRGNUM`, to resort the list and reflag the active programs, and s3ked
        cannot trigger it because the Data Index is missing from the
        transcription (§5). §92 tested what the machine does by itself: it
        **reflags on its own** -- the panel's "now active" count follows a
        SysEx write immediately -- and it does **not** re-sort. The sort is
        the half that is missing, and it is the half this method does not
        need: numbers are assigned in list order, so the list is already in
        program-number order when it finishes.

        A caller writing `PRGNUM` out of list order does not get that for
        free, and has to live with an unsorted list until the panel is
        touched.

        Returns what it did: how many programs were renumbered, and how many
        were past :attr:`_PRGNUM_MAX` and had to be left alone.
        """
        names = self.program_list(timeout=timeout)
        result = {"renumbered": 0, "beyond_range": 0, "programs": len(names)}
        for index in range(len(names)):
            if index > self._PRGNUM_MAX:
                result["beyond_range"] += 1
                continue
            self.set_header_bytes(
                "program", index, self._PRGNUM_OFFSET, bytes([index]),
                timeout=timeout,
            )
            result["renumbered"] += 1
        return result

    def program_numbers(self, *, timeout: Optional[float] = None) -> List[int]:
        """The MIDI program number each resident program carries.

        One read per program, so this is not something to put on a refresh
        path -- it is for the audit, which is already walking every program.
        Index in the returned list is the `RPLIST` position, which is what
        addresses a program; the *value* is the number that plays it, and the
        two are unrelated (§91).
        """
        count = len(self.program_list(timeout=timeout))
        return [
            self.get_header_bytes(
                "program", index, self._PRGNUM_OFFSET, 1, timeout=timeout,
            )[0]
            for index in range(count)
        ]

    def _destructive(self, frame: bytes, what: str, confirm: bool) -> None:
        if not confirm:
            self._send(frame, write=True)
            return
        self._drain()
        self._send(frame, write=True)
        self._raise_for_reply(self._receive(accept=_ONLY_REPLY), what)

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
