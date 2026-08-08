# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The frame layout, operation codes, nibbling rule, name character set and
# message field orders in this file are transcribed as data from Akai's own
# protocol specifications ("S1000 MIDI Exclusive Communication" and
# "S2800/S3000/S3200 MIDI System Exclusive Extensions"); see LICENSE and
# docs/RESOLUTION_NOTES.md §1. The structure of this module -- the
# _FieldMessage/_NoFieldMessage mixins and the destructive-command registry --
# follows the sibling eosed project's eos/messages.py, GPL-2.0-or-later.
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

"""Wire codec for the Akai S1000/S3000-family MIDI System Exclusive protocol.

Every message on this wire has the shape::

    F0 47 cc <op> 48 <body...> F7
     |  |  |   |    |
     |  |  |   |    `-- model identity, 0x48, shared by the whole family
     |  |  |   `------- operation code
     |  |  `----------- MIDI exclusive channel, 0-127
     |  `-------------- Akai manufacturer code
     `----------------- SysEx status

Two body shapes exist, and which one applies is decided by the operation code:

* The **S1000 shape** (ops 0x00-0x16, 0x1D) has a per-op body: a program
  number here, a name list there, nothing at all for the requests.
* The **S3000 extended shape** (ops 0x27-0x38) has one *uniform* 7-byte body
  -- item index, selector, byte offset, byte count -- making the whole header
  12 bytes. This is the byte-addressable accessor that makes a parameter
  editor possible at all, and it is why :class:`HeaderRequest` /
  :class:`HeaderData` are two generic classes rather than eighteen
  near-identical ones: the specification defines them as one shape.

Two conventions apply throughout and are easy to get wrong:

* **Multi-byte numbers are LSB-first 7-bit chunks.** The specification says
  so once, in passing: "Unless stated, groups of bytes in messages represent
  concatenated 7-bit sections of a data word, LSB first."
* **Data bytes are nibbled.** Each byte of payload data travels as *two*
  message bytes, low nibble first, each carrying four bits in its bottom
  nibble. This applies to the data portion only -- never to the header.

And one that is easy to miss entirely: **names are not ASCII.** See
:data:`AKAI_CHARSET`.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass
from typing import ClassVar, Dict, Iterable, List, Sequence, Tuple

__all__ = [
    "SOX",
    "EOX",
    "MANUFACTURER_ID",
    "MODEL_ID",
    "DEFAULT_EXCLUSIVE_CHANNEL",
    "NAME_LENGTH",
    "AKAI_CHARSET",
    "Command",
    "Postpone",
    "FxSelector",
    "ReplyCode",
    "DESTRUCTIVE_COMMANDS",
    "DESTRUCTIVE_ON_WRITE",
    "is_destructive",
    "encode_nibbles",
    "decode_nibbles",
    "encode_u14",
    "decode_u14",
    "encode_lsb_bytes",
    "decode_lsb_bytes",
    "encode_name",
    "decode_name",
    "build_frame",
    "parse_frame",
    "RequestStatus",
    "Status",
    "RequestProgramList",
    "ProgramList",
    "RequestSampleList",
    "SampleList",
    "DeleteProgram",
    "DeleteKeygroup",
    "DeleteSample",
    "SetExclusiveChannel",
    "Reply",
    "HeaderRequest",
    "HeaderData",
]

# --- envelope constants ----------------------------------------------------

SOX = 0xF0
EOX = 0xF7
MANUFACTURER_ID = 0x47  # Akai
MODEL_ID = 0x48  # "S1000 model identity", shared by the whole S1000/S3000 line

# The exclusive channel is this protocol's device-id analogue: 0-127, settable
# on the machine and over the wire with SETEX. Note there is NO broadcast
# value -- unlike MIDI's universal device inquiry (0x7F) or EOS's broadcast
# device id, nothing here means "whoever is listening". Autodetect therefore
# has to sweep channels rather than ask once; see s3k.bridge.
DEFAULT_EXCLUSIVE_CHANNEL = 0x00

#: Every name field in this protocol is exactly this many characters.
NAME_LENGTH = 12

#: The device's name character set. **Names are not ASCII.**
#:
#: The specification spells this out only once, buried under the block-structure
#: listings: "names (always 12 characters) in the S1000, including PLIST and
#: SLIST are not in ASCII form". A name byte is an index into this table, and
#: "Byte in name fields must be limited to this range" -- so 41 values, and
#: anything outside them is not merely unusual, it is illegal.
#:
#: Index:  0-9 = "0".."9", 10 = " ", 11-36 = "A".."Z", 37-40 = "#+-."
AKAI_CHARSET = "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ#+-."

_CHAR_TO_BYTE: Dict[str, int] = {c: i for i, c in enumerate(AKAI_CHARSET)}


class Command(enum.IntEnum):
    """Operation codes, in the three layers the documentation stacks them in.

    Mnemonics for the S1000 layer are the specification's own. The S3000 and
    S3000XL layers are given only as prose descriptions in the source
    documents ("Request for Program Header bytes"), so those mnemonics are
    ours, chosen to match the S1000 naming style.
    """

    # -- S1000 base layer ---------------------------------------------------
    RSTAT = 0x00  # request status
    STAT = 0x01  # status report
    RPLIST = 0x02  # request list of resident program names
    PLIST = 0x03  # list of resident program names
    RSLIST = 0x04  # request list of resident sample names
    SLIST = 0x05  # list of resident sample names
    RPDATA = 0x06  # request program common data
    PDATA = 0x07  # program common data (bidirectional)
    RKDATA = 0x08  # request keygroup data
    KDATA = 0x09  # keygroup data (bidirectional)
    RSDATA = 0x0A  # request sample header data
    SDATA = 0x0B  # sample header data (bidirectional)
    RSPACK = 0x0C  # request sample data packet(s)
    ASPACK = 0x0D  # accept sample data packet(s)
    RDDATA = 0x0E  # request drum settings
    DDATA = 0x0F  # drum input settings (bidirectional)
    RMDATA = 0x10  # request miscellaneous data
    MDATA = 0x11  # miscellaneous data (bidirectional)
    DELP = 0x12  # delete program and its keygroups
    DELK = 0x13  # delete keygroup
    DELS = 0x14  # delete sample header and data
    SETEX = 0x15  # set exclusive channel
    REPLY = 0x16  # command reply (ok / error)
    CASPACK = 0x1D  # corrected ASPACK

    # -- S3000 extended layer: byte-addressable structure access ------------
    RPHEADER = 0x27  # request program header bytes
    PHEADER = 0x28  # program header bytes
    RKHEADER = 0x29  # request keygroup header bytes
    KHEADER = 0x2A  # keygroup header bytes
    RSHEADER = 0x2B  # request sample header bytes
    SHEADER = 0x2C  # sample header bytes
    RFXDATA = 0x2D  # request FX/reverb bytes
    FXDATA = 0x2E  # FX/reverb bytes
    RCUEDATA = 0x2F  # request cue-list bytes
    CUEDATA = 0x30  # cue-list bytes
    RTAKEDATA = 0x31  # request take-list bytes
    TAKEDATA = 0x32  # take-list bytes
    RMISCDATA = 0x33  # request miscellaneous bytes
    MISCDATA = 0x34  # miscellaneous bytes
    RVOLLIST = 0x35  # request volume-list item
    VOLLIST = 0x36  # volume-list item (reply only)
    RHDDIR = 0x37  # request harddisk directory entry
    HDDIR = 0x38  # harddisk directory entry (reply only)

    # -- S2000/S3000XL/S3200XL layer ----------------------------------------
    RMULTIDATA = 0x41  # request multi data
    MULTIDATA = 0x42  # multi data


#: Operation codes that use the uniform 12-byte extended header.
#:
#: The S2000/S3000XL/S3200XL multi opcodes are in here too: they are numbered
#: apart from the S3000 block but carry exactly the same header --
#: ``mm,mm`` multi part number as the item index, ``ss`` selector (0 = multi
#: file header, 1 = multi part), byte offset, byte count -- so the same two
#: message classes serve them.
EXTENDED_COMMANDS = frozenset(
    [c for c in Command if Command.RPHEADER <= c <= Command.HDDIR]
    + [Command.RMULTIDATA, Command.MULTIDATA]
)

#: For each extended *request*, the operation code the device answers with.
EXTENDED_REPLY_FOR: Dict[Command, Command] = {
    Command.RPHEADER: Command.PHEADER,
    Command.RKHEADER: Command.KHEADER,
    Command.RSHEADER: Command.SHEADER,
    Command.RFXDATA: Command.FXDATA,
    Command.RCUEDATA: Command.CUEDATA,
    Command.RTAKEDATA: Command.TAKEDATA,
    Command.RMISCDATA: Command.MISCDATA,
    Command.RVOLLIST: Command.VOLLIST,
    Command.RHDDIR: Command.HDDIR,
    Command.RMULTIDATA: Command.MULTIDATA,
}


class Postpone(enum.IntFlag):
    """Deferral bits that ride in the top of the extended header's item index.

    The item index is a 14-bit field but only its low 12 bits carry the index;
    the specification gives bits 12 and 13 a side meaning for write
    operations.

    Note the polarity: these are opt-*out* flags. Left clear -- the default --
    the machine refreshes its own screen and recalculates after a write. That
    is the opposite of the sibling eosed project's situation, where EOS offers
    no redraw at all and a Program Change has to be forged to force one. Do
    not port that workaround here.

    ``RECALC`` carries a real hazard, in the specification's own words: "the
    machine may be in an undetermined state until the same parameter is sent
    with this bit cleared". Never leave it set at the end of an operation.
    """

    NONE = 0
    RECALC = 1 << 12  # bit 12: postpone recalculation
    SCREEN = 1 << 13  # bit 13: postpone screen update


#: Mask for the item index proper, once the :class:`Postpone` bits are removed.
ITEM_INDEX_MASK = 0x0FFF


class FxSelector(enum.IntEnum):
    """Selector byte values for :data:`Command.RFXDATA` / ``FXDATA``."""

    FX_HEADER = 0
    FX_ASSIGN = 1
    FX_ENTRY = 2
    RVB_ASSIGN = 3
    RVB_ENTRY = 4


class ReplyCode(enum.IntEnum):
    """Payload of a :data:`Command.REPLY` message."""

    OK = 0
    ERROR = 1


# Deletes. One-shot, no device-side confirmation, no undo. Never key-bind
# these in a UI; only reachable through an explicit arm-then-fire flow (see
# DISCLAIMER.md and CLAUDE.md's hardware rules).
DESTRUCTIVE_COMMANDS = frozenset(
    {Command.DELP, Command.DELK, Command.DELS}
)

# Whole-structure writes, which are destructive for a *non-obvious* reason and
# so are tracked separately rather than lumped in above. From the spec, on
# PDATA: "If the program name in data is the same as that of any existing
# program, that program will be deleted first." So a whole-header write can
# destroy a program the caller never named. Treat these as destructive until
# someone proves otherwise against hardware.
DESTRUCTIVE_ON_WRITE = frozenset(
    {Command.PDATA, Command.KDATA, Command.SDATA, Command.DDATA, Command.MDATA}
)


def is_destructive(command: int) -> bool:
    """True if *command* can lose data, whether or not the caller meant it to."""
    return command in DESTRUCTIVE_COMMANDS or command in DESTRUCTIVE_ON_WRITE


# --- primitive codecs ------------------------------------------------------


def encode_nibbles(data: Iterable[int]) -> bytes:
    """Split each byte into two message bytes, low nibble first."""
    out = bytearray()
    for byte in data:
        if not 0 <= byte <= 0xFF:
            raise ValueError(f"byte {byte} out of range")
        out.append(byte & 0x0F)
        out.append((byte >> 4) & 0x0F)
    return bytes(out)


def decode_nibbles(data: Sequence[int]) -> bytes:
    """Reassemble bytes from low/high nibble pairs."""
    if len(data) % 2:
        raise ValueError(f"nibbled data has odd length {len(data)}")
    return bytes(
        (data[i] & 0x0F) | ((data[i + 1] & 0x0F) << 4) for i in range(0, len(data), 2)
    )


def encode_u14(value: int) -> Tuple[int, int]:
    """Encode a 14-bit unsigned value as (low 7 bits, high 7 bits)."""
    if not 0 <= value <= 0x3FFF:
        raise ValueError(f"value {value} out of 14-bit range")
    return value & 0x7F, (value >> 7) & 0x7F


def decode_u14(low: int, high: int) -> int:
    """Decode a 14-bit unsigned value from its two 7-bit chunks."""
    return (low & 0x7F) | ((high & 0x7F) << 7)


def encode_lsb_bytes(value: int, count: int) -> List[int]:
    """Encode *value* as *count* 7-bit chunks, least significant first."""
    if value < 0:
        raise ValueError(f"value {value} is negative")
    if value >= 1 << (7 * count):
        raise ValueError(f"value {value} does not fit in {count} 7-bit chunks")
    return [(value >> (7 * i)) & 0x7F for i in range(count)]


def decode_lsb_bytes(data: Sequence[int]) -> int:
    """Decode 7-bit chunks, least significant first, into an integer."""
    return sum((byte & 0x7F) << (7 * i) for i, byte in enumerate(data))


def encode_name(name: str, length: int = NAME_LENGTH) -> List[int]:
    """Encode *name* into the device's own character set, space-padded.

    Raises :class:`ValueError` on any character the device cannot store --
    deliberately, rather than substituting. A silently mangled name is worse
    than a refused one, because the device offers no way to tell the two apart
    after the fact.
    """
    padded = name.upper().ljust(length)
    if len(padded) > length:
        raise ValueError(f"name {name!r} exceeds {length} characters")
    try:
        return [_CHAR_TO_BYTE[c] for c in padded]
    except KeyError as exc:
        bad = exc.args[0]
        raise ValueError(
            f"character {bad!r} in name {name!r} is not in the device's "
            f"character set ({AKAI_CHARSET!r})"
        ) from None


def decode_name(data: Sequence[int]) -> str:
    """Decode a name from the device's character set, trailing spaces stripped.

    Out-of-range bytes decode to ``?`` rather than raising: a name field is
    read far more often than it is written, and one bad byte in a catalog of
    hundreds should not take the whole listing down.
    """
    out = []
    for byte in data:
        out.append(AKAI_CHARSET[byte] if 0 <= byte < len(AKAI_CHARSET) else "?")
    return "".join(out).rstrip()


# --- envelope --------------------------------------------------------------


def build_frame(
    command: int,
    payload: Iterable[int] = (),
    *,
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL,
) -> bytes:
    """Wrap *payload* in the Akai SysEx envelope.

    Every outbound message funnels through here, so the envelope is defined
    exactly once. Note the field order differs from the sibling eosed
    project's E-mu frame: Akai puts the channel *before* the operation code
    and the model identity *after* it.
    """
    if not 0 <= exclusive_channel <= 0x7F:
        raise ValueError(f"exclusive channel {exclusive_channel} out of range")
    body = list(payload)
    for byte in body:
        if not 0 <= byte <= 0x7F:
            raise ValueError(f"payload byte {byte} is not a 7-bit value")
    return bytes(
        [SOX, MANUFACTURER_ID, exclusive_channel, int(command), MODEL_ID, *body, EOX]
    )


def parse_frame(data: Sequence[int]) -> Tuple[int, int, bytes]:
    """Unwrap a frame into ``(exclusive_channel, command, payload)``.

    Raises :class:`ValueError` if this is not an Akai S1000-family frame --
    which includes the case of somebody else's SysEx arriving on a shared
    input, so callers sweeping a busy wire should expect it.
    """
    if len(data) < 6 or data[0] != SOX or data[-1] != EOX:
        raise ValueError("not a SysEx frame")
    if data[1] != MANUFACTURER_ID:
        raise ValueError(f"not an Akai frame (manufacturer {data[1]:#04x})")
    if data[4] != MODEL_ID:
        raise ValueError(f"not an S1000-family frame (model {data[4]:#04x})")
    return data[2], data[3], bytes(data[5:-1])


# --- field-message helpers -------------------------------------------------
# Most S1000-layer messages are a short fixed sequence of unsigned fields (a
# raw byte, or a 14-bit LSB-first pair). Rather than hand-write encode/decode
# for each -- and risk a copy-paste slip in one of them -- the field layout is
# declared once per class as FIELDS and a shared base does the packing.


def _encode_fields(
    fields: Sequence[Tuple[str, int]], values: Dict[str, int]
) -> List[int]:
    out: List[int] = []
    for name, width in fields:
        value = values[name]
        if width == 1:
            if not 0 <= value <= 0x7F:
                raise ValueError(f"{name}={value} is not a 7-bit value")
            out.append(value)
        elif width == 2:
            out.extend(encode_u14(value))
        else:  # pragma: no cover - guards a typo in a FIELDS declaration
            raise ValueError(f"unsupported field width {width} for {name}")
    return out


def _decode_fields(
    fields: Sequence[Tuple[str, int]], payload: Sequence[int]
) -> Dict[str, int]:
    expected = sum(width for _, width in fields)
    if len(payload) != expected:
        raise ValueError(f"expected {expected} payload bytes, got {len(payload)}")
    values: Dict[str, int] = {}
    pos = 0
    for name, width in fields:
        if width == 1:
            values[name] = payload[pos]
        else:
            values[name] = decode_u14(payload[pos], payload[pos + 1])
        pos += width
    return values


# These two are plain mixins, deliberately NOT decorated with @dataclass:
# dataclass field inheritance keeps an inherited field's ORIGINAL position
# even when a subclass re-declares it, so a base-class `exclusive_channel`
# field (which needs a default) would force every subclass field to also have
# a default (TypeError: "non-default argument follows default argument").
# Each concrete subclass below declares its own `exclusive_channel` field
# instead, always last, and gets encode/decode for free from the mixin.
class _FieldMessage:
    """Mixin for fixed-field messages."""

    COMMAND: ClassVar[int] = 0
    FIELDS: ClassVar[Sequence[Tuple[str, int]]] = ()

    def encode(self) -> bytes:
        values = {name: getattr(self, name) for name, _ in self.FIELDS}
        return build_frame(
            self.COMMAND,
            _encode_fields(self.FIELDS, values),
            exclusive_channel=self.exclusive_channel,  # type: ignore[attr-defined]
        )

    @classmethod
    def decode(cls, data: Sequence[int]):
        channel, command, payload = parse_frame(data)
        if command != cls.COMMAND:
            raise ValueError(
                f"{cls.__name__}: expected command {cls.COMMAND:#04x}, "
                f"got {command:#04x}"
            )
        return cls(exclusive_channel=channel, **_decode_fields(cls.FIELDS, payload))


class _NoFieldMessage:
    """Mixin for messages with no payload at all."""

    COMMAND: ClassVar[int] = 0

    def encode(self) -> bytes:
        return build_frame(
            self.COMMAND,
            exclusive_channel=self.exclusive_channel,  # type: ignore[attr-defined]
        )

    @classmethod
    def decode(cls, data: Sequence[int]):
        channel, command, payload = parse_frame(data)
        if command != cls.COMMAND:
            raise ValueError(
                f"{cls.__name__}: expected command {cls.COMMAND:#04x}, "
                f"got {command:#04x}"
            )
        if payload:
            raise ValueError(f"{cls.__name__}: expected no payload, got {len(payload)}")
        return cls(exclusive_channel=channel)


# --- S1000-layer messages --------------------------------------------------


@dataclass
class RequestStatus(_NoFieldMessage):
    """RSTAT. Harmless and read-only -- this is the autodetect probe."""

    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.RSTAT


@dataclass
class Status:
    """STAT -- the device's reply to RSTAT.

    A far richer handshake than a MIDI device inquiry: it identifies the
    machine, reports both memory pools, and tells us the exclusive channel it
    is actually listening on (which need not be the one we guessed).
    """

    version_major: int
    version_minor: int
    max_blocks: int
    free_blocks: int
    max_words: int
    free_words: int
    exclusive_channel_setting: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND: ClassVar[int] = Command.STAT

    #: 2 version + 2 max blocks + 2 free blocks + 4 max words + 4 free words + 1 channel
    PAYLOAD_LENGTH: ClassVar[int] = 15

    @property
    def version(self) -> str:
        """Software version as the front panel reports it, ``VV.vv``."""
        return f"{self.version_major}.{self.version_minor:02d}"

    def encode(self) -> bytes:
        payload = [
            # The spec writes this pair "vv,VV ... version VV.vv" -- minor
            # first, consistent with LSB-first everywhere else.
            self.version_minor & 0x7F,
            self.version_major & 0x7F,
            *encode_u14(self.max_blocks),
            *encode_u14(self.free_blocks),
            *encode_lsb_bytes(self.max_words, 4),
            *encode_lsb_bytes(self.free_words, 4),
            self.exclusive_channel_setting & 0x7F,
        ]
        return build_frame(
            self.COMMAND, payload, exclusive_channel=self.exclusive_channel
        )

    @classmethod
    def decode(cls, data: Sequence[int]) -> "Status":
        channel, command, payload = parse_frame(data)
        if command != cls.COMMAND:
            raise ValueError(f"expected STAT, got command {command:#04x}")
        if len(payload) != cls.PAYLOAD_LENGTH:
            raise ValueError(
                f"STAT: expected {cls.PAYLOAD_LENGTH} payload bytes, "
                f"got {len(payload)}"
            )
        return cls(
            version_minor=payload[0],
            version_major=payload[1],
            max_blocks=decode_u14(payload[2], payload[3]),
            free_blocks=decode_u14(payload[4], payload[5]),
            max_words=decode_lsb_bytes(payload[6:10]),
            free_words=decode_lsb_bytes(payload[10:14]),
            exclusive_channel_setting=payload[14],
            exclusive_channel=channel,
        )


@dataclass
class RequestProgramList(_NoFieldMessage):
    """RPLIST."""

    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.RPLIST


@dataclass
class RequestSampleList(_NoFieldMessage):
    """RSLIST."""

    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.RSLIST


class _NameList:
    """Shared codec for PLIST/SLIST: a count, then that many 12-byte names.

    The names are *not* nibbled -- one message byte per character -- and they
    are in the device's own character set, not ASCII. The index of a name in
    this list is the number the rest of the protocol wants: "The machine holds
    sequential numbers, starting at zero for items in this list and these
    numbers should be used to identify a specific program."
    """

    COMMAND: ClassVar[int] = 0

    def encode(self) -> bytes:
        payload: List[int] = list(encode_u14(len(self.names)))  # type: ignore[attr-defined]
        for name in self.names:  # type: ignore[attr-defined]
            payload.extend(encode_name(name))
        return build_frame(
            self.COMMAND,
            payload,
            exclusive_channel=self.exclusive_channel,  # type: ignore[attr-defined]
        )

    @classmethod
    def decode(cls, data: Sequence[int]):
        channel, command, payload = parse_frame(data)
        if command != cls.COMMAND:
            raise ValueError(
                f"{cls.__name__}: expected command {cls.COMMAND:#04x}, "
                f"got {command:#04x}"
            )
        if len(payload) < 2:
            raise ValueError(f"{cls.__name__}: truncated payload")
        count = decode_u14(payload[0], payload[1])
        body = payload[2:]
        expected = count * NAME_LENGTH
        if len(body) != expected:
            raise ValueError(
                f"{cls.__name__}: count says {count} names ({expected} bytes), "
                f"payload carries {len(body)}"
            )
        names = [
            decode_name(body[i : i + NAME_LENGTH])
            for i in range(0, expected, NAME_LENGTH)
        ]
        return cls(names=names, exclusive_channel=channel)


@dataclass
class ProgramList(_NameList):
    """PLIST -- resident program names, in device order."""

    names: List[str] = dataclasses.field(default_factory=list)
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.PLIST


@dataclass
class SampleList(_NameList):
    """SLIST -- resident sample names, in device order."""

    names: List[str] = dataclasses.field(default_factory=list)
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.SLIST


@dataclass
class DeleteProgram(_FieldMessage):
    """DELP. DESTRUCTIVE, one-shot, no device-side confirmation."""

    program: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.DELP
    FIELDS = (("program", 2),)


@dataclass
class DeleteKeygroup(_FieldMessage):
    """DELK. DESTRUCTIVE, one-shot, no device-side confirmation."""

    program: int
    keygroup: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.DELK
    FIELDS = (("program", 2), ("keygroup", 1))


@dataclass
class DeleteSample(_FieldMessage):
    """DELS. DESTRUCTIVE, one-shot, no device-side confirmation."""

    sample: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.DELS
    FIELDS = (("sample", 2),)


@dataclass
class SetExclusiveChannel(_FieldMessage):
    """SETEX -- move the device to another exclusive channel.

    Not destructive, but it does change the address every later message must
    use, so a caller that sends this and forgets has lost the device until it
    sweeps again.
    """

    new_channel: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.SETEX
    FIELDS = (("new_channel", 1),)


@dataclass
class Reply(_FieldMessage):
    """REPLY -- the device's ok/error acknowledgement.

    Worth leaning on: it makes a write verifiable without reading the value
    back, which the sibling eosed project cannot do for most of its writes.
    """

    code: int
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL
    COMMAND = Command.REPLY
    FIELDS = (("code", 1),)

    @property
    def ok(self) -> bool:
        return self.code == ReplyCode.OK


# --- S3000 extended layer --------------------------------------------------
# One uniform shape covers ops 0x27-0x38, so these are two generic classes
# carrying their command rather than eighteen near-identical subclasses.


@dataclass
class HeaderRequest:
    """A request in the extended (12-byte header) family.

    ``index`` selects the item -- program number, sample number, effect
    number -- and ``selector`` carries the second-level choice the operation
    needs (keygroup number for RKHEADER, a :class:`FxSelector` for RFXDATA,
    the miscellaneous-data type for RMISCDATA). Operations that need no
    second level document the byte as reserved, and it is sent as zero.
    """

    command: int
    index: int = 0
    selector: int = 0
    offset: int = 0
    count: int = 0
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL

    def __post_init__(self) -> None:
        if self.command not in EXTENDED_COMMANDS:
            raise ValueError(
                f"{self.command:#04x} is not an extended-header command"
            )

    def encode(self) -> bytes:
        return build_frame(
            self.command,
            _encode_extended_header(
                self.index, self.selector, self.offset, self.count
            ),
            exclusive_channel=self.exclusive_channel,
        )

    @classmethod
    def decode(cls, data: Sequence[int]) -> "HeaderRequest":
        channel, command, payload = parse_frame(data)
        if len(payload) != 7:
            raise ValueError(
                f"extended request: expected a 7-byte body, got {len(payload)}"
            )
        index, selector, offset, count = _decode_extended_header(payload)
        return cls(
            command=command,
            index=index,
            selector=selector,
            offset=offset,
            count=count,
            exclusive_channel=channel,
        )


@dataclass
class HeaderData:
    """A data message in the extended (12-byte header) family.

    Used in both directions: the device sends one in reply to a
    :class:`HeaderRequest`, and we send one to write. ``data`` is plain
    bytes -- nibbling happens on the wire, not here.

    On a write, ``postpone`` rides in the top two bits of the item index. See
    :class:`Postpone` for why leaving it clear is usually right.
    """

    command: int
    index: int = 0
    selector: int = 0
    offset: int = 0
    data: bytes = b""
    postpone: Postpone = Postpone.NONE
    exclusive_channel: int = DEFAULT_EXCLUSIVE_CHANNEL

    def __post_init__(self) -> None:
        if self.command not in EXTENDED_COMMANDS:
            raise ValueError(
                f"{self.command:#04x} is not an extended-header command"
            )

    @property
    def count(self) -> int:
        """The byte count the header advertises -- always the real length.

        Derived rather than stored so the header can never disagree with the
        payload it describes.
        """
        return len(self.data)

    def encode(self) -> bytes:
        if not 0 <= self.index <= ITEM_INDEX_MASK:
            raise ValueError(
                f"item index {self.index} does not fit in 12 bits "
                f"(bits 12-13 carry the postpone flags)"
            )
        return build_frame(
            self.command,
            [
                *_encode_extended_header(
                    self.index | int(self.postpone),
                    self.selector,
                    self.offset,
                    self.count,
                ),
                *encode_nibbles(self.data),
            ],
            exclusive_channel=self.exclusive_channel,
        )

    @classmethod
    def decode(cls, data: Sequence[int]) -> "HeaderData":
        channel, command, payload = parse_frame(data)
        if len(payload) < 7:
            raise ValueError(
                f"extended data: expected at least a 7-byte body, got {len(payload)}"
            )
        raw_index, selector, offset, count = _decode_extended_header(payload[:7])
        body = decode_nibbles(payload[7:])
        # Trust the payload over the advertised count, but refuse to guess
        # when they disagree -- a short read here would silently write the
        # wrong bytes into a parameter table.
        if count != len(body):
            raise ValueError(
                f"extended data: header says {count} bytes, payload carries "
                f"{len(body)}"
            )
        return cls(
            command=command,
            index=raw_index & ITEM_INDEX_MASK,
            selector=selector,
            offset=offset,
            data=body,
            postpone=Postpone(raw_index & ~ITEM_INDEX_MASK),
            exclusive_channel=channel,
        )


def _encode_extended_header(
    index: int, selector: int, offset: int, count: int
) -> List[int]:
    if not 0 <= selector <= 0x7F:
        raise ValueError(f"selector {selector} is not a 7-bit value")
    return [
        *encode_u14(index),
        selector,
        *encode_u14(offset),
        *encode_u14(count),
    ]


def _decode_extended_header(payload: Sequence[int]) -> Tuple[int, int, int, int]:
    return (
        decode_u14(payload[0], payload[1]),
        payload[2],
        decode_u14(payload[3], payload[4]),
        decode_u14(payload[5], payload[6]),
    )
