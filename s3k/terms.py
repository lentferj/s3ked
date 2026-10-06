# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.

"""What the Akai calls things, declared once.

**The instrument's word is the instrument's.** An S1000 or S3000 says
PROGRAM for one stored sound and the machine's own term for the area it
lives in is its memory -- `PRGNUM`, `SMPLNUM`, the memory totals on the
STAT reply, "memory" on the panel and in the manual. So those are the words
every string a user reads uses here.

**A program is not a sample, and a sample is not a voice.** All three exist
in this protocol and none is a synonym for another:

* a **program** is the thing that plays: a set of keygroups with envelopes,
  filters and a sample assignment. It is what the panel calls a PROGRAM and
  what the family calls the instrument's word for one stored sound.
* a **sample** is the audio. Editing one means editing the machine's
  waveform data.
* a **keygroup** is what sits between them, and is named in `own` below
  rather than being left to a reader to infer: it is the part of a program
  that says which key plays what, and the parameter table in
  :mod:`s3k.params` is addressed by it.

A tool that called a program a "patch" would be translating the
manufacturer's word for its own reasons, which is the one thing the family
contract exists to stop.
"""

from __future__ import annotations

from vinsynlib.terms import Terminology, register

__all__ = ["TERMS"]

TERMS = Terminology(
    app_name="s3ked",
    sound="program",
    container="memory",
    device="Akai S1000/S3000",
    own={
        # The three regions this protocol addresses, which the panel names and
        # the family has no word for. `region` is this project's own; see
        # s3k.params.REGIONS.
        "region": "region",
        "keygroup": "keygroup",
        "sample": "sample",
        # What is on the disc. The panel says LOAD/SAVE and the protocol
        # says volume, but the thing actually being addressed is a disc
        # partition -- `s` SCSI, then a drive, then a partition, then a
        # volume -- and conflating the four is how a load wrote to the wrong
        # one.
        "disc": "volume",
        "partition": "partition",
        # The expansion boards, which the panel does not name and the
        # protocol does not enumerate: they are declared in the settings
        # cache by hand, because a field or page needing one is a page the
        # machine refuses to show.
        "board": "expansion board",
    },
)

#: Registered at import so the family's registry knows what this tool calls
#: its concepts. Registration rather than a monkeypatched module global: a
#: library cannot know the name of the program using it, and passing it in
#: is honest where patching is global.
register(TERMS)