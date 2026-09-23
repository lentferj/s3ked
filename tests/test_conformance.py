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

import re
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
    marker = f"## [{version}]"
    assert marker in changelog, \
        f"CHANGELOG has no entry for the version in pyproject ({version})"

    # Find THIS version's section, not the first one in the file. main carries
    # an [Unreleased] section above it, so indexing the first heading tested
    # whichever section happened to come first -- a test coupled to file
    # order rather than to what it was checking.
    section = changelog[changelog.index(marker):]
    section = section.split("\n## [")[0]
    assert "beta" in section.lower(), section[:200]


def _notes_anchor(heading: str) -> str:
    """GitHub's rule: lowercase, keep alphanumerics/space/hyphen, space->hyphen."""
    a = "".join(c for c in heading.lower() if c.isalnum() or c in " -")
    return a.replace(" ", "-")


def test_the_resolution_notes_index_matches_the_sections():
    """Sections are referenced from code as `RESOLUTION_NOTES §N`.

    An index that has drifted is worse than none: it sends a reader to a
    section that is not there, and the file is append-only so it drifts on
    every finding. Regenerate with tools/, or add the entry by hand -- but
    this fails first.

    It also enforces that a section NUMBER is unique. It did not until
    2026-09-15, when two unrelated sections were found both numbered §10 --
    one cited seven times and one twice, so every `§10` in the tree was
    ambiguous. The duplicate-anchor check above passed throughout, because
    the two headings have different titles and therefore different anchors:
    it was guarding the link, which was fine, and not the citation, which
    was not. Found by a count disagreeing with a sibling project's.
    """
    import pathlib
    import re
    from collections import Counter

    notes = (pathlib.Path(__file__).resolve().parent.parent
             / "docs" / "RESOLUTION_NOTES.md").read_text(encoding="utf-8")

    headings = [m.group(1) for m in
                re.finditer(r"^## (§\d+[a-z]? — .+)$", notes, re.M)]
    linked = re.findall(r"^- \[§[\da-z]+\]\(#([^)]+)\)", notes, re.M)

    assert headings, "no numbered sections found at all"
    assert len(linked) == len(headings), (
        f"{len(headings)} sections but {len(linked)} index entries")

    anchors = [_notes_anchor(h) for h in headings]
    dupes = [a for a, n in Counter(anchors).items() if n > 1]
    assert not dupes, f"duplicate anchors GitHub would suffix: {dupes[:3]}"

    missing = [a for a in linked if a not in set(anchors)]
    assert not missing, f"index links to nothing: {missing[:3]}"

    numbers = [re.match(r"§(\d+[a-z]?)", h).group(1) for h in headings]
    clashes = [n for n, k in Counter(numbers).items() if k > 1]
    assert not clashes, (
        f"two sections share a number, so every citation to it is "
        f"ambiguous: {clashes}")

    absent = [h for h, a in zip(headings, anchors) if a not in set(linked)]
    assert not absent, f"sections missing from the index: {absent[:3]}"


#: A LINEAR law stated in the notes: "= 0.11840 * PANRAT". SINGLE-FACTOR
#: ONLY -- "0.009474 * V_LOUD * (knee - velocity)" is a coefficient in a
#: two-factor law, not a slope, and matching it reported V_LOUD, K_FREQ and
#: SUSTN2 as stale against unrelated numbers.
#:
#: THE WHITESPACE BELONGS INSIDE THE LOOKAHEAD. `\b(NAME)\b\s*(?![*x(])`
#: reads as the same restriction and imposes none: `\s*` matches zero
#: characters, so the lookahead inspects the space rather than the `*` behind
#: it. The two forms are identical on single-factor input, which is why the
#: bug is invisible -- see test_the_single_factor_lookahead_actually_rejects.
LINEAR_LAW = re.compile(
    r"=\s*([0-9]*\.?[0-9]+(?:e-?\d+)?)\s*[*x]\s*"
    r"\b([A-Z][A-Z0-9_]{3,9})\b(?!\s*[*x(])")


def test_the_single_factor_lookahead_actually_rejects():
    """A filter that silently passes everything looks like one with nothing to reject.

    This pins the LIVE pattern, not a copy, so a later tidy-up that makes it
    inert fails here instead of passing quietly. The second half asserts the
    KNOWN-BAD form still accepts what it should reject -- without that, this
    test could go vacuous the day the cases stop being two-factor and nobody
    would know. (mpc2emu's shape, 2026-09-23: assert against a synthetic bad
    pattern as well as the correct one, or a sweep that matches nothing is
    indistinguishable from a clean tree.)
    """
    buggy = re.compile(
        r"=\s*([0-9]*\.?[0-9]+(?:e-?\d+)?)\s*[*x]\s*"
        r"\b([A-Z][A-Z0-9_]{3,9})\b\s*(?![*x(])")
    single = ["rate = 0.11840 * PANRAT + 0.0108 Hz",
              "dB = 0.642719 * PRLOUD - 87.63"]
    double = ["attenuation = 0.009474 * V_LOUD * (knee - velocity)",
              "octaves = 0.002075 * SUSTN2 * MODVFILT1",
              "shift = 0.06386 * K_FREQ * (note - 64)"]
    for t in single:
        assert LINEAR_LAW.search(t), f"must match a single-factor law: {t}"
    for t in double:
        assert not LINEAR_LAW.search(t), f"must reject a two-factor law: {t}"
    # and the trap is real: the buggy form rejects none of them
    assert all(buggy.search(t) for t in double), (
        "the known-bad pattern no longer accepts the two-factor cases, so "
        "this test no longer demonstrates the difference it exists to pin")


def test_a_section_whose_law_was_refitted_says_so_in_its_heading():
    """Refinement is invisible where retraction is visible.

    A retracted finding gets a later section saying so, and a reader who reads
    forward will find it. A *refitted constant* leaves the earlier section
    correct as written -- the prose still holds, only the numbers moved -- so
    nothing marks it and nothing ever will unless the heading does.

    That is not hypothetical: the converter project quoted §31's ATTAK1 law
    while §141 held the current one, and separately built a behaviour on §29
    that §31 had withdrawn the same day. It cost every attack in its output.

    This checks the structural property only -- that a section stating a law
    which `scales.py` no longer holds carries a marker in its heading. It does
    NOT check that any finding is true; a test cannot do that, and a test that
    pins a finding is how a wrong one survives (see §141).
    """
    import pathlib
    import re

    from s3k import scales

    notes = (pathlib.Path(__file__).resolve().parent.parent
             / "docs" / "RESOLUTION_NOTES.md").read_text(encoding="utf-8")
    current = {key[1]: (sc.a, sc.b) for key, sc in scales.SCALES.items()}

    heads = [(m.start(), m.group(0)) for m in
             re.finditer(r"^## §\d+[a-z]? — .+$", notes, re.M)]
    law = re.compile(
        r"\b([A-Z][A-Z0-9_]{3,9})\b[^\n]{0,60}?=\s*"
        r"([0-9]*\.?[0-9]+(?:e-?\d+)?)\s*\*\s*exp\(\s*(-?[0-9]*\.?[0-9]+)")
    # LINEAR laws were invisible to the exponential pattern above until
    # 2026-09-23, and that is exactly how PANRAT shipped at 2.002x the truth
    # for seventeen days (§260): §257 stated `rate = 0.11840 * PANRAT` while
    # scales.py held 0.23708, and this test could not see either number.
    # Matches "0.11840 * PANRAT" and "rate = 0.11840 * PANRAT + 0.0108 Hz".
    linear = LINEAR_LAW
    # a newer section may quote the law it supersedes; those lines say so
    quoting = re.compile(r"previous|earlier|withdraw|supersed|retract|was\b",
                         re.I)

    # A REVIEWED BASELINE, not a mute button. Each entry means someone read
    # the section and confirmed it states a DIFFERENT QUANTITY, not a stale
    # law. Adding one is a claim; leaving one that no longer applies is a
    # silent hole, so each carries its reason. (mpc2emu's shape, 2026-09-23.)
    reviewed = {
        # §171 fits the FULL SWING v1->v127; scales.py holds the per-side
        # deviation, which is half of it. 1.19557 / 0.596862 = 2.003 --
        # the factor of two IS the parameterisation, not a disagreement.
        ("§171", "V_LOUD"),
        # §260 is the section ABOUT superseded laws. It quotes the stale
        # PRLOUD slope as a worked example of what this guard catches, in a
        # table demonstrating that the correct and buggy lookaheads agree on
        # single-factor input. Naming the exclusion rather than widening the
        # `quoting` regex, because "0.642719" appearing in prose that never
        # says "superseded" is exactly the case the guard must keep catching
        # elsewhere -- §22 and §24 read that way too.
        ("§260", "PRLOUD"),
    }
    stale = []
    for i, (start, head) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(notes)
        # CASE-SENSITIVE, and the vocabulary this project actually uses.
        # These notes write a status marker in capitals and the same word in
        # lower case as prose -- "§X — ... and a withdrawn 19 %" is a LIVE
        # section describing a struck sub-claim. A case-insensitive match
        # silences the guard on live sections, which is worse than no guard.
        # (mpc2emu hit this building the same check on 2026-09-23.)
        if any(k in head for k in ("SUPERSEDED", "RETRACTED", "RETRACTION",
                                   "WITHDRAWN", "REFUTED")):
            continue
        for line in notes[start:end].splitlines():
            if quoting.search(line):
                continue
            for m in law.finditer(line):
                param, a, b = m.group(1), float(m.group(2)), float(m.group(3))
                if param not in current:
                    continue
                ta, tb = current[param]
                moved = (abs(a - ta) / max(abs(ta), 1e-12) > 0.02
                         or abs(b - tb) / max(abs(tb), 1e-12) > 0.02)
                if moved:
                    stale.append(f"{head.split(' — ')[0]} states {param} = "
                                 f"{a:g}*exp({b:g}) but scales.py holds "
                                 f"{ta:g}*exp({tb:g})")
            for m in linear.finditer(line):
                a, param = float(m.group(1)), m.group(2)
                sc = scales.SCALES.get(("program", param)) or \
                    scales.SCALES.get(("keygroup", param))
                if sc is None or sc.kind != "linear":
                    continue
                sec = head.split(' — ')[0].replace('## ', '')
                if (sec, param) in reviewed:
                    continue
                if abs(a - sc.a) / max(abs(sc.a), 1e-12) > 0.02:
                    stale.append(f"{head.split(' — ')[0]} states {param} "
                                 f"slope {a:g} but scales.py holds {sc.a:g}")
    assert not stale, (
        "section states a superseded law without a marker in its heading:\n  "
        + "\n  ".join(stale[:6]))
