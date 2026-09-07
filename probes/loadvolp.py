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
"""Load a named volume from a NAMED PARTITION -- §170 gate, then CLR, then load.

loadvol.py could only ever load from partition 0: its load phase does
select_device / select_drive / select_volume with no select_partition, so the
volume index it passes is resolved against whatever partition was last
selected. MX5 is on B and MX8 on C, so that omission would have loaded the
wrong volume silently. select_partition is what forces the directory re-read
(§112/§70/§96), which makes it load-critical here rather than a probe detail.

Usage: loadvolp.py <partition-int> "VOLUME NAME"
"""
import sys, time
sys.path.insert(0,"/home/lentferj/temp/s3ked-logs")
from s3kconnect import connect
PART=int(sys.argv[1]); TARGET=sys.argv[2]
PROBE_DRIVE, MAIN_DRIVE = 7, 4
def patient(fn,*a,tries=8,gap=1.5,**kw):
    last=None
    for _ in range(tries):
        try: return fn(*a,timeout=15.0,**kw)
        except Exception as e: last=e; time.sleep(gap)
    raise last
def say(*t): print(" ".join(str(x) for x in t),flush=True)
def vols(br): return [x.name.rstrip() for x in patient(br.volume_list,tries=4)]
br=connect()
try:
    say("[%s] resident before: %d programs"%(time.strftime('%H:%M:%S'),
        len(patient(br.program_list))))
    patient(br.select_device,1); time.sleep(1.5)
    patient(br.select_drive,PROBE_DRIVE); time.sleep(2.0)
    patient(br.select_partition,0); time.sleep(2.2); vp=vols(br)
    patient(br.select_drive,MAIN_DRIVE); time.sleep(2.0)
    patient(br.select_partition,0); time.sleep(2.2); vm=vols(br)
    say("   gate: SCSI %d -> %d, SCSI %d -> %d"%(PROBE_DRIVE,len(vp),MAIN_DRIVE,len(vm)))
    if vp==vm:
        say("REFUSING: identical lists, medium not present. Nothing cleared."); raise SystemExit(2)
    say("   medium LIVE")
    patient(br.select_partition,PART); time.sleep(2.5); vt=vols(br)
    say("   partition %d (%s): %s"%(PART,chr(65+PART),vt))
    if TARGET not in vt:
        say("REFUSING: %r not on partition %d. Nothing cleared."%(TARGET,PART)); raise SystemExit(3)
    vi=vt.index(TARGET)
    patient(br.select_volume,vi); time.sleep(2.0)
    say("   selected [%d] %r on partition %d"%(vi,TARGET,PART))
    need=None
    try:
        entries=patient(br.hd_directory,1,tries=3)
        need=sum(getattr(e,"audio_words",0) or 0 for e in entries)
        say("   directory: %d entries, %d audio words"%(len(entries),need))
    except Exception as e:
        say("   directory read failed (%s); size check skipped"%type(e).__name__)
    if need and need>16777216:
        say("REFUSING: needs %d words, machine holds 16777216"%need); raise SystemExit(4)
    say("\n[%s] CLR"%time.strftime('%H:%M:%S'))
    try: br.clear_memory(timeout=25.0)
    except Exception as e: say("   clear_memory raised %s (may be the machine working)"%type(e).__name__)
    time.sleep(10.0); say("   after CLR:",br.status(timeout=12.0))
    say("\n[%s] load type 1 from partition %d -- not polling"%(time.strftime('%H:%M:%S'),PART))
    patient(br.select_device,1,tries=3); time.sleep(1.2)
    patient(br.select_drive,MAIN_DRIVE,tries=3); time.sleep(2.0)
    patient(br.select_partition,PART,tries=3); time.sleep(2.5)
    vt2=vols(br)
    if vt2!=vt:
        say("REFUSING mid-sequence: partition listing changed after CLR (%s -> %s)"%(vt,vt2)); raise SystemExit(5)
    patient(br.select_volume,vi,tries=3); time.sleep(1.5)
    try: br.trigger_load(1,timeout=8.0)
    except Exception as e: say("   trigger returned %s -- that is the load running"%type(e).__name__)
    time.sleep(75.0)
    say("\n[%s] reading back"%time.strftime('%H:%M:%S'))
    names=[n.rstrip() for n in patient(br.program_list,tries=12,gap=4.0)]
    say("   %d programs, %d samples"%(len(names),len(patient(br.sample_list,tries=6,gap=3.0))))
    for i,n in enumerate(names):
        g=patient(br.get_parameter,"GROUPS",i,tries=4,_bounds=False)
        pn=patient(br.get_parameter,"PRGNUM",i,tries=4)
        say("     [%2d] g=%-3d PRGNUM %s"%(i,g,pn))
    say("   status:",br.status(timeout=12.0))
finally:
    try: br.close()
    except Exception: pass
