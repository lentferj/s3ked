# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
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

"""A sampler stand-in, so the CLI and TUI run with no hardware and no ports.

:class:`DemoBridge` duck-types the part of :class:`s3k.bridge.S3kBridge` the
application uses. It deliberately does **not** subclass or import it: the
point is to prove the application never reaches past that surface, and
inheriting would hide the moment it does.

This is production code -- ``--demo`` is a shipped mode, not a test fixture --
and the test suite subclasses it for individual scenarios.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from s3k import messages as m
from s3k import params as p

__all__ = ["DemoBridge", "DemoError"]


class DemoError(RuntimeError):
    """What the demo raises where a real device would report an error."""


# Starting state only. Every DemoBridge copies these into its own instance
# attributes in __init__ and rebuilds its headers from scratch; these lists
# are NEVER mutated.
#
# The sibling eosed project learned this the hard way: when its demo state
# lived in module-level dicts, every instance in a process shared one device,
# so a single destructive call in one test leaked into every DemoBridge built
# afterwards -- which is why its suite needed an autouse save/restore fixture
# just to pass. Copying at construction removes the need for one.
#
# Every name here is invented. CLAUDE.md forbids committing the name of any
# program or sample from a commercial library, and invented names avoid real
# instrument trademarks too -- so these describe sounds generically.
_DEMO_PROGRAMS: List[str] = [
    "BASS ROUND",
    "PAD WIDE",
    "KIT DRY",
    "BELL SOFT",
    "STRINGS LO",
]

_DEMO_SAMPLES: List[str] = [
    "BASS C1",
    "BASS C2",
    "PAD LOOP L",
    "PAD LOOP R",
    "KICK 1",
    "SNARE 1",
    "HAT CLOSED",
    "BELL A3",
    "STRING C2",
]

#: How many keygroups each demo program has, by program index.
_DEMO_KEYGROUPS: List[int] = [2, 1, 4, 1, 3]


def _blank_header(region: str) -> bytearray:
    """A header with every field at a plausible value rather than all zeroes.

    All-zero headers make a UI look broken in a way real ones never do, and
    hide off-by-one bugs in the offset table because every byte reads the
    same. Seeding each field with its own minimum spreads distinct values
    across the span.
    """
    raw = bytearray(p.HEADER_SIZE)
    for param in p.region_params(region):
        if param.kind == "text":
            raw[param.offset : param.end] = bytes(
                m.encode_name("", param.size)
            )
            continue
        value = param.default if param.default is not None else param.minimum
        if value < 0:
            value = 0
        try:
            raw[param.offset : param.end] = p.encode_field(param, value)
        except ValueError:
            pass  # a range the table cannot represent; leave the zeroes
    return raw


class DemoBridge:
    """An in-memory sampler that answers everything the application asks."""

    def __init__(self) -> None:
        self.description = "demo (no hardware)"
        self.exclusive_channel = 0
        self.timeout = 0.0
        self.closed = False

        self._programs: List[str] = list(_DEMO_PROGRAMS)
        self._samples: List[str] = list(_DEMO_SAMPLES)
        self._keygroup_counts: List[int] = list(_DEMO_KEYGROUPS)

        self._program_headers: List[bytearray] = []
        self._keygroup_headers: Dict[int, List[bytearray]] = {}
        for index, name in enumerate(self._programs):
            header = _blank_header("program")
            self._write_named(header, "program", "PRNAME", name)
            self._write_named(header, "program", "PRGNUM", index)
            self._write_named(header, "program", "PMCHAN", index % 16)
            self._write_named(header, "program", "PRIORT", 1)
            self._write_named(header, "program", "PLAYLO", 21)
            self._write_named(header, "program", "PLAYHI", 127)
            self._write_named(header, "program", "GROUPS", self._keygroup_counts[index])
            self._program_headers.append(header)

            groups = []
            for kg in range(self._keygroup_counts[index]):
                kheader = _blank_header("keygroup")
                span = 127 // max(self._keygroup_counts[index], 1)
                self._write_named(kheader, "keygroup", "LONOTE", kg * span)
                self._write_named(kheader, "keygroup", "HINOTE", (kg + 1) * span)
                self._write_named(
                    kheader,
                    "keygroup",
                    "SNAME1",
                    self._samples[(index + kg) % len(self._samples)],
                )
                groups.append(kheader)
            self._keygroup_headers[index] = groups

        self._sample_headers: List[bytearray] = []
        for name in self._samples:
            header = _blank_header("sample")
            self._write_named(header, "sample", "SHNAME", name)
            self._sample_headers.append(header)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _write_named(raw: bytearray, region: str, name: str, value) -> None:
        param = p.lookup((region, name))
        raw[param.offset : param.end] = p.encode_field(param, value)

    def _headers_for(self, region: str, index: int, selector: int) -> bytearray:
        if region == "program":
            store = self._program_headers
        elif region == "sample":
            store = self._sample_headers
        elif region == "keygroup":
            groups = self._keygroup_headers.get(index)
            if groups is None:
                raise DemoError(f"no program {index}")
            if not 0 <= selector < len(groups):
                raise DemoError(f"program {index} has no keygroup {selector}")
            return groups[selector]
        else:
            raise KeyError(f"unknown region {region!r}")
        if not 0 <= index < len(store):
            raise DemoError(f"no {region} {index}")
        return store[index]

    # -- the S3kBridge surface the application uses -------------------------

    def status(self, *, timeout: Optional[float] = None):
        from s3k.bridge import DeviceStatus

        used = sum(1 + len(g) for g in self._keygroup_headers.values())
        return DeviceStatus(
            m.Status(
                version_major=2,
                version_minor=0,
                max_blocks=1022,
                free_blocks=1022 - used - len(self._samples),
                max_words=8 * 1024 * 1024,
                free_words=8 * 1024 * 1024 - 512 * len(self._samples),
                exclusive_channel_setting=self.exclusive_channel,
            )
        )

    def is_connected(self, *, timeout: float = 1.0) -> bool:
        return not self.closed

    def program_list(self, *, timeout: Optional[float] = None) -> List[str]:
        return list(self._programs)

    def sample_list(self, *, timeout: Optional[float] = None) -> List[str]:
        return list(self._samples)

    def keygroup_count(self, program: int) -> int:
        """Not part of the real bridge -- a convenience the demo can answer.

        On hardware this is the GROUPS field of the program header, and the
        application reads it from there; the demo keeps it alongside so the
        two cannot drift.
        """
        return len(self._keygroup_headers.get(program, []))

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
        raw = self._headers_for(region, index, selector)
        if offset + count > len(raw):
            raise DemoError(
                f"read of {count} bytes at offset {offset} runs past the "
                f"{len(raw)}-byte {region} header"
            )
        return bytes(raw[offset : offset + count])

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
        raw = self._headers_for(region, index, selector)
        if offset + len(data) > len(raw):
            raise DemoError(
                f"write of {len(data)} bytes at offset {offset} runs past the "
                f"{len(raw)}-byte {region} header"
            )
        raw[offset : offset + len(data)] = data
        # Keep the name lists in step, the way a real machine would: the list
        # commands read the same storage the header write just changed.
        if region == "program":
            self._programs[index] = self._read_named(raw, "program", "PRNAME")
        elif region == "sample":
            self._samples[index] = self._read_named(raw, "sample", "SHNAME")

    @staticmethod
    def _read_named(raw: bytearray, region: str, name: str):
        param = p.lookup((region, name))
        return p.decode_field(param, bytes(raw[param.offset : param.end]))

    def get_parameter(
        self,
        param,
        index: int,
        *,
        keygroup: int = 0,
        region: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        param = param if isinstance(param, p.Parameter) else p.lookup(param, region)
        raw = self.get_header_bytes(
            param.region,
            index,
            param.offset,
            param.size,
            selector=keygroup if param.region == "keygroup" else 0,
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
        param = param if isinstance(param, p.Parameter) else p.lookup(param, region)
        if not param.writable:
            why = "read-only" if param.readonly else "an internal block address"
            raise ValueError(f"{param.name} is {why} and must not be written")
        self.set_header_bytes(
            param.region,
            index,
            param.offset,
            p.encode_field(param, value),
            selector=keygroup if param.region == "keygroup" else 0,
        )

    def get_header(
        self,
        region: str,
        index: int,
        *,
        keygroup: int = 0,
        timeout: Optional[float] = None,
    ) -> Dict[str, object]:
        params = p.region_params(region)
        extent = max(x.end for x in params)
        raw = self.get_header_bytes(
            region, index, 0, extent, selector=keygroup if region == "keygroup" else 0
        )
        return {x.name: p.decode_field(x, raw[x.offset : x.end]) for x in params}

    # -- destructive --------------------------------------------------------

    def delete_program(self, program: int, *, confirm: bool = True) -> None:
        """DESTRUCTIVE on hardware; here it only edits this instance."""
        if not 0 <= program < len(self._programs):
            raise DemoError(f"no program {program}")
        del self._programs[program]
        del self._program_headers[program]
        del self._keygroup_counts[program]
        self._keygroup_headers = {
            (k if k < program else k - 1): v
            for k, v in self._keygroup_headers.items()
            if k != program
        }

    def delete_keygroup(
        self, program: int, keygroup: int, *, confirm: bool = True
    ) -> None:
        """DESTRUCTIVE on hardware; here it only edits this instance."""
        groups = self._keygroup_headers.get(program)
        if groups is None or not 0 <= keygroup < len(groups):
            raise DemoError(f"no keygroup {keygroup} in program {program}")
        del groups[keygroup]
        self._keygroup_counts[program] = len(groups)
        self._write_named(
            self._program_headers[program], "program", "GROUPS", len(groups)
        )

    def delete_sample(self, sample: int, *, confirm: bool = True) -> None:
        """DESTRUCTIVE on hardware; here it only edits this instance."""
        if not 0 <= sample < len(self._samples):
            raise DemoError(f"no sample {sample}")
        del self._samples[sample]
        del self._sample_headers[sample]

    def set_exclusive_channel(self, channel: int) -> None:
        self.exclusive_channel = channel

    def close(self) -> None:
        self.closed = True
