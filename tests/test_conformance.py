# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the read-only conformance sweep.

The sweep is designed to be left running unattended against a real sampler,
so the property that matters most is not that it finds things -- it is that
it *cannot write*. The opcode allowlist is the whole basis for running it
without supervision, and an allowlist nobody tests is a comment.

Everything else here is arithmetic that would otherwise only be exercised on
hardware: two's complement re-reading, the S1000-layer frame layout, and the
range check actually firing on a value the machine should never report.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes"))

import conformance as cf                                   # noqa: E402

from s3k import messages as m                              # noqa: E402
from s3k import params as p                                # noqa: E402
from s3k.bridge import DeviceError                         # noqa: E402

C = m.Command


class _Recorder:
    """Stands in for the bridge's output port."""

    def __init__(self):
        self.frames = []

    def send_message(self, message, **kwargs):
        self.frames.append(bytes(message))


# --- the safety property ---------------------------------------------------


@pytest.mark.parametrize(
    "command",
    sorted(
        set(m.DESTRUCTIVE_COMMANDS)
        | set(m.DESTRUCTIVE_ON_WRITE)
        | {C.SETEX, C.PHEADER, C.KHEADER, C.SHEADER, C.MULTIDATA},
        key=int,
    ),
)
def test_the_guard_refuses_every_write_and_delete(command):
    inner = _Recorder()
    guard = cf._ReadOnlyOut(inner)
    frame = m.build_frame(command, [0, 0], exclusive_channel=0)

    with pytest.raises(cf.Forbidden):
        guard.send_message(list(frame))

    assert inner.frames == [], "a forbidden frame reached the port"
    assert guard.sent == 0


def test_no_destructive_command_is_on_the_allowlist():
    for command in cf.READ_ONLY_OPS:
        assert not m.is_destructive(command), f"{C(command).name} can lose data"


def test_the_guard_passes_reads_through():
    inner = _Recorder()
    guard = cf._ReadOnlyOut(inner)
    frame = m.RequestStatus(exclusive_channel=0).encode()

    guard.send_message(list(frame))

    assert inner.frames == [bytes(frame)]
    assert guard.sent == 1


def test_the_guard_refuses_a_frame_flagged_as_a_write():
    """`write=True` selects the slower inter-message gap -- reads never set it."""
    guard = cf._ReadOnlyOut(_Recorder())
    frame = m.RequestStatus(exclusive_channel=0).encode()

    with pytest.raises(cf.Forbidden):
        guard.send_message(list(frame), write=True)


def test_the_guard_refuses_something_that_is_not_sysex():
    guard = cf._ReadOnlyOut(_Recorder())
    with pytest.raises(cf.Forbidden):
        guard.send_message([0x90, 0x40, 0x7F])


def test_the_allowlist_is_requests_only():
    """Every allowed opcode reads; none of them is a data-carrying reply."""
    replies = set(m.EXTENDED_REPLY_FOR.values()) | {
        C.STAT, C.PLIST, C.SLIST, C.PDATA, C.KDATA, C.SDATA, C.REPLY,
    }
    assert not (cf.READ_ONLY_OPS & replies)


# --- arithmetic that hardware would otherwise be the first to exercise ------


@pytest.mark.parametrize(
    "raw,size,minimum,expected",
    [
        (0, 1, 0, 0),
        (127, 1, 0, 127),
        (255, 1, 0, 255),        # unsigned field stays unsigned
        (255, 1, -50, -1),       # signed field re-read as two's complement
        (206, 1, -50, -50),
        (50, 1, -50, 50),
        (65535, 2, -100, -1),
    ],
)
def test_signed_rereads_only_signed_fields(raw, size, minimum, expected):
    assert cf._signed(raw, size, minimum) == expected


def test_s1000_request_layout_matches_the_document():
    """``F0,47,cc,RPDATA,48, pp,pp, F7`` -- and a keygroup number when asked."""
    frame = cf._s1000_request(C.RPDATA, 5, channel=0)
    channel, command, payload = m.parse_frame(frame)
    assert (channel, command) == (0, C.RPDATA)
    assert m.decode_u14(payload[0], payload[1]) == 5
    assert len(payload) == 2

    frame = cf._s1000_request(C.RKDATA, 5, channel=0, keygroup=3)
    _channel, command, payload = m.parse_frame(frame)
    assert command == C.RKDATA
    assert len(payload) == 3 and payload[2] == 3


def test_s1000_payload_unnibbles_the_data_portion():
    data = bytes([0x00, 0x7F, 0x80, 0xFF])
    body = [*m.encode_u14(0), *m.encode_nibbles(data)]
    reply = m.build_frame(C.PDATA, body, exclusive_channel=0)

    assert cf._s1000_payload_data(reply, prefix=2) == data


# --- the checks themselves --------------------------------------------------


class _FakeBridge:
    """Answers header reads from a dict of region -> raw bytes."""

    def __init__(self, headers):
        self.headers = headers
        self.description = "fake"
        self.exclusive_channel = 0

    def get_header_bytes(self, region, index, offset, count, *, selector=0,
                         timeout=None):
        raw = self.headers[region]
        if offset + count > len(raw):
            raise DeviceError(f"{region} has no offset {offset + count}")
        return raw[offset : offset + count]


def _blank(region):
    return bytearray(p.region_size(region))


def test_range_check_flags_a_value_the_field_cannot_hold():
    raw = _blank("sample")
    spitch = p.lookup(("sample", "SPITCH"))
    raw[spitch.offset] = 200                    # documented range is 21..127

    report = cf.Report()
    cf.check_ranges(_FakeBridge({"sample": bytes(raw)}), report,
                    [("sample", 0, 0)], timeout=0.1)

    hits = [f for f in report.by_severity("contradiction") if "SPITCH" in f.what]
    assert len(hits) == 1
    assert "200" in hits[0].what and "21..127" in hits[0].what


def test_range_check_accepts_an_in_range_value():
    raw = _blank("sample")
    spitch = p.lookup(("sample", "SPITCH"))
    raw[spitch.offset] = 60

    report = cf.Report()
    cf.check_ranges(_FakeBridge({"sample": bytes(raw)}), report,
                    [("sample", 0, 0)], timeout=0.1)

    assert not [f for f in report.findings if "SPITCH" in f.what]


def test_address_fields_are_not_range_checked():
    """"Internal use" spans have no meaningful range to violate."""
    raw = _blank("sample")
    slocat = p.lookup(("sample", "SLOCAT"))
    assert slocat.kind == "address"
    for i in range(slocat.size):
        raw[slocat.offset + i] = 0xFF

    report = cf.Report()
    cf.check_ranges(_FakeBridge({"sample": bytes(raw)}), report,
                    [("sample", 0, 0)], timeout=0.1)

    assert not [f for f in report.findings if "SLOCAT" in f.what]


def test_extent_finds_the_edge_of_a_short_structure():
    """A machine whose headers stop early must be reported, not assumed."""
    short = bytes(100)
    report = cf.Report()
    cf.check_extent(_FakeBridge({"program": short}), report, ("program",),
                    timeout=0.1)

    row = report.sections["extent"][0]
    assert row["measured"] == 100
    assert row["documented"] == p.region_size("program")
    assert report.by_severity("contradiction")


def test_dry_run_completes_against_the_demo_sampler():
    assert cf.main(["--dry-run"]) == 0


def test_the_version_and_the_development_status_agree():
    """Three places describe how finished this is, and they must not drift.

    pyproject's version, its Development Status classifier, and the
    CHANGELOG's own heading were briefly inconsistent -- 0.1.0 with an Alpha
    classifier while the changelog called it a first public beta. A reader
    deciding whether to trust it near a sampler with no undo should not have
    to work out which of the three to believe.
    """
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = meta["project"]["version"]
    statuses = [c for c in meta["project"]["classifiers"]
                if c.startswith("Development Status")]

    assert statuses == ["Development Status :: 4 - Beta"], statuses
    assert version.startswith("0.1."), version

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog, \
        f"CHANGELOG has no entry for the version in pyproject ({version})"
    assert "beta" in changelog.split("## [")[1].lower()
