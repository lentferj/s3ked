"""LFO2CAL: 18 pre-built programs, ZERO parameter writes.

mpc2emu authored the disc so every condition is a whole program.  So this
selects and captures and writes nothing -- no snapshot, no restore, and it
cannot leave the machine altered however it dies.  That matters today: a
set_parameter run half an hour ago got `device reported an error writing
program 2 at offset 92 (code 1)` followed by silence, and two probes before it
had to be SIGKILLed with fields left written.

ONE capture client for all eighteen points.  start()/stop_channels() are
re-callable and a churn test showed four create/destroy cycles cost 4.1s, so
client lifetime was NOT the earlier hang -- but one client is still fewer moving
parts than eighteen.

Reports for every program: level, the COHERENT envelope swing at LFO2's rate
(loudness), the coherent BAND-RATIO swing (filter), the PAN swing, the trough,
and the strongest peak's frequency.  Both destinations in one pass, because the
disc carries both and a pan column is the only thing that shows the confound
§265 proved exists.
"""

import sys, time

sys.path.insert(0, "/home/lentferj/git-repos/s3ked/probes")
import numpy as np, rtmidi
from jcap import Capture
from pandepgate import coherent_swing_db
from s3kconnect import connect

SRC = ("system:capture_13", "system:capture_14")
HOP, SKIP, HOLD, NOTE = 0.010, 0.60, 12.0, 36  # LONOTE=HINOTE=36 on this disc
br = connect()
# MODE IS A PRECONDITION FOR ANY AUDIO MEASUREMENT, and no probe here checked it.
# After Jan's CLR+load the machine sat on page 10 and produced NOTHING for any
# note on any of 16 channels -- while SysEx answered normally, the sample was
# resident with its audio, and the capture path was verified live. Page 0
# (SINGLE) and the same note reads -11.7 dBFS.
if br.mode() != 0:
    print("mode was %d; selecting SINGLE" % br.mode(), flush=True)
    got = br.select_mode(0)
    assert got == 0, "machine did not move to SINGLE (reads %s)" % got
print("mode %d (SINGLE)" % br.mode(), flush=True)
names = br.program_list()
info = []
for i, nm in enumerate(names):
    p = lambda f: br.get_parameter(("program", f), i)
    k = lambda f: br.get_parameter(("keygroup", f), i, keygroup=0)
    info.append(
        dict(
            i=i,
            nm=nm,
            prg=br.get_header_bytes("program", i, 15, 1)[0],
            ch=p("PMCHAN"),
            dep=p("PANDEP"),
            rat=p("PANRAT"),
            asrc=p("MODSAMP3"),
            aamt=k("MODVAMP3"),
            fsrc=p("MODSFILT1"),
            famt=k("MODVFILT1"),
        )
    )
rate = 0.11880 * info[0]["rat"]
print(
    "LFO2 rate from PANRAT %d -> %.3f Hz predicted (§260); peak Hz reported per row"
    % (info[0]["rat"], rate),
    flush=True,
)
out = rtmidi.MidiOut()
out.open_port([k for k, n in enumerate(out.get_ports()) if "M4U XT" in n and "MIDI 1" in n][0])
print(
    "\n%3s %-11s %5s %5s %6s | %7s %9s %9s %8s %8s %7s"
    % (
        "idx",
        "slot",
        "dep",
        "amt",
        "prod",
        "lvl dB",
        "amp sw",
        "filt sw",
        "pan sw",
        "trough",
        "peak Hz",
    ),
    flush=True,
)
# Program names on this disc come from a commercial library and are never
# printed (project rule); the slot index plus depth/amount/product identify
# the condition completely.
for d in info:
    d["slot"] = "slot-%02d" % d["i"]
try:
    with Capture(sources=SRC, name="s3ked-lfo2cal") as cap:
        # Sample rate from the capture client, never a constant.
        sr = int(cap.samplerate)
        for d in info:
            br.select_program_number(d["prg"])
            time.sleep(0.35)
            cap.start()
            time.sleep(0.30)
            out.send_message([0x90 | d["ch"], NOTE, 100])
            time.sleep(HOLD)
            out.send_message([0x80 | d["ch"], NOTE, 0])
            time.sleep(0.25)
            ch = cap.stop_channels()
            sk = int(SKIP * sr)
            L = np.asarray(ch[0][sk:], float) / 32768.0
            R = np.asarray(ch[1][sk:], float) / 32768.0
            mono = (L + R) / 2.0
            lvl = 20 * np.log10(max(np.sqrt((mono**2).mean()), 1e-12))
            if lvl < -60:
                print(
                    "%3d %-11s SILENT at %.1f dBFS -- not analysed" % (d["i"], d["slot"], lvl),
                    flush=True,
                )
                continue
            n = int(sr * HOP)
            m = len(mono) // n
            rms = lambda x: np.sqrt((x[: m * n].reshape(m, n) ** 2).mean(axis=1))
            env = 20 * np.log10(rms(mono) + 1e-12)
            bal = 20 * np.log10((rms(L) + 1e-12) / (rms(R) + 1e-12))
            # band ratio for the filter destination, high/low energy per window
            f = np.fft.rfftfreq(4096, 1.0 / sr)
            lo = (f >= 80) & (f <= 200)
            hi = (f >= 2500) & (f <= 6000)
            br_s = []
            for w in range(len(mono) // 4096):
                P = np.abs(np.fft.rfft(mono[w * 4096 : (w + 1) * 4096] * np.hanning(4096))) ** 2
                br_s.append(10 * np.log10(P[hi].mean() / max(P[lo].mean(), 1e-30)))
            asw, _, apf = coherent_swing_db(env, HOP, rate)
            fsw, _, _ = coherent_swing_db(np.array(br_s), 4096 / sr, rate)
            psw, _, _ = coherent_swing_db(bal, HOP, rate)
            prod = d["dep"] * (d["aamt"] or d["famt"])
            print(
                "%3d %-11s %5d %5d %6d | %7.1f %9.3f %9.1f %8.3f %8.1f %7.2f"
                % (
                    d["i"],
                    d["slot"],
                    d["dep"],
                    d["aamt"] or d["famt"],
                    prod,
                    lvl,
                    asw,
                    fsw,
                    psw,
                    float(np.percentile(env, 5)),
                    apf,
                ),
                flush=True,
            )
finally:
    try:
        out.send_message([0x80, NOTE, 0])
    except Exception:
        pass
    try:
        out.close_port()
    except Exception:
        pass
    try:
        br.close()
    except Exception:
        pass
print("\nNOTHING WAS WRITTEN -- no restore needed.", flush=True)
print("DONE", flush=True)
