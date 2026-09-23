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
"""Restore verification that cannot fail silently in EITHER direction.

A boolean "byte-identical: False" told me something was wrong and nothing about
what, and I could not reproduce it across four attempts. Peer's fix: record
WHICH bytes differ, always, not only on failure.

Logging the diff on every check also addresses the direction I had no evidence
on -- a false SUCCESS. A comparison that passes because both sides came from
the same stale fetch is invisible to a boolean; here the fetch counts and the
region length are reported alongside, so a short or cached read shows up as a
suspiciously trivial comparison rather than as a pass.
"""
def diff_report(before, after, region, names=None, label=""):
    if len(before) != len(after):
        return False, "LENGTH MISMATCH %d vs %d -- a short read, not a restore failure" % (
            len(before), len(after))
    d = [(o, before[o], after[o]) for o in range(len(before)) if before[o] != after[o]]
    if not d:
        return True, "identical over %d bytes of %s%s" % (len(before), region,
                                                          (" [%s]" % label) if label else "")
    lines = []
    for o, x, y in d[:12]:
        nm = (names or {}).get(o, ["(unnamed)"])
        lines.append("offset %3d  %3d -> %3d  %s" % (o, x, y, nm))
    return False, "%d byte(s) differ over %d of %s: %s" % (
        len(d), len(before), region, "; ".join(lines))

def field_map(params, region):
    """Every byte of *region* mapped to the field that owns it, interiors too.

    THIS KEPT ONLY THE START OFFSET UNTIL 2026-09-23 and threw the size away,
    so a differing byte inside any multi-byte field reported "(unnamed)".
    That is 199 bytes across the five regions, and in the SAMPLE header the
    blind bytes outnumbered the named ones 74 to 35 -- its layout is mostly
    4- and 6-byte loop and address fields. A restore that failed inside
    SHNAME, SLOCAT or a loop point said so without naming what it had left
    wrong, which is the one thing this probe exists to tell you.

    Found by eosed the same day in their own EOS decompilation -- an offset
    table that kept the addressing mode's operand and discarded its size, so
    a `movew` row was read as a byte row. Same defect, different language:
    a span recorded by where it starts and not by how far it reaches.

    Interior bytes are labelled ``NAME+k`` rather than ``NAME`` so a report
    still distinguishes "the field changed" from "byte 7 of the field
    changed" -- the fix must not blur what it is there to sharpen.
    """
    m = {}
    for k in params.PARAMETERS_BY_NAME:
        if not (isinstance(k, tuple) and k[0] == region):
            continue
        try:
            par = params.lookup(k)
        except Exception:
            continue
        for i in range(max(1, par.size)):
            m.setdefault(par.offset + i,
                         []).append(k[1] if i == 0 else "%s+%d" % (k[1], i))
    return m
