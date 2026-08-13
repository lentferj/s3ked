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

#: Named by a demo keygroup but never resident, so the audit has something
#: to find. Invented, like every other name here.
_MISSING_SAMPLE = "TINE HARD C3"

#: How many keygroups each demo program has, by program index.
_DEMO_KEYGROUPS: List[int] = [2, 1, 4, 1, 3]

#: Multi parts on the S2000/S3000XL/S3200XL: "Sixteen multi parts are provided."
MULTI_PARTS = 16


def _blank_header(region: str) -> bytearray:
    """A header with every field at a plausible value rather than all zeroes.

    All-zero headers make a UI look broken in a way real ones never do, and
    hide off-by-one bugs in the offset table because every byte reads the
    same. Seeding each field with its own minimum spreads distinct values
    across the span.
    """
    raw = bytearray(p.region_size(region))
    for param in p.region_params(region):
        if param.kind == "text":
            raw[param.offset : param.end] = bytes(
                m.encode_name("", param.size)
            )
            continue
        value = param.default if param.default is not None else param.minimum
        if value < 0:
            value = 0
        if param.is_array:
            # An array field needs one value per element, not one value. The
            # refusal in encode_field caught this the moment TEMPER stopped
            # being modelled as a scalar -- which is what it is for.
            value = [value] * param.elements
        try:
            raw[param.offset : param.end] = p.encode_field(param, value)
        except (ValueError, TypeError):
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
                # The key range starts at 21 (A1), not 0 -- splitting 0..127
                # produced a LONOTE the real field cannot hold, which went
                # unnoticed for as long as encode_field only range-checked
                # display-offset parameters.
                count = max(self._keygroup_counts[index], 1)
                span = max((127 - 21) // count, 1)
                lo = 21 + kg * span
                hi = min(21 + (kg + 1) * span, 127) if kg + 1 < count else 127
                self._write_named(kheader, "keygroup", "LONOTE", min(lo, 127))
                self._write_named(kheader, "keygroup", "HINOTE", hi)
                # A blank header leaves HIVEL1 at 0, which means "this zone
                # can never sound" -- velocity 0 is note-off. Every zone on
                # every real bank read so far is 0..127, so the demo says so
                # too; without this the demo's deliberate dangling reference
                # is correctly suppressed as unreachable and the integrity
                # check has nothing to show.
                self._write_named(kheader, "keygroup", "LOVEL1", 0)
                self._write_named(kheader, "keygroup", "HIVEL1", 127)
                # The last keygroup of the last program names a sample that
                # is deliberately NOT resident, so the demo has one dangling
                # reference to show. That is the state a load which ran out
                # of memory leaves behind: the program is resident and
                # selectable, and the zone plays silence with nothing on the
                # machine to say so (§73). A demo where nothing is ever wrong
                # cannot demonstrate the check that finds it.
                last = (index == len(self._keygroup_counts) - 1
                        and kg == self._keygroup_counts[index] - 1)
                self._write_named(
                    kheader,
                    "keygroup",
                    "SNAME1",
                    _MISSING_SAMPLE if last
                    else self._samples[(index + kg) % len(self._samples)],
                )
                groups.append(kheader)
            self._keygroup_headers[index] = groups

        self._sample_headers: List[bytearray] = []
        for name in self._samples:
            header = _blank_header("sample")
            self._write_named(header, "sample", "SHNAME", name)
            self._sample_headers.append(header)

        # Multi mode: one file header plus 16 parts, as on the XL family.
        self._multi_header = _blank_header("multi")
        self._write_named(self._multi_header, "multi", "MULTINAME", "DEMO MULTI")
        self._multi_parts: List[bytearray] = []
        for part in range(MULTI_PARTS):
            raw = _blank_header("multipart")
            self._write_named(
                raw, "multipart", "PRNAME", self._programs[part % len(self._programs)]
            )
            self._write_named(raw, "multipart", "PMCHAN", part)
            self._write_named(raw, "multipart", "PRIORT", 1)
            self._write_named(raw, "multipart", "PLAYLO", 21)
            self._write_named(raw, "multipart", "PLAYHI", 127)
            self._multi_parts.append(raw)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _write_named(raw: bytearray, region: str, name: str, value) -> None:
        param = p.lookup((region, name))
        raw[param.offset : param.end] = p.encode_field(param, value)

    def _headers_for(self, region: str, index: int, selector: int) -> bytearray:
        if region == "multi":
            return self._multi_header
        if region == "multipart":
            if not 0 <= index < len(self._multi_parts):
                raise DemoError(f"no multi part {index}")
            return self._multi_parts[index]
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

    def volume_list(self, *, limit: int = 512,
                    timeout: Optional[float] = None):
        """A disk that looks like a disk, without being anyone's disk.

        Invented names, deliberately: a real machine's volume list carries the
        titles of whatever commercial library was loaded onto it, and those do
        not belong in a fixture. What a demo needs is the SHAPE -- a boot
        volume, a run of numbered ones, enough of them to make the pane scroll
        -- and none of that requires a real title.
        """
        from s3k.bridge import _Volume

        names = ["BOOT SYSTEM", "STARTUP 01", "STARTUP 02"]
        names += [f"WORK VOL{i:02d}" for i in range(3, 34)]
        return [_Volume(index=i, name=n, kind=3) for i, n in enumerate(names)]

    def trigger_load(self, load_type: int = 1, *, timeout: Optional[float] = None):
        """The demo loads instantly and adds, as the machine does."""
        self._loaded = True

    #: Mirrors of the real bridge's tables. The app reads these off whichever
    #: bridge it was handed, so the demo has to carry them too -- app.py must
    #: not import s3k.bridge, which pulls in rtmidi.
    DEVICE_TYPES = {0: "FLOPPY", 1: "HARD", 2: "FLASH"}
    MODES = {0: "SINGLE", 8: "GLOBAL", 10: "LOAD"}

    #: SINGLE, which is where the machine comes up.
    _mode = 0

    def clear_memory(self, *, timeout: Optional[float] = None):
        samples, programs = len(self._samples), len(self._programs)
        self._samples = []
        # the machine refuses to delete the last program, so the demo does too
        self._programs = self._programs[:1]
        return {"samples": samples, "programs": max(0, programs - 1),
                "samples_left": 0, "programs_left": len(self._programs)}

    def mode(self, *, timeout: Optional[float] = None) -> int:
        return self._mode

    def select_mode(self, mode: int, *, timeout: Optional[float] = None) -> int:
        self._mode = mode
        return self._mode

    _scsi_drive_id = 4
    _device_type = 1

    def select_drive(self, scsi_id: int, *, timeout: Optional[float] = None):
        self._scsi_drive_id = scsi_id
        return self.load_source()

    def select_device(self, kind: int, *, timeout: Optional[float] = None):
        self._device_type = kind
        return self.load_source()

    def load_source(self, *, timeout: Optional[float] = None):
        return {"scsi_drive_id": self._scsi_drive_id, "scsi_local_id": 6,
                "device_type": self._device_type,
                "partition": getattr(self, "_partition", 0), "volume": 1}

    def select_partition(self, partition: int, *, timeout: Optional[float] = None):
        self._partition = max(0, min(7, int(partition)))
        return self.load_source()

    def hd_directory(self, kind: int = 1, *, limit: int = 512,
                     timeout: Optional[float] = None):
        """An invented directory, for the same reason the volumes are invented.

        A real one lists whatever commercial library the disk holds. A fixture
        needs the SHAPE -- programs first, then the samples they use, with the
        selector acting as a starting point rather than a filter.
        """
        from s3k import bridge as b, messages as m
        from s3k.bridge import _DirectoryEntry

        # the demo's partitions differ from each other, so stepping through
        # them in the pane visibly does something
        letter = chr(65 + getattr(self, "_partition", 0))
        programs = [f"{letter} DEEP BASS", f"{letter} GLASS PAD", "SOFT KEYS"]
        samples = [f"BASS C{i}" for i in range(1, 5)]
        samples += [f"PAD C{i}" for i in range(1, 4)]
        names = (programs + samples) if kind <= 1 else samples
        n_prog = len(programs) if kind <= 1 else 0

        def record(i, name):
            # A real record: type, then a three-byte file size. Without a size
            # the pane's "will it fit" check reads 0.00 MB and tells the user
            # nothing, which is worse than not showing it.
            is_prog = i < n_prog
            kind_byte = b.ITEM_PROGRAM if is_prog else b.ITEM_SAMPLE
            size = 900 if is_prog else (380_000 + (i * 47_000) % 300_000)
            return (bytes(m.encode_name(name, 12)) + b"\x20\x20\x20\x20"
                    + bytes([kind_byte]) + int(size).to_bytes(3, "little")
                    + b"\x00\x00\x1e\x09")

        return [_DirectoryEntry(index=i, name=n, raw=record(i, n))
                for i, n in enumerate(names)]

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
        from s3k.bridge import _selector_for

        raw = self.get_header_bytes(
            param.region,
            index,
            param.offset,
            param.size,
            selector=_selector_for(param.region, keygroup),
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
        from s3k.bridge import _selector_for

        self.set_header_bytes(
            param.region,
            index,
            param.offset,
            p.encode_field(param, value),
            selector=_selector_for(param.region, keygroup),
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
        from s3k.bridge import _selector_for

        raw = self.get_header_bytes(
            region, index, 0, extent, selector=_selector_for(region, keygroup)
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
