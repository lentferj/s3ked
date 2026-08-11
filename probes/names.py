#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The 41-entry character set exercised here is transcribed as data from
# Akai's own S1000 document ("in non-ascii form - see below"). See LICENSE
# and docs/RESOLUTION_NOTES.md §1 for provenance.
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

"""Settle the name fields: does every character survive a write?

    probes/names.py --dry-run
    probes/names.py --allow-write --program 1

**Decoding is already proven; encoding is not.** Names came back readable on
the first hardware session, so ``decode_name`` and the 41-entry table are
sound in that direction. Nothing has ever sent a name *to* the machine, so
``encode_name`` -- the mapping from a Python string into those byte values --
is untested, and every name s3ked ever writes depends on it.

Four checks, on a scratch program, restoring everything afterwards:

1. **Character coverage.** The set is 41 characters and a name is 12 bytes,
   so four names exercise every entry. Anything that comes back different
   names the wrong table entry exactly.
2. **Cross-opcode agreement.** After each write, ``RPLIST`` is asked for the
   program list. That is a different opcode family from the byte-offset read,
   so agreement means the machine's own catalogue updated -- not just the
   header bytes we wrote. Same independence argument as §11 Finding D.
3. **Padding.** ``encode_name`` space-pads and ``decode_name`` strips, an
   assumption never checked against a machine. A short name is written and
   the raw bytes inspected.
4. **References, which are not labels.** ``SNAME1``-``SNAME4`` say *which
   sample a velocity zone plays*. Writing an invented name there does not
   mislabel anything -- it points the zone at a sample that does not exist.
   So those are exercised by swapping between two names that do exist.

Sample renames go to a sample no keygroup references, for the same reason.

Nothing here writes a name matching an existing program. The specification
says a ``PDATA`` write whose name collides **deletes the other program
first**, and whether the byte-offset write inherits that is unknown -- see
TODO. Colliding on purpose is a separate, deliberate experiment, not
something this probe should do by accident.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from s3k import bridge as b  # noqa: E402
from s3k import messages as m  # noqa: E402
from s3k import params as p  # noqa: E402

NAME_LEN = m.NAME_LENGTH


def coverage_names(charset: str = m.AKAI_CHARSET,
                   length: int = NAME_LEN) -> List[str]:
    """Split the character set into names that use every entry exactly once.

    The final chunk is short, which is deliberate -- it exercises padding in
    the same pass.
    """
    return [charset[i:i + length] for i in range(0, len(charset), length)]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}" + (
            f" -- {self.detail}" if self.detail else ""
        )


@dataclass
class Run:
    checks: List[Check] = field(default_factory=list)
    unrestored: List[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks) and not self.unrestored


class NameProbe:
    def __init__(self, bridge, *, allow_write: bool, program: int, timeout: float):
        self.bridge = bridge
        self.allow_write = allow_write
        self.program = program
        self.timeout = timeout
        self.run = Run()

    # -- helpers ------------------------------------------------------------

    def _write(self, param, index, value, keygroup=0) -> None:
        if not self.allow_write:
            return
        self.bridge.set_parameter(
            param, index, value, keygroup=keygroup, timeout=self.timeout
        )

    def _read(self, param, index, keygroup=0):
        return self.bridge.get_parameter(
            param, index, keygroup=keygroup, timeout=self.timeout
        )

    def _raw(self, param, index, keygroup=0) -> bytes:
        return self.bridge.get_header_bytes(
            param.region, index, param.offset, param.size,
            selector=keygroup if param.region == "keygroup" else 0,
            timeout=self.timeout,
        )

    def _restore(self, param, index, original, keygroup=0) -> None:
        if not self.allow_write:
            return
        try:
            self._write(param, index, original, keygroup)
            if self._read(param, index, keygroup) != original:
                raise RuntimeError("read-back differs")
        except Exception as exc:
            self.run.unrestored.append(
                f"{param.region} {index} {param.name} -> {original!r} ({exc})"
            )

    # -- checks -------------------------------------------------------------

    def check_coverage(self) -> None:
        """Every character in the set, written and read back."""
        param = p.lookup(("program", "PRNAME"))
        original = self._read(param, self.program)
        seen_ok = True
        try:
            for chunk in coverage_names():
                want = chunk.rstrip()  # decode_name strips trailing spaces
                # Works with or without hardware: every character the device
                # claims to support must survive encode_name in the first
                # place, or the write could never have been legal.
                try:
                    m.encode_name(chunk)
                    self.run.add(f"{chunk!r} is encodable", True)
                except ValueError as exc:
                    seen_ok = False
                    self.run.add(f"{chunk!r} is encodable", False, str(exc))
                    continue
                self._write(param, self.program, chunk)
                got = self._read(param, self.program)
                if not self.allow_write:
                    continue
                if got != want:
                    seen_ok = False
                    bad = [
                        (i, a, c)
                        for i, (a, c) in enumerate(zip(want, got))
                        if a != c
                    ]
                    self.run.add(
                        f"charset chunk {chunk!r}", False,
                        f"read {got!r}; first difference {bad[:3]}",
                    )
                else:
                    self.run.add(f"charset chunk {chunk!r}", True, f"read {got!r}")

                # Cross-opcode: RPLIST is a different family entirely.
                listed = self.bridge.program_list()[self.program]
                self.run.add(
                    f"RPLIST agrees for {chunk!r}", listed == want,
                    f"list says {listed!r}",
                )
        finally:
            self._restore(param, self.program, original)
        if self.allow_write and seen_ok:
            self.run.add("all 41 characters round-tripped", True)

    def check_padding(self) -> None:
        """A short name: what fills the tail?"""
        param = p.lookup(("program", "PRNAME"))
        original = self._read(param, self.program)
        try:
            self._write(param, self.program, "ABC")
            if not self.allow_write:
                return
            raw = self._raw(param, self.program)
            got = self._read(param, self.program)
            space = m.AKAI_CHARSET.index(" ")
            tail = set(raw[3:])
            self.run.add("short name reads back trimmed", got == "ABC",
                         f"read {got!r}")
            self.run.add("tail is space-padded", tail == {space},
                         f"raw {raw.hex(' ')}, tail bytes {sorted(tail)} "
                         f"(space = {space})")
        finally:
            self._restore(param, self.program, original)

    def check_reference(self, keygroup: int = 0) -> None:
        """SNAME1 points at a sample; swap it between two that exist."""
        param = p.lookup(("keygroup", "SNAME1"))
        samples = self.bridge.sample_list()
        original = self._read(param, self.program, keygroup)
        other = next((s for s in samples if s != original), None)
        if other is None:
            self.run.add("zone reference swap", False, "no second sample resident")
            return
        try:
            self._write(param, self.program, other, keygroup)
            if not self.allow_write:
                return
            got = self._read(param, self.program, keygroup)
            self.run.add(f"zone 1 reference {original!r} -> {other!r}",
                         got == other, f"read {got!r}")
        finally:
            self._restore(param, self.program, original, keygroup)

    def check_sample_name(self) -> None:
        """Rename a sample no keygroup points at, and check SLIST follows."""
        param = p.lookup(("sample", "SHNAME"))
        samples = self.bridge.sample_list()
        referenced = set()
        for prog in range(len(self.bridge.program_list())):
            for kg in range(4):
                try:
                    header = self.bridge.get_header("keygroup", prog, keygroup=kg)
                except Exception:
                    break
                referenced |= {
                    str(header[f"SNAME{i}"]).strip() for i in (1, 2, 3, 4)
                }
        free = [i for i, s in enumerate(samples) if s not in referenced]
        if not free:
            self.run.add("sample rename", False,
                         "every resident sample is referenced by a keygroup")
            return

        index = free[-1]
        original = self._read(param, index)
        try:
            self._write(param, index, "RENAMETEST")
            if not self.allow_write:
                return
            got = self._read(param, index)
            listed = self.bridge.sample_list()[index]
            self.run.add(f"sample {index} rename ({original!r})",
                         got == "RENAMETEST", f"read {got!r}")
            self.run.add("RSLIST agrees", listed == "RENAMETEST",
                         f"list says {listed!r}")
        finally:
            self._restore(param, index, original)

    def check_multi_name(self) -> None:
        param = p.lookup(("multi", "MULTINAME"))
        try:
            original = self._read(param, 0)
        except Exception as exc:
            self.run.add("multi file name", False, f"could not read: {exc}")
            return
        try:
            self._write(param, 0, "MULTITEST")
            if not self.allow_write:
                return
            got = self._read(param, 0)
            self.run.add(f"multi file name ({original!r})", got == "MULTITEST",
                         f"read {got!r}")
        finally:
            self._restore(param, 0, original)

    def all_checks(self) -> Run:
        for check in (self.check_coverage, self.check_padding,
                      self.check_reference, self.check_sample_name,
                      self.check_multi_name):
            try:
                check()
            except Exception as exc:
                self.run.add(check.__name__, False, f"raised: {exc}")
        return self.run


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--exclusive-channel", type=int, default=0)
    ap.add_argument("--program", type=int, default=1,
                    help="scratch program to rename (default 1)")
    ap.add_argument("--gap", type=float, default=0.1)
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args(argv)

    if args.dry_run:
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
    elif args.port:
        bridge = b.S3kBridge.standard(
            args.port, exclusive_channel=args.exclusive_channel,
            gap=args.gap, write_gap=args.gap,
        )
    else:
        bridge = b.S3kBridge.autodetect(
            channels=(args.exclusive_channel,), gap=args.gap, write_gap=args.gap,
            on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        )

    probe = NameProbe(bridge, allow_write=args.allow_write,
                      program=args.program, timeout=args.timeout)
    if args.allow_write:
        print(f"WRITING names to program {args.program} on {bridge.description}")
    else:
        print("REHEARSAL -- nothing will be written.")
    try:
        run = probe.all_checks()
    finally:
        if hasattr(bridge, "close"):
            bridge.close()

    print()
    for check in run.checks:
        print(f"  {check.line()}")
    if run.unrestored:
        print()
        print("!!! NOT RESTORED:")
        for item in run.unrestored:
            print(f"    {item}")
    print()
    failed = [c for c in run.checks if not c.ok]
    print(f"{len(run.checks) - len(failed)} passed, {len(failed)} failed")
    return 0 if run.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
