# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Wire codec: framing, nibbling, the character set, and every message class."""

import pytest

from s3k import messages as m


# --- primitives -------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 0x7F, 0x80, 0x3FFF])
def test_u14_round_trip(value):
    assert m.decode_u14(*m.encode_u14(value)) == value


def test_u14_is_lsb_first():
    # The spec states the convention only in passing, and getting it backwards
    # would put every offset and count in the wrong place.
    assert m.encode_u14(0x0081) == (0x01, 0x01)


def test_u14_rejects_out_of_range():
    with pytest.raises(ValueError):
        m.encode_u14(0x4000)


@pytest.mark.parametrize("data", [b"", b"\x00", b"\xff", bytes(range(256))])
def test_nibbles_round_trip(data):
    assert m.decode_nibbles(m.encode_nibbles(data)) == data


def test_nibbles_are_low_then_high():
    assert m.encode_nibbles(b"\xAB") == bytes([0x0B, 0x0A])


def test_nibbles_stay_7_bit():
    # Every emitted byte must be legal inside a SysEx frame.
    assert all(b <= 0x7F for b in m.encode_nibbles(bytes(range(256))))


def test_decode_nibbles_rejects_odd_length():
    with pytest.raises(ValueError):
        m.decode_nibbles(b"\x01\x02\x03")


@pytest.mark.parametrize("value,count", [(0, 1), (127, 1), (128, 2), (8388608, 4)])
def test_lsb_bytes_round_trip(value, count):
    assert m.decode_lsb_bytes(m.encode_lsb_bytes(value, count)) == value


# --- the Akai character set -------------------------------------------------


def test_charset_has_41_entries_in_spec_order():
    assert len(m.AKAI_CHARSET) == 41
    assert m.AKAI_CHARSET[0] == "0"
    assert m.AKAI_CHARSET[9] == "9"
    assert m.AKAI_CHARSET[10] == " "
    assert m.AKAI_CHARSET[11] == "A"
    assert m.AKAI_CHARSET[36] == "Z"
    assert m.AKAI_CHARSET[37:] == "#+-."


@pytest.mark.parametrize("name", ["", "A", "BASS 1", "DRUMS.A", "PAD-2", "X+Y#Z"])
def test_name_round_trip(name):
    assert m.decode_name(m.encode_name(name)) == name


def test_name_is_not_ascii():
    # The single most surprising property of this protocol: a name byte is an
    # index into a 41-entry table, so "A" is 11 and not 0x41.
    assert m.encode_name("A")[0] == 11


def test_name_is_padded_to_twelve():
    assert len(m.encode_name("BASS")) == m.NAME_LENGTH


def test_name_lowercase_is_folded():
    assert m.encode_name("bass") == m.encode_name("BASS")


def test_name_rejects_unrepresentable_characters():
    # Refused rather than substituted: a silently mangled name cannot be told
    # from a correct one after the fact.
    with pytest.raises(ValueError, match="character"):
        m.encode_name("BASS!")


def test_name_rejects_overlong():
    with pytest.raises(ValueError):
        m.encode_name("THIRTEEN CHRS")


def test_decode_name_tolerates_bad_bytes():
    # Read paths must not take a whole catalog down over one bad byte.
    assert m.decode_name([11, 99, 12]) == "A?B"


# --- envelope ---------------------------------------------------------------


def test_frame_shape():
    frame = m.build_frame(m.Command.RSTAT, exclusive_channel=3)
    assert frame == bytes([0xF0, 0x47, 0x03, 0x00, 0x48, 0xF7])


def test_frame_round_trip():
    frame = m.build_frame(m.Command.REPLY, [0], exclusive_channel=9)
    assert m.parse_frame(frame) == (9, m.Command.REPLY, b"\x00")


def test_build_frame_rejects_non_7bit_payload():
    with pytest.raises(ValueError):
        m.build_frame(m.Command.REPLY, [0x80])


def test_build_frame_rejects_bad_channel():
    with pytest.raises(ValueError):
        m.build_frame(m.Command.RSTAT, exclusive_channel=128)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xf0\xf7",
        bytes([0xF0, 0x18, 0x00, 0x00, 0x48, 0xF7]),  # E-mu, not Akai
        bytes([0xF0, 0x47, 0x00, 0x00, 0x49, 0xF7]),  # wrong model id
        bytes([0xF0, 0x47, 0x00, 0x00, 0x48, 0x00]),  # no EOX
    ],
)
def test_parse_frame_rejects_foreign_data(data):
    with pytest.raises(ValueError):
        m.parse_frame(data)


# --- S1000-layer messages ---------------------------------------------------


def test_request_status_round_trip():
    msg = m.RequestStatus(exclusive_channel=5)
    assert m.RequestStatus.decode(msg.encode()) == msg


def test_no_field_message_rejects_payload():
    with pytest.raises(ValueError):
        m.RequestStatus.decode(m.build_frame(m.Command.RSTAT, [0]))


def test_no_field_message_rejects_wrong_command():
    with pytest.raises(ValueError):
        m.RequestStatus.decode(m.build_frame(m.Command.RPLIST))


def test_status_round_trip_and_version_format():
    msg = m.Status(
        version_major=2,
        version_minor=0,
        max_blocks=1022,
        free_blocks=900,
        max_words=8388608,
        free_words=1234567,
        exclusive_channel_setting=7,
        exclusive_channel=7,
    )
    back = m.Status.decode(msg.encode())
    assert back == msg
    assert back.version == "2.00"


def test_status_payload_length_is_fixed():
    msg = m.Status(2, 0, 1, 1, 1, 1, 0)
    assert len(msg.encode()) == 6 + m.Status.PAYLOAD_LENGTH


def test_status_rejects_short_payload():
    with pytest.raises(ValueError):
        m.Status.decode(m.build_frame(m.Command.STAT, [0, 0]))


@pytest.mark.parametrize("cls,command", [
    (m.ProgramList, m.Command.PLIST),
    (m.SampleList, m.Command.SLIST),
])
def test_name_list_round_trip(cls, command):
    msg = cls(names=["BASS 1", "PAD-2", "DRUMS.A"], exclusive_channel=1)
    assert cls.decode(msg.encode()).names == msg.names


def test_name_list_empty():
    assert m.ProgramList.decode(m.ProgramList(names=[]).encode()).names == []


def test_name_list_detects_count_mismatch():
    # A truncated list must fail loudly; silently short catalogs are how a UI
    # ends up quietly hiding half a machine's contents.
    payload = list(m.encode_u14(2)) + m.encode_name("ONLY ONE")
    with pytest.raises(ValueError, match="count says"):
        m.ProgramList.decode(m.build_frame(m.Command.PLIST, payload))


@pytest.mark.parametrize("msg", [
    m.DeleteProgram(program=5),
    m.DeleteKeygroup(program=5, keygroup=3),
    m.DeleteSample(sample=200),
    m.SetExclusiveChannel(new_channel=4),
    m.Reply(code=0),
])
def test_field_message_round_trip(msg):
    assert type(msg).decode(msg.encode()) == msg


def test_reply_ok_flag():
    assert m.Reply(code=m.ReplyCode.OK).ok
    assert not m.Reply(code=m.ReplyCode.ERROR).ok


def test_field_message_rejects_wrong_length():
    with pytest.raises(ValueError):
        m.DeleteProgram.decode(m.build_frame(m.Command.DELP, [1]))


# --- destructive registry ---------------------------------------------------


def test_deletes_are_destructive():
    for command in (m.Command.DELP, m.Command.DELK, m.Command.DELS):
        assert m.is_destructive(command)


def test_whole_header_writes_are_destructive():
    # Not obviously so: the spec says writing a program whose name matches an
    # existing one deletes that program first.
    assert m.is_destructive(m.Command.PDATA)
    assert m.Command.PDATA in m.DESTRUCTIVE_ON_WRITE


def test_reads_are_not_destructive():
    for command in (m.Command.RSTAT, m.Command.RPLIST, m.Command.RPHEADER):
        assert not m.is_destructive(command)


# --- the extended (12-byte header) family -----------------------------------


def test_header_request_is_thirteen_bytes():
    # The spec is explicit: a request is a 12-byte header plus EOX.
    frame = m.HeaderRequest(command=m.Command.RPHEADER, index=1, count=2).encode()
    assert len(frame) == 13


def test_header_request_round_trip():
    msg = m.HeaderRequest(
        command=m.Command.RKHEADER,
        index=7,
        selector=3,
        offset=64,
        count=12,
        exclusive_channel=2,
    )
    assert m.HeaderRequest.decode(msg.encode()) == msg


def test_header_request_rejects_non_extended_command():
    with pytest.raises(ValueError, match="not an extended-header command"):
        m.HeaderRequest(command=m.Command.RSTAT)


def test_header_data_round_trip():
    msg = m.HeaderData(
        command=m.Command.PHEADER, index=3, offset=16, data=bytes(range(20))
    )
    back = m.HeaderData.decode(msg.encode())
    assert back.data == msg.data
    assert back.offset == msg.offset
    assert back.index == msg.index


def test_header_data_count_follows_payload():
    # Derived, not stored, so the header can never disagree with the data.
    assert m.HeaderData(command=m.Command.PHEADER, data=b"abc").count == 3


def test_header_data_rejects_count_mismatch():
    good = m.HeaderData(command=m.Command.PHEADER, data=b"ab").encode()
    tampered = bytearray(good)
    tampered[10] = 9  # count low byte
    with pytest.raises(ValueError, match="header says"):
        m.HeaderData.decode(bytes(tampered))


def test_postpone_bits_ride_in_the_item_index():
    msg = m.HeaderData(
        command=m.Command.PHEADER,
        index=5,
        data=b"\x01",
        postpone=m.Postpone.SCREEN | m.Postpone.RECALC,
    )
    back = m.HeaderData.decode(msg.encode())
    assert back.index == 5
    assert back.postpone == m.Postpone.SCREEN | m.Postpone.RECALC


def test_postpone_defaults_clear():
    # The polarity matters: clear means the device repaints itself, which is
    # the behaviour this project relies on instead of eosed's redraw hack.
    assert m.HeaderData(command=m.Command.PHEADER).postpone == m.Postpone.NONE


def test_item_index_cannot_collide_with_postpone_bits():
    with pytest.raises(ValueError, match="12 bits"):
        m.HeaderData(command=m.Command.PHEADER, index=0x1000, data=b"\x00").encode()


def test_every_extended_request_has_a_reply_opcode():
    for request, reply in m.EXTENDED_REPLY_FOR.items():
        assert request in m.EXTENDED_COMMANDS
        assert reply in m.EXTENDED_COMMANDS
        assert reply == request + 1
