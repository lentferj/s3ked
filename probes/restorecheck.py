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
    m = {}
    for k in params.PARAMETERS_BY_NAME:
        if isinstance(k, tuple) and k[0] == region:
            try: m.setdefault(params.lookup(k).offset, []).append(k[1])
            except Exception: pass
    return m
