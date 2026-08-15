#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The operations probed here, their frame layouts and the ranges checked
# against are transcribed as data from Akai's own S1000 and
# S2800/S3000/S3200 MIDI System Exclusive documents. See LICENSE and
# docs/RESOLUTION_NOTES.md §1 for provenance.
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

"""Read-only conformance sweep: what the machine does versus what the book says.

    probes/conformance.py --dry-run
    probes/conformance.py --out conformance.json

**This probe cannot write.** Every frame leaving it passes an opcode
allowlist (:data:`READ_ONLY_OPS`) installed in front of the bridge's output
port; a write, a delete or a SETEX raises :class:`Forbidden` before it
reaches the wire. That guard is the reason this is safe to run unattended,
and it is asserted rather than merely intended -- see ``_ReadOnlyOut``.

Six checks, cheapest first:

1. **inventory** -- program and sample lists, and whether the names decode
   through the device's 41-entry character set without unmappable bytes.
2. **opcodes** -- send every documented *read* operation and record what
   comes back: data, a REPLY/error, silence, or the wrong operation code.
   Three opcode layers are stacked in this family and nobody has confirmed
   which ones a real S3000XL answers.
3. **ranges** -- read every header and check each field's value against the
   range the specification states for it. A wrong offset, width or signedness
   shows up here as a value that cannot be what the field claims to be. This
   is the check that judges our transcription against the machine rather than
   against itself.
4. **cross-layer** -- read the same header twice, once with the S1000
   whole-block operation (``RPDATA``/``RKDATA``/``RSDATA``, nibbled) and once
   with the S3000 byte-offset operation (``RPHEADER``/``RKHEADER``/
   ``RSHEADER``). Two different opcode layers reading the same bytes: if they
   agree, the offsets are corroborated by something other than themselves.
5. **extent** -- find where reads start being refused, which pins the real
   size of each structure rather than the documented one.
6. **invariants** -- cross-message consistency: keygroup zones naming samples
   that exist, GROUPS matching the keygroups that actually answer, loop points
   inside the sample.

``--dry-run`` runs everything against the built-in demo sampler and opens no
MIDI port. The demo answers only the high-level calls, so the raw-frame checks
report themselves skipped rather than inventing a result.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import bridge as b  # noqa: E402
from s3k import messages as m  # noqa: E402
from s3k import params as p  # noqa: E402

C = m.Command

#: Every operation this probe is permitted to put on the wire.
#:
#: Requests only, and only requests that read. ``RSPACK`` is a read but is
#: excluded deliberately: it pulls whole sample *data* packets, which is a
#: bulk transfer measured in megabytes, not a conformance question.
READ_ONLY_OPS = frozenset(
    {
        C.RSTAT,
        C.RPLIST,
        C.RSLIST,
        C.RPDATA,
        C.RKDATA,
        C.RSDATA,
        C.RPHEADER,
        C.RKHEADER,
        C.RSHEADER,
        C.RFXDATA,
        C.RCUEDATA,
        C.RTAKEDATA,
        C.RMISCDATA,
        C.RVOLLIST,
        C.RHDDIR,
        C.RMULTIDATA,
    }
)


class Forbidden(RuntimeError):
    """The probe tried to send something that is not a read. A bug, not a finding."""


class _ReadOnlyOut:
    """Output-port shim that refuses anything outside :data:`READ_ONLY_OPS`.

    Wraps the bridge's ``out`` rather than trusting call sites. Every frame
    this project sends is ``F0 47 cc op 48 ...``, so the operation code is at
    a fixed index and can be checked without parsing the rest.
    """

    def __init__(self, inner):
        self._inner = inner
        self.sent = 0

    def send_message(self, message, *, write: bool = False) -> None:
        data = list(message)
        if len(data) < 4 or data[0] != m.SOX:
            raise Forbidden(f"not a SysEx frame: {data[:8]}")
        op = data[3]
        if op not in READ_ONLY_OPS:
            name = C(op).name if op in set(C) else f"{op:#04x}"
            raise Forbidden(
                f"refusing to send {name}: this probe is read-only "
                f"(allowlist: {sorted(x.name for x in READ_ONLY_OPS)})"
            )
        if write:
            raise Forbidden("refusing a frame flagged as a write")
        self.sent += 1
        self._inner.send_message(data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@dataclass
class Finding:
    """One thing the machine did that the documents do not account for."""

    check: str
    severity: str  # "contradiction" | "gap" | "note"
    what: str
    detail: str = ""

    def line(self) -> str:
        tail = f" -- {self.detail}" if self.detail else ""
        return f"[{self.severity}] {self.check}: {self.what}{tail}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    sections: Dict[str, object] = field(default_factory=dict)
    reads: int = 0
    seconds: float = 0.0

    def add(self, check: str, severity: str, what: str, detail: str = "") -> None:
        self.findings.append(Finding(check, severity, what, detail))

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]


# --- helpers ---------------------------------------------------------------


def _signed(value: int, size: int, minimum: int) -> int:
    """Re-read a field as two's complement when its range says it is signed."""
    if minimum < 0 and value >= (1 << (8 * size - 1)):
        return value - (1 << (8 * size))
    return value


def _raw_exchange(bridge, frame: bytes, timeout: float) -> Optional[bytes]:
    """Send a hand-built frame, return the reply, or None on silence."""
    try:
        return bridge.send_and_receive(frame, timeout=timeout)
    except Exception:
        return None


def _s1000_request(op: int, index: int, channel: int, keygroup: Optional[int] = None) -> bytes:
    """Build an S1000-layer whole-block request.

    From the S1000 document: ``F0,47,cc,RPDATA,48, pp,pp, F7`` for a program,
    with a keygroup number appended for ``RKDATA``.
    """
    payload = list(m.encode_u14(index))
    if keygroup is not None:
        payload.append(keygroup & 0x7F)
    return m.build_frame(op, payload, exclusive_channel=channel)


def _s1000_payload_data(reply: bytes, prefix: int) -> Optional[bytes]:
    """Un-nibble the data portion of a PDATA/KDATA/SDATA reply.

    *prefix* is how many body bytes precede the data (2 for a program or
    sample number, 3 when a keygroup number follows it).
    """
    try:
        _channel, _command, payload = m.parse_frame(reply)
    except ValueError:
        return None
    body = payload[prefix:]
    if len(body) % 2:
        body = body[:-1]
    try:
        return m.decode_nibbles(body)
    except ValueError:
        return None


# --- check 1: inventory ----------------------------------------------------


def check_inventory(bridge, report: Report) -> Tuple[List[str], List[str]]:
    programs = bridge.program_list()
    samples = bridge.sample_list()
    report.sections["inventory"] = {"programs": programs, "samples": samples}

    for label, names in (("program", programs), ("sample", samples)):
        for i, name in enumerate(names):
            if "?" in name:
                report.add(
                    "inventory",
                    "contradiction",
                    f"{label} {i} name has bytes outside the character set",
                    f"decoded as {name!r}; the 41-entry table in AKAI_CHARSET "
                    f"does not cover what the machine sent",
                )
    return programs, samples


# --- check 2: opcode support map -------------------------------------------

#: Extended-layer reads, all of which share the uniform 12-byte header, with
#: the selector value each one needs. Everything from RFXDATA down has never
#: been confirmed against any machine.
_EXTENDED_PROBES: Tuple[Tuple[int, int, str], ...] = (
    (C.RPHEADER, 0, "program header"),
    (C.RKHEADER, 0, "keygroup header"),
    (C.RSHEADER, 0, "sample header"),
    (C.RFXDATA, int(m.FxSelector.FX_HEADER), "FX header"),
    (C.RFXDATA, int(m.FxSelector.FX_ASSIGN), "FX assign"),
    (C.RFXDATA, int(m.FxSelector.FX_ENTRY), "FX entry"),
    (C.RFXDATA, int(m.FxSelector.RVB_ASSIGN), "reverb assign"),
    (C.RFXDATA, int(m.FxSelector.RVB_ENTRY), "reverb entry"),
    (C.RCUEDATA, 0, "cue list"),
    (C.RTAKEDATA, 0, "take list"),
    (C.RMISCDATA, 0, "miscellaneous data"),
    (C.RVOLLIST, 0, "volume list"),
    (C.RHDDIR, 0, "harddisk directory"),
    (C.RMULTIDATA, 0, "multi file header"),
    (C.RMULTIDATA, 1, "multi part"),
)


def _classify(reply: Optional[bytes], expect: int) -> Tuple[str, str]:
    if reply is None:
        return "silent", "no answer within the timeout"
    try:
        _channel, command, payload = m.parse_frame(reply)
    except ValueError:
        return "unparseable", reply.hex(" ")
    if command == C.REPLY:
        code = payload[0] if payload else -1
        name = m.ReplyCode(code).name if code in set(m.ReplyCode) else str(code)
        return ("refused" if code else "ok-no-data"), f"REPLY {name}"
    if command == expect:
        return "supported", f"{len(payload)} body bytes"
    return "wrong-op", f"expected {expect:#04x}, got {command:#04x}"


def check_opcodes(bridge, report: Report, timeout: float) -> Dict[str, str]:
    supported: Dict[str, str] = {}
    rows = []

    for op, selector, label in _EXTENDED_PROBES:
        frame = m.HeaderRequest(
            command=op,
            index=0,
            selector=selector,
            offset=0,
            count=1,
            exclusive_channel=bridge.exclusive_channel,
        ).encode()
        reply = _raw_exchange(bridge, frame, timeout)
        status, detail = _classify(reply, m.EXTENDED_REPLY_FOR[op])
        key = f"{C(op).name}[{selector}]"
        supported[key] = status
        rows.append({"op": C(op).name, "selector": selector, "what": label,
                     "status": status, "detail": detail})
        report.reads += 1

    for op, expect, kg, label in (
        (C.RPDATA, C.PDATA, None, "program common block"),
        (C.RKDATA, C.KDATA, 0, "keygroup block"),
        (C.RSDATA, C.SDATA, None, "sample header block"),
    ):
        frame = _s1000_request(op, 0, bridge.exclusive_channel, keygroup=kg)
        reply = _raw_exchange(bridge, frame, timeout)
        status, detail = _classify(reply, expect)
        supported[C(op).name] = status
        rows.append({"op": C(op).name, "selector": None, "what": label,
                     "status": status, "detail": detail})
        report.reads += 1

    report.sections["opcodes"] = rows

    for row in rows:
        if row["status"] in ("silent", "unparseable", "wrong-op"):
            report.add(
                "opcodes",
                "gap",
                f"{row['op']} ({row['what']}) -> {row['status']}",
                row["detail"],
            )
    return supported


# --- check 3: range conformance --------------------------------------------


def _read_region(bridge, region: str, index: int, keygroup: int, timeout: float):
    params = p.region_params(region)
    extent = max(x.end for x in params)
    selector = keygroup if region == "keygroup" else None
    kwargs = {"timeout": timeout}
    if selector is not None:
        kwargs["selector"] = selector
    elif region == "multipart":
        kwargs["selector"] = 1
    return params, bridge.get_header_bytes(region, index, 0, extent, **kwargs)


def check_ranges(
    bridge, report: Report, targets: Sequence[Tuple[str, int, int]], timeout: float
) -> Dict[str, Dict[str, object]]:
    values: Dict[str, Dict[str, object]] = {}
    checked = 0

    for region, index, keygroup in targets:
        label = f"{region} {index}" + (f" kg {keygroup}" if region == "keygroup" else "")
        try:
            params, raw = _read_region(bridge, region, index, keygroup, timeout)
        except Exception as exc:
            report.add("ranges", "gap", f"{label} could not be read", str(exc))
            continue
        report.reads += 1

        decoded: Dict[str, object] = {}
        for param in params:
            span = raw[param.offset : param.end]
            if len(span) != param.size:
                continue
            if param.kind == "text":
                text = p.decode_field(param, span)
                decoded[param.name] = text
                if "?" in str(text):
                    report.add(
                        "ranges",
                        "contradiction",
                        f"{label} {param.name} is not decodable text",
                        f"offset {param.offset}, {param.size} bytes -> {text!r}",
                    )
                continue

            number = _signed(
                int.from_bytes(span, "little"), param.size, param.minimum
            )
            decoded[param.name] = number
            if param.kind == "address":
                continue  # "internal use"; the spec states no meaningful range
            checked += 1
            if not (param.minimum <= number <= param.maximum):
                report.add(
                    "ranges",
                    "contradiction",
                    f"{label} {param.name} = {number}, outside {param.minimum}..{param.maximum}",
                    f"offset {param.offset}, {param.size} byte(s), "
                    f"raw {span.hex(' ')}",
                )
        values[label] = decoded

    # Called twice -- programs and samples, then the keygroups their GROUPS
    # implies -- so accumulate rather than replace.
    section = report.sections.setdefault("ranges", {"checked_fields": 0, "values": {}})
    section["checked_fields"] += checked
    section["values"].update(values)
    return values


# --- check 4: cross-layer agreement ----------------------------------------


def check_cross_layer(bridge, report: Report, timeout: float) -> None:
    """S1000 whole-block reads against S3000 byte-offset reads of the same header.

    The only check here that is not self-referential: two different opcode
    layers, written years apart, describing the same bytes.
    """
    cases = (
        ("program", C.RPDATA, 2, None, 0),
        ("keygroup", C.RKDATA, 3, 0, 0),
        ("sample", C.RSDATA, 2, None, 0),
    )
    rows = []

    for region, op, prefix, keygroup, index in cases:
        frame = _s1000_request(op, index, bridge.exclusive_channel, keygroup=keygroup)
        reply = _raw_exchange(bridge, frame, timeout)
        report.reads += 1
        if reply is None:
            rows.append({"region": region, "result": "no S1000-layer answer"})
            report.add(
                "cross-layer",
                "gap",
                f"{C(op).name} did not answer; {region} offsets stay uncorroborated",
            )
            continue

        block = _s1000_payload_data(reply, prefix)
        if block is None:
            rows.append({"region": region, "result": "unparseable"})
            report.add("cross-layer", "gap", f"{C(op).name} reply could not be un-nibbled")
            continue

        try:
            kwargs = {"timeout": timeout}
            if region == "keygroup":
                kwargs["selector"] = keygroup
            extended = bridge.get_header_bytes(region, index, 0, len(block), **kwargs)
            report.reads += 1
        except Exception as exc:
            rows.append({"region": region, "result": f"extended read failed: {exc}"})
            report.add("cross-layer", "gap", f"{region}: extended read failed", str(exc))
            continue

        overlap = min(len(block), len(extended))
        mismatches = [i for i in range(overlap) if block[i] != extended[i]]
        rows.append(
            {
                "region": region,
                "s1000_bytes": len(block),
                "s3000_bytes": len(extended),
                "compared": overlap,
                "mismatches": mismatches[:32],
                "mismatch_count": len(mismatches),
            }
        )
        if mismatches:
            first = mismatches[0]
            report.add(
                "cross-layer",
                "contradiction",
                f"{region}: {len(mismatches)} of {overlap} bytes differ between "
                f"{C(op).name} and the byte-offset read",
                f"first at offset {first}: S1000 layer {block[first]:#04x}, "
                f"S3000 layer {extended[first]:#04x}",
            )

    report.sections["cross_layer"] = rows


# --- check 5: structure extent ---------------------------------------------


#: How far past a structure to probe before concluding the machine simply
#: does not range-check. Well beyond any documented header.
_EXTENT_CEILING = 1024


def check_extent(bridge, report: Report, regions: Sequence[str], timeout: float) -> None:
    """Find the highest offset each structure will answer a one-byte read at.

    The S1000 document promises an out-of-range request draws an error. On the
    extended layer that turns out not to be true (§11) -- the machine answers
    anything -- so reaching the ceiling is reported as *no bound found* rather
    than as a measurement. Silently printing the ceiling as if it were the
    structure size would be inventing a number.
    """
    rows = []
    for region in regions:
        documented = p.region_size(region)

        def readable(offset: int) -> bool:
            try:
                kwargs = {"timeout": timeout}
                if region == "keygroup":
                    kwargs["selector"] = 0
                elif region == "multipart":
                    kwargs["selector"] = 1
                bridge.get_header_bytes(region, 0, offset, 1, **kwargs)
                return True
            except Exception:
                return False

        report.reads += 1
        if not readable(0):
            rows.append({"region": region, "result": "offset 0 is not readable"})
            report.add("extent", "gap", f"{region}: even offset 0 refused")
            continue

        low, high = 0, _EXTENT_CEILING
        while low + 1 < high:
            mid = (low + high) // 2
            report.reads += 1
            if readable(mid):
                low = mid
            else:
                high = mid
        actual = low + 1

        if actual >= _EXTENT_CEILING:
            rows.append({"region": region, "documented": documented,
                         "measured": None, "result": "no bound found"})
            report.add(
                "extent",
                "contradiction",
                f"{region}: no upper bound -- reads answered at every offset "
                f"up to {_EXTENT_CEILING}",
                "the document says an out-of-range request draws an error; "
                "this machine returns data instead, so a wrong offset fails "
                "silently rather than loudly (§11)",
            )
            continue

        rows.append({"region": region, "documented": documented, "measured": actual})
        if actual != documented:
            report.add(
                "extent",
                "note" if actual > documented else "contradiction",
                f"{region} answers {actual} bytes, the table assumes {documented}",
                "larger than documented is headroom; smaller means the table "
                "describes bytes this machine does not have",
            )
    report.sections["extent"] = rows


# --- check 6: cross-message invariants -------------------------------------


def check_invariants(
    bridge,
    report: Report,
    values: Dict[str, Dict[str, object]],
    samples: Sequence[str],
    timeout: float,
) -> None:
    rows = []
    known = {s.strip() for s in samples}

    program = values.get("program 0")
    if program and hasattr(bridge, "send_and_receive"):
        groups = program.get("GROUPS")
        if isinstance(groups, int):
            # Counted with RKDATA, not RKHEADER. The extended layer answers a
            # read for *any* keygroup number (§11), so counting with it would
            # measure this loop's own bound; the S1000 layer refuses a
            # keygroup that does not exist, exactly as documented.
            answering = 0
            for kg in range(99):
                frame = _s1000_request(
                    C.RKDATA, 0, bridge.exclusive_channel, keygroup=kg
                )
                reply = _raw_exchange(bridge, frame, timeout)
                report.reads += 1
                if reply is None:
                    break
                _channel, command, _payload = m.parse_frame(reply)
                if command != C.KDATA:
                    break
                answering += 1
            rows.append({"GROUPS": groups, "keygroups_answering": answering,
                         "counted_with": "RKDATA"})
            if answering != groups:
                report.add(
                    "invariants",
                    "contradiction",
                    f"program 0 declares GROUPS={groups} but {answering} keygroups answer",
                    "either the GROUPS offset is wrong or the keygroup selector is",
                )

    for label, decoded in values.items():
        if not label.startswith("keygroup"):
            continue
        for n in (1, 2, 3, 4):
            name = decoded.get(f"SNAME{n}")
            if not isinstance(name, str):
                continue
            trimmed = name.strip()
            if not trimmed or set(trimmed) <= {"0"}:
                continue  # an unused zone
            if trimmed not in known:
                report.add(
                    "invariants",
                    "contradiction",
                    f"{label} zone {n} names sample {trimmed!r}, which is not resident",
                    f"resident samples: {sorted(known)}",
                )

    for label, decoded in values.items():
        if not label.startswith("sample"):
            continue
        length = decoded.get("SLNGTH")
        if not isinstance(length, int) or length <= 0:
            continue
        for field_name in ("SSTART", "SMPEND", "LOOPAT1"):
            value = decoded.get(field_name)
            if isinstance(value, int) and value > length:
                report.add(
                    "invariants",
                    "contradiction",
                    f"{label} {field_name}={value} is past SLNGTH={length}",
                    "a sample position outside its own sample means one of the "
                    "two offsets is wrong",
                )
    report.sections["invariants"] = rows


# --- driver ----------------------------------------------------------------


def run(bridge, *, timeout: float, max_keygroups: int, max_samples: int) -> Report:
    report = Report()
    started = time.time()

    status = bridge.status()
    report.sections["status"] = {
        "connection": bridge.description,
        "exclusive_channel": status.exclusive_channel,
        "blocks": [status.used_blocks, status.max_blocks],
        "words": [status.used_words, status.max_words],
    }
    report.reads += 1

    programs, samples = check_inventory(bridge, report)

    raw_capable = hasattr(bridge, "send_and_receive")
    if raw_capable:
        check_opcodes(bridge, report, timeout)
    else:
        report.sections["opcodes"] = "skipped: bridge has no raw frame path"

    # Programs first, so each one's GROUPS says how many keygroups to sweep.
    # Sweeping a fixed count instead would fill the report with refusals that
    # are correct behaviour, and bury the findings that are not.
    program_targets = [("program", i, 0) for i in range(len(programs))]
    program_targets += [
        ("sample", i, 0) for i in range(min(len(samples), max_samples))
    ]
    # Multi mode is the S2000/S3000XL/S3200XL headline feature and the least
    # corroborated part of the table, so sweep it whenever the machine
    # answers the opcode at all.
    opcodes = report.sections.get("opcodes")
    if isinstance(opcodes, list) and any(
        row["op"] == "RMULTIDATA" and row["status"] == "supported"
        for row in opcodes
    ):
        program_targets += [("multi", 0, 0), ("multipart", 0, 0)]
    values = check_ranges(bridge, report, program_targets, timeout)

    keygroup_targets: List[Tuple[str, int, int]] = []
    for index in range(len(programs)):
        declared = values.get(f"program {index}", {}).get("GROUPS")
        count = declared if isinstance(declared, int) and declared > 0 else 1
        for kg in range(min(int(count), max_keygroups)):
            keygroup_targets.append(("keygroup", index, kg))
    values.update(check_ranges(bridge, report, keygroup_targets, timeout))

    if raw_capable:
        check_cross_layer(bridge, report, timeout)
    else:
        report.sections["cross_layer"] = "skipped: bridge has no raw frame path"

    check_extent(bridge, report, ("program", "keygroup", "sample"), timeout)
    check_invariants(bridge, report, values, samples, timeout)

    report.seconds = time.time() - started
    return report


def _print(report: Report) -> None:
    print()
    print(f"{report.reads} reads in {report.seconds:.1f}s")

    order = ("contradiction", "gap", "note")
    counts = {s: len(report.by_severity(s)) for s in order}
    print(
        "contradictions: {contradiction}   gaps: {gap}   notes: {note}".format(**counts)
    )

    for severity in order:
        found = report.by_severity(severity)
        if not found:
            continue
        print()
        print(f"--- {severity} ---")
        for finding in found:
            print(f"  {finding.line()}")

    if not report.findings:
        print()
        print("nothing contradicted the documents.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="MIDI port name (default: autodetect)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run against the demo sampler; opens no MIDI port")
    ap.add_argument("--exclusive-channel", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=1.0,
                    help="reply timeout in seconds (default: 1.0)")
    ap.add_argument("--max-keygroups", type=int, default=4,
                    help="keygroups per program to sweep (default: 4)")
    ap.add_argument("--max-samples", type=int, default=8,
                    help="sample headers to sweep (default: 8)")
    ap.add_argument("--out", help="write the full report as JSON here")
    args = ap.parse_args(argv)

    if args.dry_run:
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
    elif args.port:
        bridge = b.S3kBridge.standard(
            args.port, exclusive_channel=args.exclusive_channel
        )
    else:
        bridge = b.S3kBridge.autodetect(
            channels=(args.exclusive_channel,),
            on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        )

    # The guard goes on before anything is sent, including by the checks that
    # build their own frames.
    guard = None
    if hasattr(bridge, "out"):
        guard = _ReadOnlyOut(bridge.out)
        bridge.out = guard

    try:
        report = run(
            bridge,
            timeout=args.timeout,
            max_keygroups=args.max_keygroups,
            max_samples=args.max_samples,
        )
    finally:
        if hasattr(bridge, "close"):
            bridge.close()

    if guard is not None:
        report.sections["frames_sent"] = guard.sent

    _print(report)

    if args.out:
        payload = {
            "findings": [vars(f) for f in report.findings],
            "sections": report.sections,
            "reads": report.reads,
            "seconds": round(report.seconds, 2),
        }
        Path(args.out).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print()
        print(f"full report written to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
