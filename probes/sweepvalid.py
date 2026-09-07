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
"""Partition sweep with NAME-BYTE VALIDATION, not listing comparison.

Two failure modes, both mine to have found and neither caught by counting:
  - an EMPTY partition echoes the previous non-empty read
  - reading PAST the last real partition returns ~19 "volumes" whose names are
    non-printable garbage

A listing-comparison rule catches the first and passes the second. Validating
that names are printable ASCII in the 12-char field catches both.
"""
import sys, time, string, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from s3kconnect import connect
OK=set(string.ascii_letters+string.digits+" -_.#/&'()+")
def printable(n): return bool(n) and all(c in OK for c in n)
def patient(fn,*a,tries=6,gap=1.5,**kw):
    last=None
    for _ in range(tries):
        try: return fn(*a,timeout=15.0,**kw)
        except Exception as e: last=e; time.sleep(gap)
    raise last
br=connect()
try:
    patient(br.select_device,1); time.sleep(1.2)
    patient(br.select_drive,4); time.sleep(1.8)
    prev=None; real={}
    for part in range(8):
        try:
            patient(br.select_partition,part,tries=3); time.sleep(2.2)
            vs=[x.name.rstrip() for x in patient(br.volume_list,tries=3)]
        except Exception as e:
            print("  partition %d (%s): no answer"%(part,chr(65+part)),flush=True); continue
        bad=[v for v in vs if not printable(v)]
        echo=(vs==prev)
        if bad:
            verdict="GARBAGE -- %d of %d names non-printable, past the last real partition"%(len(bad),len(vs))
        elif echo:
            verdict="ECHO of the previous partition -- ABSENT, not %d volumes"%len(vs)
        else:
            verdict="%d volumes"%len(vs); real[part]=vs
        print("  partition %d (%s): %s"%(part,chr(65+part),verdict),flush=True)
        if not bad and not echo:
            for i,n in enumerate(vs): print("       %2d  %r"%(i,n),flush=True)
        prev=vs
    tot=sum(len(v) for v in real.values())
    print("\n  REAL partitions: %s   total %d volumes"%(sorted(real),tot),flush=True)
    allv=[n for v in real.values() for n in v]
    # volumes to confirm present, from the command line -- never hard-coded:
    # a volume name may carry library material (CLAUDE.md), and a name baked
    # into a probe outlives the card it was written for.
    for want in sys.argv[1:]:
        hit=[n for n in allv if n.upper().startswith(want.upper()[:9])]
        print("     %-14s %s"%(want,hit or "NOT FOUND"),flush=True)
finally:
    try: br.close()
    except Exception: pass
