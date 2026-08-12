# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# The parameter names, byte offsets, field sizes, ranges and descriptions in
# the table below are transcribed as data from Akai's own
# "S2800/S3000/S3200 MIDI System Exclusive Extensions". See LICENSE and
# docs/RESOLUTION_NOTES.md §1 for provenance -- including the fact that the
# text transcribed from is itself a third-party hand transcription of a
# printed document. The structure of this module follows the sibling eosed
# project's eos/params.py, GPL-2.0-or-later.
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

"""What the header bytes mean: the Program, Keygroup and Sample header maps.

The S3000 extended operations (:mod:`s3k.messages`, ops ``0x27``-``0x38``)
address structures by *byte offset*, so a parameter here is a
``(region, offset, size)`` span rather than the flat parameter id the sibling
eosed project uses. Three regions exist, each a 192-byte header:

===========  =======  ====================================================
region       entries  addressed by
===========  =======  ====================================================
``program``       84  program number
``keygroup``     132  program number + keygroup number
``sample``        35  sample number
===========  =======  ====================================================

**Nothing in this table has been verified against hardware.** It is a
transcription of a transcription -- see :mod:`s3k` docs and DISCLAIMER.md --
and a wrong offset here writes to the wrong parameter on a real machine. The
one guard that exists is structural, in ``tests/test_params.py``: no two
spans in a region may overlap, and none may run past the end of its header.
That catches transposition slips; it cannot catch a span that is wrong in the
source.

Where the specification's range text did not reduce cleanly to a numeric
span, the original wording is preserved verbatim in ``notes`` rather than
discarded, because that prose frequently carries the part that matters --
which value means OFF, which model the range applies to, what a "0" is really
reserved for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "Parameter",
    "REGIONS",
    "PRIMARY_REGIONS",
    "HEADER_SIZE",
    "REGION_SIZES",
    "region_size",
    "PARAMETERS",
    "PARAMETERS_BY_NAME",
    "lookup",
    "region_params",
    "group_params",
    "groups",
    "describe_value",
    "note_name",
    "decode_field",
    "encode_field",
]

#: The byte-addressable structures, in the order a UI should show them.
#:
#: ``multi`` and ``multipart`` come from the *S2000/S3000XL/S3200XL* document
#: and exist only on that sub-family -- multi mode is the headline feature of
#: those machines ("a major change over the S3000 family, intended to help
#: with multi-timbral operation"). On a plain S3000 there is no multi file.
REGIONS: Tuple[str, ...] = ("program", "keygroup", "sample", "multi", "multipart")

#: The regions a bare parameter name resolves against by default.
#:
#: The multi file is a separate address space on a separate structure, and it
#: reuses a dozen program-header field names (PRIORT, PANPOS, PLAYLO, …)
#: because a multi part *is* a program header. Treating those as ambiguous
#: would make ``lookup("PRIORT")`` unusable for the common case; treating the
#: multi regions as opt-in keeps the guard where it is actually needed --
#: genuine collisions *within* the primary structures, like RESERVED.
PRIMARY_REGIONS: Tuple[str, ...] = ("program", "keygroup", "sample")

#: "Program, keygroup and Sample headers have been extended to 192 bytes."
HEADER_SIZE = 192

#: Byte length of each structure.
#:
#: The multi file's two sections are the awkward ones: the S2000 document
#: gives their field offsets but never states a total size. ``multi`` is
#: therefore bounded at its own known extent, and ``multipart`` at 192 because
#: it demonstrably mirrors the program header (see REGION_SIZES' note below
#: and RESOLUTION_NOTES §8).
REGION_SIZES: Dict[str, int] = {
    "program": HEADER_SIZE,
    "keygroup": HEADER_SIZE,
    "sample": HEADER_SIZE,
    "multi": 32,
    "multipart": HEADER_SIZE,
}


#: What a modulation *source* byte means, from the S2800/S3000/S3200 document's
#: "Values used to represent Modulation Sources".
#:
#: Transcribed 2026-08-10 after these fields were read off hardware as bare
#: numbers and **0 was misread as a live routing** in a handoff to the sibling
#: mpc2emu project. 0 is "No Source" -- the slot is off. A field whose meaning
#: lives in a separate table in the source document is exactly the field that
#: gets misread when the table is not carried with it.
#:
#: The "!" sources are the instantaneous value sampled at note-on rather than
#: tracked continuously.
MOD_SOURCES: Dict[int, str] = {
    0: "no source",
    1: "modwheel",
    2: "bend",
    3: "pressure",
    4: "external",
    5: "velocity",
    6: "key",
    7: "LFO1",
    8: "LFO2",
    9: "env1",
    10: "env2",
    11: "!modwheel",
    12: "!bend",
    13: "!external",
    14: "env3",
}


def region_size(region: str) -> int:
    """Byte length of *region*'s structure."""
    try:
        return REGION_SIZES[region]
    except KeyError:
        raise KeyError(f"unknown region {region!r}; expected one of {REGIONS}") from None


@dataclass(frozen=True)
class Parameter:
    """One field in one of the three headers.

    ``kind`` distinguishes the three things a span can be:

    * ``"num"`` -- an ordinary numeric field; ``minimum``/``maximum`` apply.
    * ``"text"`` -- a name, in the device's own character set, not ASCII
      (see :data:`s3k.messages.AKAI_CHARSET`). Range fields are meaningless.
    * ``"address"`` -- a block address or absolute memory location the
      specification marks "internal use". Readable, but writing one is a good
      way to corrupt the machine's own data structures; treat as read-only
      whatever ``readonly`` says.
    """

    region: str
    offset: int
    name: str
    size: int
    group: str
    minimum: int
    maximum: int
    kind: str = "num"
    unit: Optional[str] = None
    values: Optional[Dict[int, str]] = None
    default: Optional[int] = None
    readonly: bool = False
    display_offset: int = 0
    """Added to the stored byte to get the number the front panel shows.

    A few fields store an index where the panel shows a count, so the two
    differ by a constant. ``POLYPH`` stores 0-31 for a polyphony the machine
    displays as 1-32 -- confirmed on hardware 2026-08-10, RESOLUTION_NOTES
    §11 Finding H.

    :func:`describe_value` adds it and :func:`encode_field` subtracts it, so
    everything a user reads or types is the panel's number and the stored
    form never leaves this module. ``minimum``/``maximum`` are always the
    *stored* range.
    """
    desc: Optional[str] = None
    notes: Optional[str] = None
    elements: int = 1
    """How many independent values the span holds, when it is an ARRAY.

    ``TEMPER`` is twelve bytes -- one per semitone of the octave, each a
    detune in cents -- and modelling it as a single twelve-byte integer is not
    merely imprecise, it corrupts. ``encode_field`` would write one number
    across the whole span, so a temperament of -5 cents on C became
    ``FB FF FF FF FF FF FF FF FF FF FF FF``: C at -5 and **every other note at
    -1**. Reading it back gave one meaningless large integer.

    With ``elements`` set, the span is ``elements`` values of
    ``size // elements`` bytes each, ``minimum``/``maximum`` apply to each
    ELEMENT, and encode/decode work in sequences. A scalar passed to an array
    field is refused rather than broadcast, because broadcasting is exactly
    the behaviour that made this a silent corruption instead of an error.
    """
    models: Optional[str] = None
    """Which machines have this field, when it is not the whole family.

    Set from the specification's own sub-headings -- a handful of fields are
    introduced under "S2000/S3000XL/S3200XL Parameters" and do not exist on a
    plain S2800/S3000/S3200.
    """

    @property
    def key(self) -> Tuple[str, int]:
        """The identity a caller addresses this parameter by."""
        return (self.region, self.offset)

    @property
    def end(self) -> int:
        """One past the last byte this parameter occupies."""
        return self.offset + self.size

    @property
    def element_size(self) -> int:
        """Bytes per element. Equals ``size`` for an ordinary scalar field."""
        return self.size // self.elements

    @property
    def is_array(self) -> bool:
        return self.elements > 1

    @property
    def writable(self) -> bool:
        return not self.readonly and self.kind != "address"


def _p(
    region: str,
    offset: int,
    name: str,
    size: int,
    group: str,
    minimum: int,
    maximum: int,
    *,
    kind: str = "num",
    unit: Optional[str] = None,
    values: Optional[Dict[int, str]] = None,
    default: Optional[int] = None,
    readonly: bool = False,
    display_offset: int = 0,
    elements: int = 1,
    desc: Optional[str] = None,
    notes: Optional[str] = None,
    models: Optional[str] = None,
) -> Parameter:
    if size % elements:
        raise ValueError(
            f"{name}: {size} bytes does not divide into {elements} elements"
        )
    return Parameter(
        region=region,
        offset=offset,
        name=name,
        size=size,
        group=group,
        minimum=minimum,
        maximum=maximum,
        kind=kind,
        unit=unit,
        values=values,
        default=default,
        readonly=readonly,
        display_offset=display_offset,
        elements=elements,
        desc=desc,
        notes=notes,
        models=models,
    )


_PARAMS: List[Parameter] = [
    # -- PROGRAM HEADER -------------------------------------------------------
    _p(
        "program",
        0,
        "PRIDENT",
        1,
        "program.general",
        1,
        1,
        readonly=True,
        desc="Block identifier",
        notes="Not in the source document, which starts the program header at "
              "offset 1. Added from hardware 2026-08-10: every program block "
              "read from an S3000XL carries 0x01 here, matching the keygroup's "
              "KGIDENT (0x02) and the sample's SHIDENT (0x03), both of which "
              "the document does list. See RESOLUTION_NOTES §14.",
    ),
    _p(
        "program",
        1,
        "KGRP1@",
        2,
        "program.general",
        0,
        16383,
        kind="address",
        desc="Block address of first keygroup (internal use)",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "program",
        3,
        "PRNAME",
        12,
        "program.general",
        0,
        0,
        kind="text",
        desc="Name of program",
    ),
    _p(
        "program",
        15,
        "PRGNUM",
        1,
        "program.general",
        0,
        128,
        desc="MIDI program number After sending data to this parameter, Miscellaneous function BTSORT should be triggered to resort the list of programs into order and to flag active programs.",
    ),
    _p(
        "program",
        16,
        "PMCHAN",
        1,
        "program.midi",
        0,
        255,
        values={255: "OMNI"},
        desc="MIDI channel",
        notes="range as written: \"255 signifies OMNI, 0 to 15 indicate MIDI channel\"",
    ),
    _p(
        "program",
        17,
        "POLYPH",
        1,
        "program.midi",
        0,
        31,
        display_offset=1,
        desc="Depth of polyphony",
        notes="range as written: \"0 to 31 (these represent polyphony values of 1 to 32)\"",
    ),
    _p(
        "program",
        18,
        "PRIORT",
        1,
        "program.midi",
        0,
        3,
        values={0: "low", 1: "norm", 2: "high", 3: "hold"},
        desc="Priority of voices playing this program",
        notes="range as written: \"0=low, 1=norm, 2=high, 3=hold\"",
    ),
    _p(
        "program",
        19,
        "PLAYLO",
        1,
        "program.midi",
        21,
        127,
        desc="Lower limit of play range",
        notes="range as written: \"21 to 127 represents A1 to G8\"",
    ),
    _p(
        "program",
        20,
        "PLAYHI",
        1,
        "program.midi",
        21,
        127,
        desc="Upper limit of play range",
        notes="range as written: \"21 to 127 represents A1 to G8\"",
    ),
    _p(
        "program",
        21,
        "OSHIFT",
        1,
        "program.output",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        22,
        "OUTPUT",
        1,
        "program.output",
        0,
        255,
        values={255: "off"},
        desc="Individual output routing. This parameter also controls send to effects section.",
        notes="range as written: \"255 indicates OFF On S3200: 0 to 7 indicates outputs 1 to 8, 8 indicates FX, 9 indicates RVB and 10 indicates R+F. On S3000: 0 to 7 indicates outputs 1 to 8, 8 indicates FX. On S2800: 0 and 1 indicates outputs 1 and 2, 2 indicates FX.\"",
    ),
    _p(
        "program",
        23,
        "STEREO",
        1,
        "program.output",
        0,
        99,
        desc="Left and right output levels",
    ),
    _p(
        "program",
        24,
        "PANPOS",
        1,
        "program.pan",
        -50,
        50,
        desc="Balance between left and right outputs",
    ),
    _p(
        "program",
        25,
        "PRLOUD",
        1,
        "program.output",
        0,
        99,
        desc="Basic loudness of this program",
    ),
    _p(
        "program",
        26,
        "V_LOUD",
        1,
        "program.pan",
        -50,
        50,
        desc="Note-on velocity dependence of loudness",
    ),
    _p(
        "program",
        27,
        "K_LOUD",
        1,
        "program.pan",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        28,
        "P_LOUD",
        1,
        "program.pan",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p("program", 29, "PANRAT", 1, "program.pan", 0, 99, desc="Speed of LFO2"),
    _p("program", 30, "PANDEP", 1, "program.pan", 0, 99, desc="Depth of LFO2"),
    _p(
        "program",
        31,
        "PANDEL",
        1,
        "program.pan",
        0,
        99,
        desc="Delay in growth of LFO2",
    ),
    _p(
        "program",
        32,
        "K_PANP",
        1,
        "program.pan",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p("program", 33, "LFORAT", 1, "program.lfo", 0, 99, desc="Speed of LFO1"),
    _p("program", 34, "LFODEP", 1, "program.lfo", 0, 99, desc="Depth of LFO1"),
    _p(
        "program",
        35,
        "LFODEL",
        1,
        "program.lfo",
        0,
        99,
        desc="Delay in growth of LFO1",
    ),
    _p(
        "program",
        36,
        "MWLDEP",
        1,
        "program.lfo",
        0,
        99,
        desc="Amount of control of LFO1 depth by Modwheel",
    ),
    _p(
        "program",
        37,
        "PRSDEP",
        1,
        "program.lfo",
        0,
        99,
        desc="Amount of control of LFO1 depth by Aftertouch",
    ),
    _p(
        "program",
        38,
        "VELDEP",
        1,
        "program.lfo",
        0,
        99,
        desc="Amount of control of LFO1 depth by Note-On velocity",
    ),
    _p(
        "program",
        39,
        "B_PTCH",
        1,
        "program.pitch",
        0,
        24,
        unit="semitones",
        desc="Range of increase of Pitch by bendwheel",
        notes="range as written: \"0 to 24 semitones\"",
    ),
    _p(
        "program",
        40,
        "P_PTCH",
        1,
        "program.pitch",
        -12,
        12,
        unit="semitones",
        desc="Amount of control of Pitch by Pressure",
        notes="range as written: \"-12 to +12 semitones\"",
    ),
    _p(
        "program",
        41,
        "KXFADE",
        1,
        "program.pitch",
        0,
        1,
        values={0: "OFF", 1: "ON"},
        desc="Keygroup crossfade enable",
        notes="range as written: \"0 represents OFF, 1 represents ON\"",
    ),
    _p(
        "program",
        42,
        "GROUPS",
        1,
        "program.general",
        1,
        99,
        readonly=True,
        desc="Number of keygroups. To change the number of keygroups in a program, the KDATA and DELK commands should be used.",
        notes="range as written: \"1 to 99 (Read-only)\"; read-only",
    ),
    _p(
        "program",
        43,
        "TPNUM",
        1,
        "program.general",
        0,
        127,
        desc="Temporary program number (internal use)",
    ),
    _p(
        "program",
        44,
        "TEMPER",
        12,
        "program.pitch",
        -50,
        50,
        unit="cents",
        elements=12,
        desc="Key temperament C, C#, D, D# etc.",
        notes="range as written: \"-50 to +50 cents\" -- and that range is PER "
              "SEMITONE. Twelve independent signed bytes, one for each note of "
              "the octave starting at C, not one twelve-byte number. Modelled "
              "as a scalar until 2026-08-12, which meant writing -5 cents "
              "stored FB FF FF FF FF FF FF FF FF FF FF FF: C at -5 and every "
              "other note at -1. The only field in this table with this shape. "
              "RESOLUTION_NOTES §66.",
    ),
    _p(
        "program",
        56,
        "ECHOUT",
        1,
        "program.midi",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        57,
        "MW_PAN",
        1,
        "program.pan",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        58,
        "COHERE",
        1,
        "program.midi",
        1,
        1,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        59,
        "DESYNC",
        1,
        "program.midi",
        0,
        1,
        values={0: "OFF", 1: "ON"},
        desc="Enable de-synchronisation of LFO1 across notes",
        notes="range as written: \"0 represents OFF, 1 represents ON\"",
    ),
    _p(
        "program",
        60,
        "PLAW",
        1,
        "program.midi",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        61,
        "VASSOQ",
        1,
        "program.midi",
        0,
        1,
        values={0: "OLDEST", 1: "QUIETEST"},
        desc="Criterion by which voices are stolen",
        notes="range as written: \"0 represents OLDEST, 1 represents QUIETEST\"",
    ),
    _p(
        "program",
        62,
        "SPLOUD",
        1,
        "program.output",
        0,
        99,
        desc="Reduction in loudness due to soft pedal",
    ),
    _p(
        "program",
        63,
        "SPATT",
        1,
        "program.output",
        0,
        99,
        desc="Stretch of attack due to soft pedal",
    ),
    _p(
        "program",
        64,
        "SPFILT",
        1,
        "program.output",
        0,
        99,
        desc="Reduction of filter frequency due to soft pedal",
    ),
    _p(
        "program",
        65,
        "PTUNO",
        2,
        "program.pitch",
        -12800,
        12800,
        desc="Tuning offset of program",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "program",
        67,
        "K_LRAT",
        1,
        "program.lfo",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        68,
        "K_LDEP",
        1,
        "program.lfo",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        69,
        "K_LDEL",
        1,
        "program.lfo",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        70,
        "VOSCL",
        1,
        "program.midi",
        0,
        99,
        desc="Level sent to Individual outputs/effects",
    ),
    _p(
        "program",
        71,
        "VSSCL",
        1,
        "program.midi",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "program",
        72,
        "LEGATO",
        1,
        "program.midi",
        0,
        1,
        values={0: "OFF", 1: "ON"},
        desc="Mono legato mode enable",
        notes="range as written: \"0 represents OFF, 1 represents ON\"",
    ),
    _p(
        "program",
        73,
        "B_PTCHD",
        1,
        "program.pitch",
        0,
        12,
        unit="semitones",
        desc="Range of decrease of Pitch by bendwheel",
        notes="range as written: \"0 to 12 semitones\"",
    ),
    _p(
        "program",
        74,
        "B_MODE",
        1,
        "program.pitch",
        0,
        1,
        values={0: "NORMAL", 1: "HELD"},
        desc="Bending of held notes",
        notes="range as written: \"0 represents NORMAL mode, 1 represents HELD mode\"",
    ),
    _p(
        "program",
        75,
        "TRANSPOSE",
        1,
        "program.midi",
        -50,
        50,
        unit="semitones",
        desc="Shift pitch of incoming MIDI Values used to represent Modulation Sources 0: No Source 1: Modwheel 2: Bend 3: Pressure",
        notes="range as written: \"-50 to +50 semitones\"",
    ),
    _p(
        "program",
        76,
        "MODSPAN1",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="First source of assignable modulation of pan position",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        77,
        "MODSPAN2",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Second source of assignable modulation of pan",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        78,
        "MODSPAN3",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Third source of assignable modulation of pan",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        79,
        "MODSAMP1",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="First source of assignable modulation of loudness",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        80,
        "MODSAMP2",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Second source of assignable modulation of loudness",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        81,
        "MODSLFOT",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Source of assignable modulation of LFO1 speed",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        82,
        "MODSLFOL",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Source of assignable modulation of LFO1 depth",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        83,
        "MODSLFOD",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Source of assignable modulation of LFO1 delay",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        84,
        "MODSFILT1",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="First source of assignable modulation of filter frequency",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        85,
        "MODSFILT2",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Second source of assignable modulation of filter frequency",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        86,
        "MODSFILT3",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Third source of assignable modulation of filter frequency",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        87,
        "MODSPITCH",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Source of assignable modulation of pitch",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        88,
        "MODSAMP3",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Third source of assignable modulation of loudness",
        notes="range as written: \"See \"Values used to represent Modulation Sources\" above\"",
    ),
    _p(
        "program",
        89,
        "MODVPAN1",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of pan by assignable source 1",
    ),
    _p(
        "program",
        90,
        "MODVPAN2",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of pan by assignable source 2",
    ),
    _p(
        "program",
        91,
        "MODVPAN3",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of pan by assignable source 3",
    ),
    _p(
        "program",
        92,
        "MODVAMP1",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of loudness by assignable source 1",
    ),
    _p(
        "program",
        93,
        "MODVAMP2",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of loudness by assignable source 2",
    ),
    _p(
        "program",
        94,
        "MODVLFOR",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of LFO1 speed",
    ),
    _p(
        "program",
        95,
        "MODVLVOL",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of LFO1 depth",
    ),
    _p(
        "program",
        96,
        "MODVLFOD",
        1,
        "program.mods",
        -50,
        50,
        desc="Amount of control of LFO1 delay",
    ),
    _p(
        "program",
        97,
        "LFO1WAVE",
        1,
        "program.lfo",
        0,
        255,
        desc="LFO1 waveform",
        notes="range as written: \"0 represents Triangle, 1 represents Sawtooth, 2 represents Square\". MEASURED and confirmed on hardware (RESOLUTION_NOTES §46) by reading the pitch track, which IS the waveform since LFO1 drives pitch. Value 3 is an undocumented FOURTH shape: symmetric like a triangle but spending half as long near its centre, so neither triangle nor square. Not identified.",
    ),
    _p(
        "program",
        98,
        "LFO2WAVE",
        1,
        "program.lfo",
        0,
        255,
        desc="LFO2 waveform",
        notes="range as written: \"0 represents Triangle, 1 represents Sawtooth, 2 represents Square\"",
    ),
    _p(
        "program",
        99,
        "MODSLFLT2_1",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="First source of assignable modulation of filter 2 frequency (only used on S3200).",
        notes="range as written: \"See \"Values used to represent Modulation sources\" above\"",
    ),
    _p(
        "program",
        100,
        "MODSLFLT2_2",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Second source of assignable modulation of filter 2 frequency (only used on S3200).",
        notes="range as written: \"See \"Values used to represent Modulation sources\" above\"",
    ),
    _p(
        "program",
        101,
        "MODSLFLT2_3",
        1,
        "program.mods",
        0,
        255,
        values=MOD_SOURCES,
        desc="Third source of assignable modulation of filter 2 frequency (only used on S3200).",
        notes="range as written: \"See \"Values used to represent Modulation sources\" above\"",
    ),
    _p(
        "program",
        102,
        "LFO2TRIG",
        1,
        "program.lfo",
        0,
        255,
        desc="Retrigger mode for LFO2",
    ),
    _p(
        "program",
        103,
        "RESERVED",
        7,
        "program.general",
        0,
        72057594037927935,
        desc="Not used",
    ),
    _p(
        "program",
        110,
        "PORTIME",
        1,
        "program.portamento",
        0,
        255,
        desc="PORTAMENTO TIME",
    ),
    _p(
        "program",
        111,
        "PORTYPE",
        1,
        "program.portamento",
        0,
        255,
        desc="PORTAMENTO TYPE",
    ),
    _p(
        "program",
        112,
        "PORTEN",
        1,
        "program.portamento",
        0,
        255,
        desc="PORTAMENTO ON/OFF",
    ),
    _p(
        "program",
        113,
        "PFXCHAN",
        1,
        "program.output",
        0,
        4,
        desc="Effects Bus Select 0 = OFF 1 = FX1 2 = FX2 3 = RV3 4 = RV4",
        models="S2000/S3000XL/S3200XL",
    ),
    _p("program", 114, "PFXSLEV", 1, "program.output", 0, 99, desc="Not used", models="S2000/S3000XL/S3200XL"),

    # -- KEYGROUP HEADER ------------------------------------------------------
    _p(
        "keygroup",
        0,
        "KGIDENT",
        1,
        "keygroup.general",
        2,
        2,
        desc="Block identifier (internal use)",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        1,
        "NXTKG@",
        2,
        "keygroup.general",
        0,
        16383,
        kind="address",
        desc="Next keygroup block address (internal use)",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "keygroup",
        3,
        "LONOTE",
        1,
        "keygroup.general",
        21,
        127,
        desc="Lower limit of keyrange",
        notes="range as written: \"21 to 127 represents A1 to G8\"",
    ),
    _p(
        "keygroup",
        4,
        "HINOTE",
        1,
        "keygroup.general",
        21,
        127,
        desc="Upper limit of keyrange",
        notes="range as written: \"21 to 127 represents A1 to G8\"",
    ),
    _p(
        "keygroup",
        5,
        "KGTUNO",
        2,
        "keygroup.pitch",
        -12800,
        12800,
        desc="Keygroup tuning offset",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "keygroup",
        7,
        "FILFRQ",
        1,
        "keygroup.filter",
        0,
        99,
        desc="Basic filter frequency",
    ),
    _p(
        "keygroup",
        8,
        "K_FREQ",
        1,
        "keygroup.filter",
        0,
        12,
        unit="semitones",
        desc="Key follow of filter frequency",
        notes="range as written: \"0 to 12 semitones\"",
    ),
    _p(
        "keygroup",
        9,
        "V_FREQ",
        1,
        "keygroup.filter",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        10,
        "P_FREQ",
        1,
        "keygroup.filter",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        11,
        "E_FREQ",
        1,
        "keygroup.filter",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        12,
        "ATTAK1",
        1,
        "keygroup.env.1",
        0,
        99,
        desc="Attack rate of envelope 1",
    ),
    _p(
        "keygroup",
        13,
        "DECAY1",
        1,
        "keygroup.env.1",
        0,
        99,
        desc="Decay rate of envelope 1",
    ),
    _p(
        "keygroup",
        14,
        "SUSTN1",
        1,
        "keygroup.env.1",
        0,
        99,
        desc="Sustain level of envelope 1",
    ),
    _p(
        "keygroup",
        15,
        "RELSE1",
        1,
        "keygroup.env.1",
        0,
        99,
        desc="Release rate of envelope 1",
    ),
    _p(
        "keygroup",
        16,
        "V_ATT1",
        1,
        "keygroup.env.1",
        -50,
        50,
        desc="Note-on velocity dependence of envelope 1 attack rate",
    ),
    _p(
        "keygroup",
        17,
        "V_REL1",
        1,
        "keygroup.env.1",
        -50,
        50,
        desc="Note-on velocity dependence of envelope 1 release rate",
    ),
    _p(
        "keygroup",
        18,
        "O_REL1",
        1,
        "keygroup.env.1",
        -50,
        50,
        desc="Note-off velocity dependence of envelope 1 release rate",
    ),
    _p(
        "keygroup",
        19,
        "K_DAR1",
        1,
        "keygroup.env.1",
        -50,
        50,
        desc="Dependence of envelope 2 decay and release rates on key",
    ),
    _p(
        "keygroup",
        20,
        "ATTAK2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Attack rate of envelope 2",
        notes="also called ENV2R1 in later OS versions",
    ),
    _p(
        "keygroup",
        21,
        "DECAY2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Decay rate of envelope 2",
        notes="also called ENV2R3 in later OS versions",
    ),
    _p(
        "keygroup",
        22,
        "SUSTN2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Sustain level of envelope 2",
        notes="also called ENV2L3 in later OS versions",
    ),
    _p(
        "keygroup",
        23,
        "RELSE2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Release rate of envelope 2",
        notes="also called ENV2R4 in later OS versions",
    ),
    _p(
        "keygroup",
        24,
        "V_ATT2",
        1,
        "keygroup.env.2",
        -50,
        50,
        desc="Dependence of envelope 2 attack on note-on velocity",
    ),
    _p(
        "keygroup",
        25,
        "V_REL2",
        1,
        "keygroup.env.2",
        -50,
        50,
        desc="Dependence of envelope 2 release on note-on velocity",
    ),
    _p(
        "keygroup",
        26,
        "O_REL2",
        1,
        "keygroup.env.2",
        -50,
        50,
        desc="Dependence of envelope 2 release on note-off velocity",
    ),
    _p(
        "keygroup",
        27,
        "K_DAR2",
        1,
        "keygroup.env.2",
        -50,
        50,
        desc="Dependence of envelope 2 decay and release rates on key",
    ),
    _p(
        "keygroup",
        28,
        "V_ENV2",
        1,
        "keygroup.env.2",
        -50,
        50,
        desc="Scaling of envelope 2 by note-on velocity",
    ),
    _p(
        "keygroup",
        29,
        "E_PTCH",
        1,
        "keygroup.pitch",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        30,
        "VXFADE",
        1,
        "keygroup.general",
        0,
        1,
        values={0: "OFF", 1: "ON"},
        desc="Velocity zone crossfade",
        notes="range as written: \"0 represents OFF, 1 represents ON\"",
    ),
    _p(
        "keygroup",
        31,
        "VZONES",
        1,
        "keygroup.general",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        32,
        "LKXF",
        1,
        "keygroup.general",
        0,
        255,
        desc="Calculated left keygroup crossfade factor (internal)",
    ),
    _p(
        "keygroup",
        33,
        "RKXF",
        1,
        "keygroup.general",
        0,
        255,
        desc="Calculated right keygroup crossfade factor (internal) Velocity zone 1",
    ),
    _p(
        "keygroup",
        34,
        "SNAME1",
        12,
        "keygroup.zone.1",
        0,
        0,
        kind="text",
        desc="Sample name used in velocity zone 1",
    ),
    _p(
        "keygroup",
        46,
        "LOVEL1",
        1,
        "keygroup.zone.1",
        0,
        127,
        desc="Lower limit of velocity range",
    ),
    _p(
        "keygroup",
        47,
        "HIVEL1",
        1,
        "keygroup.zone.1",
        0,
        127,
        desc="Upper limit of velocity range",
    ),
    _p(
        "keygroup",
        48,
        "VTUNO1",
        2,
        "keygroup.zone.1",
        -12800,
        12800,
        desc="Velocity zone 1 tuning offset",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "keygroup",
        50,
        "VLOUD1",
        1,
        "keygroup.zone.1",
        -50,
        50,
        desc="Velocity zone 1 loudness offset",
    ),
    _p(
        "keygroup",
        51,
        "VFREQ1",
        1,
        "keygroup.zone.1",
        -50,
        50,
        desc="Velocity zone 1 filter frequency offset",
    ),
    _p(
        "keygroup",
        52,
        "VPANO1",
        1,
        "keygroup.zone.1",
        -50,
        50,
        desc="Velocity zone 1 pan offset",
    ),
    _p(
        "keygroup",
        53,
        "ZPLAY1",
        1,
        "keygroup.zone.1",
        0,
        4,
        values={0: "As sample", 1: "Loop in release", 2: "Loop til release", 3: "No loops", 4: "Play to sample end"},
        desc="Type of sample playback in velocity zone 1",
        notes="range as written: \"0 = As sample 1 = Loop in release 2 = Loop til release 3 = No loops 4 = Play to sample end\"",
    ),
    _p(
        "keygroup",
        54,
        "LVXF1",
        1,
        "keygroup.zone.1",
        0,
        255,
        desc="Low velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        55,
        "HVXF1",
        1,
        "keygroup.zone.1",
        0,
        255,
        desc="High velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        56,
        "SBADD1",
        2,
        "keygroup.zone.1",
        0,
        16383,
        kind="address",
        desc="Calculated sample header block address (internal) ;Velocity zone 2",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "keygroup",
        58,
        "SNAME2",
        12,
        "keygroup.zone.2",
        0,
        0,
        kind="text",
        desc="Sample name used in velocity zone 2",
    ),
    _p(
        "keygroup",
        70,
        "LOVEL2",
        1,
        "keygroup.zone.2",
        0,
        127,
        desc="Lower limit of velocity range 2",
    ),
    _p(
        "keygroup",
        71,
        "HIVEL2",
        1,
        "keygroup.zone.2",
        0,
        127,
        desc="Upper limit of velocity range 2",
    ),
    _p(
        "keygroup",
        72,
        "VTUNO2",
        2,
        "keygroup.zone.2",
        -12800,
        12800,
        desc="Velocity zone 2 tuning offset",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "keygroup",
        74,
        "VLOUD2",
        1,
        "keygroup.zone.2",
        -50,
        50,
        desc="Velocity zone 2 loudness offset",
    ),
    _p(
        "keygroup",
        75,
        "VFREQ2",
        1,
        "keygroup.zone.2",
        -50,
        50,
        desc="Velocity zone 2 filter frequency offset",
    ),
    _p(
        "keygroup",
        76,
        "VPANO2",
        1,
        "keygroup.zone.2",
        -50,
        50,
        desc="Velocity zone 2 pan offset",
    ),
    _p(
        "keygroup",
        77,
        "ZPLAY2",
        1,
        "keygroup.zone.2",
        0,
        4,
        values={0: "As sample", 1: "Loop in release", 2: "Loop til release", 3: "No loops", 4: "Play to sample end"},
        desc="Type of sample playback in velocity zone 2",
        notes="range as written: \"0 = As sample 1 = Loop in release 2 = Loop til release 3 = No loops 4 = Play to sample end\"",
    ),
    _p(
        "keygroup",
        78,
        "LVXF2",
        1,
        "keygroup.zone.2",
        0,
        255,
        desc="Low velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        79,
        "HVXF2",
        1,
        "keygroup.zone.2",
        0,
        255,
        desc="High velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        80,
        "SBADD2",
        2,
        "keygroup.zone.2",
        0,
        16383,
        kind="address",
        desc="Calculated sample header block address (internal) ;Velocity zone 3",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "keygroup",
        82,
        "SNAME3",
        12,
        "keygroup.zone.3",
        0,
        0,
        kind="text",
        desc="Sample name used in velocity zone 3",
    ),
    _p(
        "keygroup",
        94,
        "LOVEL3",
        1,
        "keygroup.zone.3",
        0,
        127,
        desc="Lower limit of velocity range 3",
    ),
    _p(
        "keygroup",
        95,
        "HIVEL3",
        1,
        "keygroup.zone.3",
        0,
        127,
        desc="Upper limit of velocity range 3",
    ),
    _p(
        "keygroup",
        96,
        "VTUNO3",
        2,
        "keygroup.zone.3",
        -12800,
        12800,
        desc="Velocity zone 3 tuning offset",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "keygroup",
        98,
        "VLOUD3",
        1,
        "keygroup.zone.3",
        -50,
        50,
        desc="Velocity zone 3 loudness offset",
    ),
    _p(
        "keygroup",
        99,
        "VFREQ3",
        1,
        "keygroup.zone.3",
        -50,
        50,
        desc="Velocity zone 3 filter frequency offset",
    ),
    _p(
        "keygroup",
        100,
        "VPANO3",
        1,
        "keygroup.zone.3",
        -50,
        50,
        desc="Velocity zone 3 pan offset",
    ),
    _p(
        "keygroup",
        101,
        "ZPLAY3",
        1,
        "keygroup.zone.3",
        0,
        4,
        values={0: "As sample", 1: "Loop in release", 2: "Loop til release", 3: "No loops", 4: "Play to sample end"},
        desc="Type of sample playback in velocity zone 3",
        notes="range as written: \"0 = As sample 1 = Loop in release 2 = Loop til release 3 = No loops 4 = Play to sample end\"",
    ),
    _p(
        "keygroup",
        102,
        "LVXF3",
        1,
        "keygroup.zone.3",
        0,
        255,
        desc="Low velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        103,
        "HVXF3",
        1,
        "keygroup.zone.3",
        0,
        255,
        desc="High velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        104,
        "SBADD3",
        2,
        "keygroup.zone.3",
        0,
        16383,
        kind="address",
        desc="Calculated sample header block address (internal) ;Velocity zone 4",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "keygroup",
        106,
        "SNAME4",
        12,
        "keygroup.zone.4",
        0,
        0,
        kind="text",
        desc="Sample name used in velocity zone 4",
    ),
    _p(
        "keygroup",
        118,
        "LOVEL4",
        1,
        "keygroup.zone.4",
        0,
        127,
        desc="Lower limit of velocity range 4",
    ),
    _p(
        "keygroup",
        119,
        "HIVEL4",
        1,
        "keygroup.zone.4",
        0,
        127,
        desc="Upper limit of velocity range 4",
    ),
    _p(
        "keygroup",
        120,
        "VTUNO4",
        2,
        "keygroup.zone.4",
        -12800,
        12800,
        desc="Velocity zone 4 tuning offset",
        notes="range as written: \"-50.00 to +50.00 (fraction is binary)\" -- those are SEMITONES, and the raw unit is 1/256 of one. Declared 0..50 here until 2026-08-12, which was the document's display range transcribed as a value range and 256x too narrow: it makes 19.53 cents the largest detune the field can express. Measured by writing raw values and reading the PITCH back -- 256 gave +99.8 cents, 512 +199.8, 1280 +499.9, 2560 +999.9, 5120 +1999.9, and the negatives match to 0.3 cents. Every value round-tripped exactly, negatives as two's complement. Beyond +-5120 (+-20 semitones) the scale is unverified: the pitch detector tops out, and whether the sampler itself will transpose that far is untested. RESOLUTION_NOTES §56.",
    ),
    _p(
        "keygroup",
        122,
        "VLOUD4",
        1,
        "keygroup.zone.4",
        -50,
        50,
        desc="Velocity zone 4 loudness offset",
    ),
    _p(
        "keygroup",
        123,
        "VFREQ4",
        1,
        "keygroup.zone.4",
        -50,
        50,
        desc="Velocity zone 4 filter frequency offset",
    ),
    _p(
        "keygroup",
        124,
        "VPANO4",
        1,
        "keygroup.zone.4",
        -50,
        50,
        desc="Velocity zone 4 pan offset",
    ),
    _p(
        "keygroup",
        125,
        "ZPLAY4",
        1,
        "keygroup.zone.4",
        0,
        4,
        values={0: "As sample", 1: "Loop in release", 2: "Loop til release", 3: "No loops", 4: "Play to sample end"},
        desc="Type of sample playback in velocity zone 4",
        notes="range as written: \"0 = As sample 1 = Loop in release 2 = Loop til release 3 = No loops 4 = Play to sample end\"",
    ),
    _p(
        "keygroup",
        126,
        "LVXF4",
        1,
        "keygroup.zone.4",
        0,
        255,
        desc="Low velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        127,
        "HVXF4",
        1,
        "keygroup.zone.4",
        0,
        255,
        desc="High velocity crossfade factor (internal use)",
    ),
    _p(
        "keygroup",
        128,
        "SBADD4",
        2,
        "keygroup.zone.4",
        0,
        16383,
        kind="address",
        desc="Calculated sample header block address (internal) ;Keygroup common",
        notes="range as written: \"Block address\"",
    ),
    _p(
        "keygroup",
        130,
        "KBEAT",
        1,
        "keygroup.general",
        -50,
        50,
        desc="Fixed rate detune",
    ),
    _p(
        "keygroup",
        131,
        "AHOLD",
        1,
        "keygroup.general",
        0,
        1,
        values={0: "OFF", 1: "ON"},
        desc="Remain in attack phase until first loop encountered ;More Zone stuff",
        notes="range as written: \"0 represents OFF, 1 represents ON\"",
    ),
    _p(
        "keygroup",
        132,
        "CP1",
        1,
        "keygroup.zone.1",
        0,
        1,
        values={0: "TRACK", 1: "CONST"},
        desc="Constant pitch flag for velocity zone 1",
        notes="range as written: \"0 represents TRACK, 1 represents CONST\"",
    ),
    _p(
        "keygroup",
        133,
        "CP2",
        1,
        "keygroup.zone.2",
        0,
        1,
        values={0: "TRACK", 1: "CONST"},
        desc="Constant pitch flag for velocity zone 2",
        notes="range as written: \"0 represents TRACK, 1 represents CONST\"",
    ),
    _p(
        "keygroup",
        134,
        "CP3",
        1,
        "keygroup.zone.3",
        0,
        1,
        values={0: "TRACK", 1: "CONST"},
        desc="Constant pitch flag for velocity zone 3",
        notes="range as written: \"0 represents TRACK, 1 represents CONST\"",
    ),
    _p(
        "keygroup",
        135,
        "CP4",
        1,
        "keygroup.zone.4",
        0,
        1,
        values={0: "TRACK", 1: "CONST"},
        desc="Constant pitch flag for velocity zone 4",
        notes="range as written: \"0 represents TRACK, 1 represents CONST\"",
    ),
    _p(
        "keygroup",
        136,
        "VZOUT1",
        1,
        "keygroup.zone.1",
        0,
        10,
        desc="Individual output offset for velocity zone 1",
        notes="range as written: \"0 to 10 for S3000, S3200; 0 to 4 for S2800\"",
    ),
    _p(
        "keygroup",
        137,
        "VZOUT2",
        1,
        "keygroup.zone.2",
        0,
        10,
        desc="Individual output offset for velocity zone 2",
        notes="range as written: \"0 to 10 for S3000, S3200; 0 to 4 for S2800\"",
    ),
    _p(
        "keygroup",
        138,
        "VZOUT3",
        1,
        "keygroup.zone.3",
        0,
        10,
        desc="Individual output offset for velocity zone 3",
        notes="range as written: \"0 to 10 for S3000, S3200; 0 to 4 for S2800\"",
    ),
    _p(
        "keygroup",
        139,
        "VZOUT4",
        1,
        "keygroup.zone.4",
        0,
        10,
        desc="Individual output offset for velocity zone 4",
        notes="range as written: \"0 to 10 for S3000, S3200; 0 to 4 for S2800\"",
    ),
    _p(
        "keygroup",
        140,
        "VSS1",
        2,
        "keygroup.zone.1",
        -9999,
        9999,
        desc="Start point dependence on note-on velocity for sample in velocity zone 1",
        notes="range as written: \"-9999 to +9999 data points\"",
    ),
    _p(
        "keygroup",
        142,
        "VSS2",
        2,
        "keygroup.zone.2",
        -9999,
        9999,
        desc="Start point dependence on note-on velocity for sample in velocity zone 2",
        notes="range as written: \"-9999 to +9999 data points\"",
    ),
    _p(
        "keygroup",
        144,
        "VSS3",
        2,
        "keygroup.zone.3",
        -9999,
        9999,
        desc="Start point dependence on note-on velocity for sample in velocity zone 3",
        notes="range as written: \"-9999 to +9999 data points\"",
    ),
    _p(
        "keygroup",
        146,
        "VSS4",
        2,
        "keygroup.zone.4",
        -9999,
        9999,
        desc="Start point dependence on note-on velocity for sample in velocity zone 4",
        notes="range as written: \"-9999 to +9999 data points\"",
    ),
    _p(
        "keygroup",
        148,
        "KV_LO",
        1,
        "keygroup.general",
        0,
        0,
        desc="Not used",
        notes="range as written: \"fixed value in the specification\"",
    ),
    _p(
        "keygroup",
        149,
        "FILQ",
        1,
        "keygroup.filter",
        0,
        15,
        desc="Resonance of filter 1",
    ),
    _p(
        "keygroup",
        150,
        "L_PTCH",
        1,
        "keygroup.pitch",
        -50,
        50,
        desc="Amount of control of pitch by LFO1",
    ),
    _p(
        "keygroup",
        151,
        "MODVFILT1",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of filter frequency by assignable source 1",
    ),
    _p(
        "keygroup",
        152,
        "MODVFILT2",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of filter frequency by assignable source 2",
    ),
    _p(
        "keygroup",
        153,
        "MODVFILT3",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of filter frequency by assignable source 3",
    ),
    _p(
        "keygroup",
        154,
        "MODVPITCH",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of pitch by assignable source",
    ),
    _p(
        "keygroup",
        155,
        "MODVAMP3",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of loudness by assignable keygroup source",
    ),
    _p(
        "keygroup",
        156,
        "ENV2L1",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Level of envelope 2 at end attack phase (phase 1)",
        notes="Level of envelope 2 at the end of phase 1. Envelope 2 is a FOUR-STAGE RATE/LEVEL envelope, exactly like envelope 3 -- the ADSR-flavoured names cover R1 (ATTAK2), R3 (DECAY2), L3 (SUSTN2) and R4 (RELSE2), and these four are the missing L1, R2, L2 and L4 of the same structure. Nothing here aliases anything: writing each of the eight and reading back all eight moves only the one written. RESOLUTION_NOTES §67.",
    ),
    _p(
        "keygroup",
        157,
        "ENV2R2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Rate during phase 2 of envelope 2",
        notes="Rate of phase 2 of envelope 2. Envelope 2 is a FOUR-STAGE RATE/LEVEL envelope, exactly like envelope 3 -- the ADSR-flavoured names cover R1 (ATTAK2), R3 (DECAY2), L3 (SUSTN2) and R4 (RELSE2), and these four are the missing L1, R2, L2 and L4 of the same structure. Nothing here aliases anything: writing each of the eight and reading back all eight moves only the one written. RESOLUTION_NOTES §67.",
    ),
    _p(
        "keygroup",
        158,
        "ENV2L2",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Level of envelope 2 at end of phase 1",
        notes="Level of envelope 2 at the end of phase 2. Envelope 2 is a FOUR-STAGE RATE/LEVEL envelope, exactly like envelope 3 -- the ADSR-flavoured names cover R1 (ATTAK2), R3 (DECAY2), L3 (SUSTN2) and R4 (RELSE2), and these four are the missing L1, R2, L2 and L4 of the same structure. Nothing here aliases anything: writing each of the eight and reading back all eight moves only the one written. RESOLUTION_NOTES §67.",
    ),
    _p(
        "keygroup",
        159,
        "ENV2L4",
        1,
        "keygroup.env.2",
        0,
        99,
        desc="Final envelope 2 level",
        notes="Level of envelope 2 at the end of phase 4. Envelope 2 is a FOUR-STAGE RATE/LEVEL envelope, exactly like envelope 3 -- the ADSR-flavoured names cover R1 (ATTAK2), R3 (DECAY2), L3 (SUSTN2) and R4 (RELSE2), and these four are the missing L1, R2, L2 and L4 of the same structure. Nothing here aliases anything: writing each of the eight and reading back all eight moves only the one written. RESOLUTION_NOTES §67.",
    ),
    _p(
        "keygroup",
        160,
        "KGMUTE",
        1,
        "keygroup.general",
        0,
        255,
        values={255: "off"},
        desc="Keygroup mute group",
        notes="range as written: \"0ffh = off, mute groups 0 to 31\"",
    ),
    _p(
        "keygroup",
        161,
        "KFXCHAN",
        1,
        "keygroup.output",
        0,
        5,
        desc="Keygroup override Effects Bus select 0 = PRG (use the global program header selection) 1 = OFF 2 = FX1 3 = FX2 4 = RV3 5 = RV4",
        notes="the specification documents this byte twice: earlier as PFXCHAN (with the program header's enumeration) and again here, with a leading \"0 = PRG\" that shifts every later value by one. The later definition is used. UNVERIFIED -- if the machine follows the earlier one, values read here are off by one",
    ),
    _p(
        "keygroup",
        162,
        "KFXSLEV",
        1,
        "keygroup.output",
        0,
        99,
        desc="Keygroup override Effects Send level",
        notes="the specification documents this byte twice: earlier as PFXSLEV (with the program header's enumeration) and again here, with a leading \"0 = PRG\" that shifts every later value by one. The later definition is used. UNVERIFIED -- if the machine follows the earlier one, values read here are off by one",
    ),
    _p(
        "keygroup",
        163,
        "RESERVED",
        5,
        "keygroup.general",
        0,
        1099511627775,
        desc="Not used",
    ),
    _p(
        "keygroup",
        168,
        "LSI2_ON",
        1,
        "keygroup.filter2",
        0,
        1,
        values={0: "-6dB", 1: "0dB"},
        desc="Route audio through second LSI",
        notes="range as written: \"0 = -6dB, 1 = 0dB\"",
    ),
    _p(
        "keygroup",
        169,
        "FLT2GAIN",
        1,
        "keygroup.filter2",
        0,
        1,
        values={0: "-6dB", 1: "0dB"},
        desc="Make-up gain of second filter",
        notes="range as written: \"0 = -6dB, 1 = 0dB\"",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        170,
        "FLT2MODE",
        1,
        "keygroup.filter2",
        0,
        3,
        values={0: "Low-pass", 1: "Band-pass", 2: "High-pass", 3: "EQ"},
        desc="Mode of second filter",
        notes="range as written: \"0 = Low-pass, 1 = Band-pass, 2 = High-pass, 3 = EQ\"",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        171,
        "FLT2Q",
        1,
        "keygroup.filter2",
        0,
        31,
        desc="Resonance of second filter",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        172,
        "TONEFREQ",
        1,
        "keygroup.filter2",
        0,
        99,
        desc="Center frequency of tone section",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        173,
        "TONESLOP",
        1,
        "keygroup.filter2",
        -50,
        50,
        desc="Slope of tone section",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        174,
        "MODVFLT2_1",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of second filter frequency by source 1",
    ),
    _p(
        "keygroup",
        175,
        "MODVFLT2_2",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of second filter frequency by source 2",
    ),
    _p(
        "keygroup",
        176,
        "MODVFLT2_3",
        1,
        "keygroup.mods",
        -50,
        50,
        desc="Amount of control of second filter frequency by source 3",
    ),
    _p(
        "keygroup",
        177,
        "FIL2FR",
        1,
        "keygroup.filter2",
        0,
        99,
        desc="Basic second filter frequency",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        178,
        "K_FRQ2",
        1,
        "keygroup.filter2",
        -24,
        24,
        unit="semitones",
        desc="Second filter key follow",
        notes="range as written: \"-24 to +24 semitones\"",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        179,
        "ENV3R1",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Attack rate of envelope 3",
        notes="also called ATTAK3 in later OS versions",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        180,
        "ENV3L1",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Final level of attack phase (phase 1) of envelope 3",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        181,
        "ENV3R2",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Rate of phase 2 of envelope 3",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        182,
        "ENV3L2",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Final level of phase 2 of envelope 3",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        183,
        "ENV3R3",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Rate of phase 3 of envelope 3",
        notes="also called DECAY3 in later OS versions",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        184,
        "ENV3L3",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Final level of phase 3 of envelope 3",
        notes="also called SUSTN3 in later OS versions",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        185,
        "ENV3R4",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Rate of release phase (phase 4) of envelope 3",
        notes="also called RELSE3 in later OS versions",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        186,
        "ENV3L4",
        1,
        "keygroup.env.3",
        0,
        99,
        desc="Final target level of envelope 3",
        models="S3200 (second LSI fitted as standard), or an S3000XL/S2000 with the optional IB304F filter board. The fields exist in the header on every model; without the board they do nothing -- the machine answers `2nd filter board IB304F not fitted!` at the panel. RESOLUTION_NOTES §19.",
    ),
    _p(
        "keygroup",
        187,
        "V_ATT3",
        1,
        "keygroup.env.3",
        -50,
        50,
        desc="Dependence of envelope 3 attack rate on note-on velocity",
    ),
    _p(
        "keygroup",
        188,
        "V_REL3",
        1,
        "keygroup.env.3",
        -50,
        50,
        desc="Dependence of envelope 3 release rate on note-on velocity",
    ),
    _p(
        "keygroup",
        189,
        "O_REL3",
        1,
        "keygroup.env.3",
        -50,
        50,
        desc="Dependence of envelope 3 release rate on note-off velocity",
    ),
    _p(
        "keygroup",
        190,
        "K_DAR3",
        1,
        "keygroup.env.3",
        -50,
        50,
        desc="Dependence of envelope 3 release and decay rate on key",
    ),
    _p(
        "keygroup",
        191,
        "V_ENV3",
        1,
        "keygroup.env.3",
        -50,
        50,
        desc="Scaling of envelope 3 by note-on velocity",
    ),

    # -- SAMPLE HEADER --------------------------------------------------------
    _p(
        "sample",
        0,
        "SHIDENT",
        1,
        "sample.general",
        0,
        255,
        desc="Block identifier",
        notes="range as written: \"3 (Fixed)\"",
    ),
    _p(
        "sample",
        1,
        "SBANDW",
        1,
        "sample.data",
        0,
        255,
        desc="Sample bandwidth",
        notes="range as written: \"0 represents 10kHz, 1 represents 20kHz\"",
    ),
    _p(
        "sample",
        2,
        "SPITCH",
        1,
        "sample.general",
        21,
        127,
        desc="Original pitch",
        notes="range as written: \"21 to 127 represents A1 to G8\"",
    ),
    _p(
        "sample",
        3,
        "SHNAME",
        12,
        "sample.general",
        0,
        0,
        kind="text",
        desc="Sample name",
    ),
    _p(
        "sample",
        15,
        "SSRVLD",
        1,
        "sample.general",
        0,
        255,
        desc="Sample rate validity",
        notes="range as written: \"0 indicates rate is invalid, 128 indicates rate is valid\"",
    ),
    _p("sample", 16, "SLOOPS", 1, "sample.loop", 0, 255, readonly=True,
        desc="Number of loops"),
    _p(
        "sample",
        17,
        "SALOOP",
        1,
        "sample.loop",
        0,
        255,
        readonly=True,
        desc="First active loop (internal use)",
    ),
    _p(
        "sample",
        18,
        "SHLOOP",
        1,
        "sample.loop",
        0,
        255,
        readonly=True,
        desc="Highest loop (internal use)",
    ),
    _p(
        "sample",
        19,
        "SPTYPE",
        1,
        "sample.general",
        0,
        3,
        values={0: "Normal looping", 1: "Loop until release", 2: "No looping", 3: "Play to sample end"},
        desc="Playback type",
        notes="range as written: \"0 = Normal looping 1 = Loop until release 2 = No looping 3 = Play to sample end\"",
    ),
    _p(
        "sample",
        20,
        "STUNO",
        2,
        "sample.general",
        0,
        65535,
        desc="Sample tuning offset cent:semi",
    ),
    _p(
        "sample",
        22,
        "SLOCAT",
        4,
        "sample.data",
        0,
        268435455,
        kind="address",
        desc="Absolute start address in memory of sample",
        notes="range as written: \"Absolute location in Wave memory\"",
    ),
    _p(
        "sample",
        26,
        "SLNGTH",
        4,
        "sample.data",
        0,
        4294967295,
        desc="Length of sample",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        30,
        "SSTART",
        4,
        "sample.data",
        0,
        4294967295,
        desc="Offset from start of sample from which playback commences",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        34,
        "SMPEND",
        4,
        "sample.data",
        0,
        4294967295,
        desc="Offset from start of sample from which playback ceases ;First Loop",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        38,
        "LOOPAT1",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Position in sample of first loop point",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        42,
        "LLNGTH1",
        6,
        "sample.loop",
        0,
        281474976710655,
        desc="First loop length",
    ),
    _p(
        "sample",
        48,
        "LDWELL1",
        2,
        "sample.loop",
        0,
        9999,
        unit="ms",
        values={0: "no loop", 9999: "hold"},
        desc="Dwell time of first loop ;Second Loop",
        notes="range as written: \"0 represents No Loop, 9999 = Hold, 1 to 9998 represents Dwell time in milliseconds\"",
    ),
    _p(
        "sample",
        50,
        "LOOPAT2",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Position in sample of second loop point",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        54,
        "LLNGTH2",
        6,
        "sample.loop",
        0,
        281474976710655,
        desc="Second loop length",
    ),
    _p(
        "sample",
        60,
        "LDWELL2",
        2,
        "sample.loop",
        0,
        9999,
        unit="ms",
        values={0: "no loop", 9999: "hold"},
        desc="Dwell time of second loop ;Third Loop",
        notes="range as written: \"0 represents No Loop, 9999 = Hold, 1 to 9998 represents Dwell time in milliseconds\"",
    ),
    _p(
        "sample",
        62,
        "LOOPAT3",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Position in sample of third loop point",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        66,
        "LLNGTH3",
        6,
        "sample.loop",
        0,
        281474976710655,
        desc="Third loop length",
    ),
    _p(
        "sample",
        72,
        "LDWELL3",
        2,
        "sample.loop",
        0,
        9999,
        unit="ms",
        values={0: "no loop", 9999: "hold"},
        desc="Dwell time of third loop ;Fourth Loop",
        notes="range as written: \"0 represents No Loop, 9999 = Hold, 1 to 9998 represents Dwell time in milliseconds\"",
    ),
    _p(
        "sample",
        74,
        "LOOPAT4",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Position in sample of fourth loop point",
        notes="range as written: \"Number of data points from start of sample\"",
    ),
    _p(
        "sample",
        78,
        "LLNGTH4",
        6,
        "sample.loop",
        0,
        281474976710655,
        desc="Fourth loop length",
    ),
    _p(
        "sample",
        84,
        "LDWELL4",
        2,
        "sample.loop",
        0,
        9999,
        unit="ms",
        values={0: "no loop", 9999: "hold"},
        desc="Dwell time of fourth loop",
        notes="range as written: \"0 represents No Loop, 9999 = Hold, 1 to 9998 represents Dwell time in milliseconds\"",
    ),
    _p(
        "sample",
        86,
        "SLXY1",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Relative loop factors for loop 1",
    ),
    _p(
        "sample",
        98,
        "SLXY2",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Relative loop factors for loop 2",
    ),
    _p(
        "sample",
        110,
        "SLXY3",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Relative loop factors for loop 3",
    ),
    _p(
        "sample",
        122,
        "SLXY4",
        4,
        "sample.loop",
        0,
        4294967295,
        desc="Relative loop factors for loop 4",
    ),
    _p("sample", 134, "SSPARE", 1, "sample.general", 0, 255, readonly=True,
        desc="Used internally"),
    _p("sample", 135, "SWCOMM", 1, "sample.general", 0, 255, desc="Not used"),
    _p(
        "sample",
        136,
        "SSPAIR",
        2,
        "sample.general",
        0,
        16383,
        kind="address",
        desc="Address of stereo partner (internal use)",
        notes="range as written: \"Block address\"",
    ),
    _p("sample", 138, "SSRATE", 2, "sample.data", 0, 65535, desc="Sample rate"),
    _p(
        "sample",
        140,
        "SHLTO",
        1,
        "sample.general",
        -50,
        50,
        desc="Tuning offset of hold loop Frank Neumann, February 5th, 2002",
    ),
    # -- MULTI FILE HEADER ----------------------------------------------------
    # From the S2000/S3000XL/S3200XL document. Selector 0 of RMULTIDATA/
    # MULTIDATA; the item index is unused (reserved) for this section.
    # "This header currently holds little useful information" -- the spec's own
    # assessment, and it is right: a name, four effect assignments, a filename.
    _p("multi", 3, "MULTINAME", 12, "multi.general", 0, 0, kind="text",
        models="S2000/S3000XL/S3200XL", desc="The filename of the multi file"),
    _p("multi", 16, "FX1", 1, "multi.effects", 0, 255, models="S2000/S3000XL/S3200XL",
        desc="The fx setup assigned to fx channel 1"),
    _p("multi", 17, "FX2", 1, "multi.effects", 0, 255, models="S2000/S3000XL/S3200XL",
        desc="The fx setup assigned to fx channel 2"),
    _p("multi", 18, "FX3", 1, "multi.effects", 0, 255, models="S2000/S3000XL/S3200XL",
        desc="The fx setup assigned to fx channel 3"),
    _p("multi", 19, "FX4", 1, "multi.effects", 0, 255, models="S2000/S3000XL/S3200XL",
        desc="The fx setup assigned to fx channel 4"),
    _p("multi", 20, "FXFILENAME", 12, "multi.general", 0, 0, kind="text",
        models="S2000/S3000XL/S3200XL", desc="The filename of the associated fx file"),

    # -- MULTI PART -----------------------------------------------------------
    # Selector 1; the item index is the multi part number (0-15).
    #
    # Every offset here matches the program header's field of the same name --
    # all twelve of them, across two independently transcribed documents. That
    # is the strongest cross-check this project has (RESOLUTION_NOTES §8): a
    # multi part IS a program header, with only a subset of fields meaningful.
    _p("multipart", 3, "PRNAME", 12, "multipart.general", 0, 0, kind="text",
        readonly=True, models="S2000/S3000XL/S3200XL",
        desc="Name of the program used for this multi part. To assign programs "
             "to parts it is better to use MIDI program change commands",
        notes="read-only"),
    _p("multipart", 16, "PMCHAN", 1, "multipart.midi", 0, 255,
        values={255: "OMNI"}, models="S2000/S3000XL/S3200XL",
        desc="MIDI channel this part responds to, irrespective of part number",
        notes='range as written: "255 signifies OMNI, 0 to 15 indicate MIDI channel"'),
    _p("multipart", 18, "PRIORT", 1, "multipart.midi", 0, 3,
        values={0: "low", 1: "norm", 2: "high", 3: "hold"}, models="S2000/S3000XL/S3200XL",
        desc="Priority of voices playing this part"),
    _p("multipart", 19, "PLAYLO", 1, "multipart.midi", 21, 127, models="S2000/S3000XL/S3200XL",
        desc="Lower limit of play range"),
    _p("multipart", 20, "PLAYHI", 1, "multipart.midi", 21, 127, models="S2000/S3000XL/S3200XL",
        desc="Upper limit of play range"),
    _p("multipart", 22, "OUTPUT", 1, "multipart.output", 0, 255, models="S2000/S3000XL/S3200XL",
        desc="Individual output routing",
        notes="the source leaves this Range field blank (OCR reads \"Rsngs:\"); "
              "see the program header's OUTPUT for the model-dependent meanings"),
    _p("multipart", 23, "STEREO", 1, "multipart.output", 0, 99, models="S2000/S3000XL/S3200XL",
        desc="Left and right output levels"),
    _p("multipart", 24, "PANPOS", 1, "multipart.output", -50, 50, models="S2000/S3000XL/S3200XL",
        desc="Balance between left and right outputs"),
    _p("multipart", 70, "VOSCL", 1, "multipart.output", 0, 99, models="S2000/S3000XL/S3200XL",
        desc="Level sent to individual outputs"),
    _p("multipart", 75, "TRANSPOSE", 1, "multipart.midi", -50, 50,
        unit="semitones", models="S2000/S3000XL/S3200XL", desc="Shift pitch of incoming MIDI"),
    _p("multipart", 113, "PFXCHAN", 1, "multipart.output", 0, 4,
        values={0: 'OFF', 1: 'FX1', 2: 'FX2', 3: 'RV3', 4: 'RV4'}, models="S2000/S3000XL/S3200XL", desc="Effects bus select"),
    _p("multipart", 114, "PFXSLEV", 1, "multipart.output", 0, 99, models="S2000/S3000XL/S3200XL",
        desc="Effects send level"),
    _p("multipart", 115, "PTUNOCM", 1, "multipart.pitch", -50, 50, unit="cents",
        models="S2000/S3000XL/S3200XL", desc="Tune offset in cents, used in MULTI mode only"),

]

#: Every parameter, keyed by ``(region, offset)``.
PARAMETERS: Dict[Tuple[str, int], Parameter] = {p.key: p for p in _PARAMS}

#: Every parameter, keyed by ``(region, NAME)``.
#:
#: Names are not unique across regions -- PFXCHAN and PFXSLEV appear in both
#: the program and keygroup headers, and Reserved appears in both too -- so
#: the region has to be part of the key. :func:`lookup` accepts a bare name
#: and resolves it when it is unambiguous.
PARAMETERS_BY_NAME: Dict[Tuple[str, str], Parameter] = {
    (p.region, p.name): p for p in _PARAMS
}

_BY_BARE_NAME: Dict[str, List[Parameter]] = {}
for _p_ in _PARAMS:
    _BY_BARE_NAME.setdefault(_p_.name, []).append(_p_)
del _p_


def lookup(ref, region: Optional[str] = None) -> Parameter:
    """Find a parameter by ``(region, offset)``, ``(region, name)``, or name.

    A bare name is resolved only when it is unambiguous; where one is not,
    the error names the regions it appears in rather than picking one, since
    guessing here means reading or writing the wrong header.
    """
    if isinstance(ref, tuple):
        if len(ref) != 2:
            raise ValueError(f"expected a (region, offset|name) pair, got {ref!r}")
        first, second = ref
        if isinstance(second, int):
            try:
                return PARAMETERS[(first, second)]
            except KeyError:
                raise KeyError(
                    f"no parameter at offset {second} in the {first} header"
                ) from None
        try:
            return PARAMETERS_BY_NAME[(first, second.upper())]
        except KeyError:
            raise KeyError(f"no parameter {second!r} in the {first} header") from None

    name = str(ref).upper()
    if region is not None:
        return lookup((region, name))
    matches = _BY_BARE_NAME.get(name)
    if not matches:
        raise KeyError(f"no parameter named {name!r}")
    # Primary structures first; the multi regions are opt-in (see
    # PRIMARY_REGIONS for why).
    primary = [x for x in matches if x.region in PRIMARY_REGIONS]
    candidates = primary or matches
    if len(candidates) > 1:
        where = ", ".join(sorted(x.region for x in candidates))
        raise KeyError(
            f"{name!r} is ambiguous -- it exists in the {where} structures; "
            f"pass region= to choose"
        )
    return candidates[0]


def region_params(region: str) -> List[Parameter]:
    """Every parameter in *region*, in offset order."""
    if region not in REGIONS:
        raise KeyError(f"unknown region {region!r}; expected one of {REGIONS}")
    return sorted(
        (p for p in _PARAMS if p.region == region), key=lambda p: p.offset
    )


def group_params(prefix: str) -> List[Parameter]:
    """Every parameter whose dotted group starts with *prefix*.

    Groups are dotted (``"keygroup.env.1"``, ``"program.mods"``) precisely so a
    UI can ask for a whole branch -- ``group_params("keygroup")`` is every
    keygroup parameter, ``group_params("keygroup.zone")`` is all four zones.
    """
    prefix = prefix.rstrip(".")
    return sorted(
        (p for p in _PARAMS if p.group == prefix or p.group.startswith(prefix + ".")),
        key=lambda p: (p.region, p.offset),
    )


def groups(region: Optional[str] = None) -> List[str]:
    """Distinct group names, optionally limited to one region."""
    return sorted(
        {p.group for p in _PARAMS if region is None or p.region == region}
    )


# --- value presentation ----------------------------------------------------

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: Parameters whose value is a MIDI note number.
#:
#: RESOLVED from documentation; see RESOLUTION_NOTES §4. The S2800/S3000/S3200
#: document words these as "21 to 127 represents A1 to G8", which cannot hold
#: at both ends -- but that is simply a dropped minus sign. The
#: S2000/S3000XL/S3200XL document writes the same field as **"21 to 127
#: represents A-1 to G8"**, and the S3000XL owner's manual shows the front
#: panel rendering keyspans as ``C_0`` … ``G_8``.
#:
#: All three agree on one offset: octave = value // 12 - 2, so note 21 is
#: ``A-1``, note 24 is ``C0``, middle C (60) is ``C3``, and note 127 is ``G8``.
_NOTE_VALUED = {
    ("program", "PLAYLO"),
    ("program", "PLAYHI"),
    ("keygroup", "LONOTE"),
    ("keygroup", "HINOTE"),
    ("multipart", "PLAYLO"),
    ("multipart", "PLAYHI"),
}


def note_name(value: int) -> str:
    """MIDI note number to the name the front panel shows, e.g. 21 -> ``A-1``.

    See :data:`_NOTE_VALUED` for the three sources that pin the octave offset.
    """
    return f"{_NOTE_NAMES[value % 12]}{value // 12 - 2}"


def decode_field(param: Parameter, data: bytes) -> object:
    """Turn the raw bytes of *param*'s span into a Python value.

    Text fields decode through the device's character set; everything else is
    an unsigned little-endian integer of the field's width. Signedness is
    *not* inferred here -- see :func:`describe_value`.

    Sign-extends a field whose stated range goes negative, and applies
    ``display_offset``, so this is the exact inverse of :func:`encode_field`
    in both directions. Getting that wrong is not cosmetic: an asymmetric
    pair walks the value on every read-modify-write, and a caller comparing
    against the parameter's own range sees every negative field as
    out-of-bounds.

    The sibling eosed project hit exactly this on its first write test
    against real hardware -- value and range decoded by different rules, so
    the two halves of one parameter contradicted each other (its
    RESOLUTION_NOTES §18). Here it was caught by a rehearsal instead.
    """
    if len(data) != param.size:
        raise ValueError(
            f"{param.name}: expected {param.size} bytes, got {len(data)}"
        )
    if param.kind == "text":
        from s3k.messages import decode_name

        return decode_name(data)
    if param.is_array:
        width = param.element_size
        return tuple(
            _decode_one(param, data[i * width:(i + 1) * width], width)
            for i in range(param.elements)
        )
    return _decode_one(param, data, param.size)


def _decode_one(param: Parameter, data: bytes, width: int) -> int:
    number = int.from_bytes(data, "little")
    if param.minimum < 0 and number >= (1 << (8 * width - 1)):
        number -= 1 << (8 * width)
    return number + param.display_offset


def encode_field(param: Parameter, value) -> bytes:
    """Turn a Python value into the raw bytes of *param*'s span."""
    if param.kind == "text":
        from s3k.messages import encode_name

        return bytes(encode_name(str(value), param.size))
    if param.is_array:
        if isinstance(value, (str, bytes)) or not hasattr(value, "__len__"):
            raise TypeError(
                f"{param.name} holds {param.elements} independent values; "
                f"pass a sequence of that length rather than {value!r}. A "
                f"scalar is refused rather than broadcast -- broadcasting is "
                f"what silently corrupted the other {param.elements - 1}."
            )
        if len(value) != param.elements:
            raise ValueError(
                f"{param.name}: expected {param.elements} values, "
                f"got {len(value)}"
            )
        return b"".join(_encode_one(param, v, param.element_size)
                        for v in value)
    return _encode_one(param, value, param.size)


def _encode_one(param: Parameter, value, width: int) -> bytes:
    number = int(value) - param.display_offset
    if not (param.minimum <= number <= param.maximum):
        # EVERY numeric field is checked, not only the display-offset ones.
        # This used to guard `display_offset` alone, on the reasoning that
        # those wrap into two's complement and store nonsense (POLYPH 0
        # becoming 255). The others do something worse: they encode cleanly
        # and go to the machine. MODVFILT1 is -50..+50, a calibration probe
        # asked for 60, the byte 60 was duly sent, and the filter modulated
        # the wrong way for a whole run before the range was checked by hand.
        # A value that fits in the field's WIDTH is not the same as a value
        # the field accepts.
        low = param.minimum + param.display_offset
        high = param.maximum + param.display_offset
        raise ValueError(
            f"{param.name}: {value} is outside {low}..{high}"
        )
    if number < 0:
        # The headers store small signed quantities (pan, transpose, tuning)
        # as two's complement in the field's own width. The spec states the
        # display range, not the storage form, so this is the one place the
        # transcription is being interpreted rather than copied.
        number += 1 << (8 * width)
    if not 0 <= number < (1 << (8 * width)):
        raise ValueError(
            f"{param.name}: value {value} does not fit in {width} byte(s)"
        )
    return number.to_bytes(width, "little")


def describe_value(param: Parameter, value) -> str:
    """Render *value* the way a user should read it.

    Never raises. A parameter table this size, transcribed by hand, will
    contain a value that falls outside its own stated range sooner or later,
    and a display helper is the wrong place to discover that -- so an
    unmappable value is shown as itself rather than blowing up a whole pane.
    """
    if param.is_array and isinstance(value, (tuple, list)):
        # Twelve detunes read as twelve numbers and nothing else. Naming the
        # notes is what makes a temperament legible at a glance, and the
        # non-zero ones are the whole content -- an equal-tempered program is
        # all zeros and should say so in three words rather than twelve.
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        if len(value) == len(names):
            moved = [f"{names[i]} {v:+d}" for i, v in enumerate(value) if v]
            if not moved:
                return "equal temperament"
            unit = f" {param.unit}" if param.unit else ""
            return ", ".join(moved) + unit
        return ", ".join(str(v) for v in value)
    try:
        if param.kind == "text":
            return str(value)

        number = int(value)

        # Re-read a two's complement field the way the range says to.
        if param.minimum < 0 and number >= (1 << (8 * param.size - 1)):
            number -= 1 << (8 * param.size)

        # *value* already carries the display offset -- decode_field applied
        # it -- so nothing is added here. `values` keys are display values
        # too, which matters only for a field that has both, and none does.
        if param.values and number in param.values:
            return param.values[number]

        if param.key[0:2] and (param.region, param.name) in _NOTE_VALUED:
            if 0 <= number <= 127:
                return f"{number} ({note_name(number)})"

        # What the value MEANS, where it was measured. The tables carry each
        # parameter's range and none of its meaning -- FILFRQ is "basic filter
        # frequency, 0 to 99" and not one word about which hertz -- so this is
        # the only place a reader learns that 63 is about 4 kHz.
        physical = ""
        try:
            from s3k.scales import describe as _describe

            physical = _describe(param.region, param.name, number)
        except Exception:                       # never break a display pane
            physical = ""

        if param.unit and physical:
            return f"{number} {param.unit} ({physical})"
        if physical:
            return f"{number} ({physical})"
        if param.unit:
            return f"{number} {param.unit}"
        return str(number)
    except Exception:  # pragma: no cover - the guarantee is "never raises"
        return str(value)
