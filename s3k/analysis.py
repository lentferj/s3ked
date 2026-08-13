# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The cross-reference model -- reverse sample usage, dangling references and
# orphan detection -- follows the sibling eosed project's eosed/app.py, which
# is GPL-2.0-or-later. The Akai specifics are this project's: EOS voices
# reference a sample by NUMBER and Akai keygroup zones reference one by NAME,
# which changes what a dangling reference even is.
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
"""Who uses this sample, and what points at nothing.

The question this answers is not academic here. A load that exceeds free
memory reports "insufficient waveform memory" **once** and then behaves as
though all is well (§73), leaving programs resident and selectable whose
samples never arrived. They play silence. Nothing on the machine
distinguishes them from a program that is merely quiet, and the panel will
not tell you either.

Two differences from the sibling eosed's version of this, both from the
Akai's own model:

**Zones reference samples by name, not by number.** EOS keeps
``E4_GEN_SAMPLE = N`` after sample N is erased, so a dangling reference there
is a number pointing at the device's "Empty Sample" placeholder. Here a zone
holds twelve characters, and a reference is dangling when no resident sample
carries that name. That makes the check a set membership rather than a
placeholder comparison -- and it makes *duplicate names* a real ambiguity,
since the machine enforces no uniqueness (§13a) and two samples sharing a
name cannot be told apart by a zone that names one of them.

**An unassigned zone holds twelve SPACES**, charset value 10, decoding to a
blank string. That is what the machine actually does, measured on a loaded
program: an unused zone reads ``[10]*12`` while the assigned zone beside it
holds a twelve-character name.

This module first assumed twelve *zero* bytes and tested against a fixture
that invented them, which passed nine synthetic tests and then reported 182
references to ``''`` on the first real bank. Zeros are still treated as
unassigned -- they are what an unwritten field would hold, and no harm comes
of accepting both -- but blank-after-decode is the check that matters.

The zero case carries its own trap, and it is why the blank check cannot
simply be "raw bytes are all zero". Index 0 of the Akai charset is the
character ``0`` (the §68 trap), so twelve zero bytes decode to
``000000000000`` -- *not* blank -- and ``encode_name("000000000000")`` is
likewise twelve zeros. A sample named entirely of zeroes and an unwritten
field are therefore the same bytes, and cannot be separated at all.

So: **blank after decoding, or all-zero, means unassigned.** If a resident
sample *is* named ``000000000000``, :attr:`Audit.indistinguishable` names it,
because no zone reference to it can then be told from an empty zone and any
usage count for it is a lower bound.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import messages as m
from . import params as p

__all__ = ["ZoneRef", "Audit", "collect"]

#: The four velocity zones of a keygroup, in order.
ZONE_FIELDS = ("SNAME1", "SNAME2", "SNAME3", "SNAME4")


@dataclass(frozen=True)
class ZoneRef:
    """One velocity zone that names a sample."""

    program: int
    program_name: str
    keygroup: int
    zone: int
    sample: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return (f"program {self.program} ({self.program_name}) "
                f"keygroup {self.keygroup} zone {self.zone}")


@dataclass
class Audit:
    """A cross-reference of what points at what, and what points at nothing."""

    references: List[ZoneRef] = field(default_factory=list)
    resident: List[str] = field(default_factory=list)
    #: Programs whose keygroup count could not be read, so were not walked.
    unread: List[Tuple[int, str]] = field(default_factory=list)
    #: Resident samples whose name encodes to twelve zero bytes, which is
    #: also what an unassigned zone holds. References to these cannot be
    #: distinguished from empty zones.
    indistinguishable: List[str] = field(default_factory=list)

    def _resident_set(self) -> set:
        return {name.strip() for name in self.resident}

    def dangling(self) -> List[ZoneRef]:
        """Zones naming a sample the machine does not hold.

        These are the silent ones. After a partial load this is the list of
        programs that will play nothing, and it is the only place that
        information exists.
        """
        held = self._resident_set()
        return [ref for ref in self.references if ref.sample.strip() not in held]

    def usage(self, sample: str) -> List[ZoneRef]:
        """Every zone naming ``sample``. The "who uses this" question."""
        want = sample.strip()
        return [ref for ref in self.references if ref.sample.strip() == want]

    def orphans(self) -> List[str]:
        """Resident samples that no zone names.

        Not a fault -- a librarian may keep samples no program uses yet, and
        the four built-in waveforms are orphans on a freshly booted machine.
        It is a memory question: an orphan is waveform memory nothing is
        playing.
        """
        used = {ref.sample.strip() for ref in self.references}
        return [name for name in self.resident if name.strip() not in used]

    def ambiguous(self) -> Dict[str, int]:
        """Sample names held more than once, with their counts.

        The machine enforces no name uniqueness (§13a), and a zone names a
        sample rather than numbering it, so a duplicated name is a reference
        that cannot be resolved to one sample. Rare, and worth saying out
        loud when it happens rather than silently picking the first.
        """
        counts: Dict[str, int] = {}
        for name in self.resident:
            key = name.strip()
            counts[key] = counts.get(key, 0) + 1
        return {name: n for name, n in counts.items() if n > 1}

    def programs_playing_silence(self) -> Dict[int, List[ZoneRef]]:
        """Dangling references grouped by program, worst first."""
        out: Dict[int, List[ZoneRef]] = {}
        for ref in self.dangling():
            out.setdefault(ref.program, []).append(ref)
        return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))

    def summary(self) -> str:
        """One paragraph, suitable for a status line or a CLI tail."""
        bad = self.dangling()
        parts = [
            f"{len(self.references)} zone reference(s) across "
            f"{len({r.program for r in self.references})} program(s)",
            f"{len(self.resident)} resident sample(s)",
        ]
        if bad:
            programs = len({r.program for r in bad})
            parts.append(
                f"{len(bad)} DANGLING in {programs} program(s) — these play "
                f"silence")
        else:
            parts.append("no dangling references")
        if self.orphans():
            parts.append(f"{len(self.orphans())} unused sample(s)")
        if self.ambiguous():
            parts.append(f"{len(self.ambiguous())} duplicated sample name(s)")
        if self.unread:
            parts.append(f"{len(self.unread)} program(s) could not be read")
        if self.indistinguishable:
            parts.append(
                f"{len(self.indistinguishable)} sample name(s) encode to the "
                f"same bytes as an empty zone — usage counts for them are a "
                f"lower bound")
        return "; ".join(parts)


def _zone_name(bridge, program: int, keygroup: int, offset: int,
               timeout: Optional[float]) -> Optional[str]:
    """The zone's sample name, or ``None`` when nothing is assigned.

    Read as raw bytes rather than through ``get_parameter``, because the
    decoded text cannot distinguish "no sample" from a sample named
    ``000000000000`` -- twelve zero bytes decode to that string, not to
    blank.
    """
    raw = bridge.get_header_bytes("keygroup", program, offset,
                                  m.NAME_LENGTH, selector=keygroup,
                                  timeout=timeout)
    if not any(raw):
        return None          # unwritten; see the module docstring
    name = m.decode_name(list(raw))
    if not name.strip():
        return None          # twelve spaces: what the machine really stores
    return name


def collect(bridge, *, programs: Optional[Sequence[str]] = None,
            samples: Optional[Sequence[str]] = None,
            timeout: Optional[float] = None,
            progress=None) -> Audit:
    """Walk every keygroup of every program and build the cross-reference.

    Read-only. Four reads per keygroup plus one per program, so a bank of
    nine programs averaging four keygroups is about 150 round trips -- under
    two seconds at the measured ~94 reads/s.

    **The keygroup range comes from ``GROUPS`` and is not guessed.** Reading
    a keygroup past the end does not fail: the extended layer does not
    bounds-check, and returns the previous valid read's buffer instead
    (§11). A walk that trusted a fixed upper bound would therefore
    manufacture references by re-reading the last real keygroup, and they
    would look entirely plausible.
    """
    audit = Audit()
    audit.resident = list(samples if samples is not None
                          else bridge.sample_list(timeout=timeout))
    audit.indistinguishable = [name for name in audit.resident
                               if not any(m.encode_name(name.strip()))]
    names = list(programs if programs is not None
                 else bridge.program_list(timeout=timeout))
    groups_field = p.lookup(("program", "GROUPS"))
    offsets = [p.lookup(("keygroup", f)).offset for f in ZONE_FIELDS]

    for index, program_name in enumerate(names):
        try:
            count = int(bridge.get_parameter(groups_field, index,
                                             timeout=timeout))
        except Exception:
            audit.unread.append((index, program_name))
            continue
        for keygroup in range(count):
            for zone, offset in enumerate(offsets, start=1):
                try:
                    name = _zone_name(bridge, index, keygroup, offset, timeout)
                except Exception:
                    continue
                if name is None:
                    continue
                audit.references.append(
                    ZoneRef(program=index, program_name=program_name,
                            keygroup=keygroup, zone=zone, sample=name))
        if progress is not None:
            progress(index + 1, len(names))
    return audit
