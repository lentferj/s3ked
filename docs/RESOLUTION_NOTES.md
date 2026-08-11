<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# Resolution notes

*How* to resolve what `TODO.md` lists as open: protocol findings, probe
procedures, and ready-to-apply code. Numbered sections, referenced from code
comments as "see RESOLUTION_NOTES §N".

---

## §1 — Protocol survey: what this family has, and what it does not (resolved, 2026-08-08)

This section exists so the question that started the project is never
re-derived. It was settled from primary sources before a line of code was
written.

### The question

Does the Akai S3000(XL) offer anything like the protocol the sibling
**k2kremote** project uses for the Kurzweil K2000 — read the device's LCD,
inject front-panel button presses, mirror the screen in a terminal?

### The answer: no. There is no panel protocol on this family.

Checked against the Akai scan held by the Internet Archive as item
`S3000XLOM`, file `S2000S3000xlS3200xl-SysexDocumentation_djvu.txt`.
Grepping the whole document for `screen|front panel|panel|display|keypress|
button|LCD|wheel` yields **one** hit, and it is only naming the "Mode
Buttons". There is no display read, no button injection, and no panel echo
anywhere in the documented command set.

The S1000 document is the mild tease here: it describes the machine's own UI
as "a 240x64 graphic screen, two S900-like rotary encoders and a similar set
of function and numeric keys… 8 'soft' function keys under the screen" —
*the same panel geometry as the K2000*. Those pixels are simply not reachable
over MIDI.

### What the family does have: one editor/librarian protocol

```
F0 | 47 | cc | op | 48 | item-index(14b) | selector | offset(14b) | count(14b) | <nibbled data> | F7
```

`47` = Akai, `cc` = MIDI exclusive channel, `48` = model identity (shared by
the whole S1000/S3000 line). Three stacked opcode layers:

| Layer | Opcodes | Content |
|---|---|---|
| S1000 base | `00`–`16`, `1D` | RSTAT/STAT, RPLIST/PLIST, RSLIST/SLIST, RPDATA/PDATA, RKDATA/KDATA, RSDATA/SDATA, RSPACK/ASPACK/CASPACK, RDDATA/DDATA, RMDATA/MDATA, DELP/DELK/DELS, SETEX, REPLY |
| S2800/S3000/S3200 | `27`–`38` | byte-offset request/write for Program / Keygroup / Sample headers, FX/Reverb, Cue-List, Take List, Misc, Volume List, HD directory |
| S2000/S3000XL/S3200XL | `41`, `42` | REQUEST MULTI DATA / MULTI DATA |

The S3000 document states the design intent plainly: "anything that can be
done from the S3000 front panel should be able to be done by means of such
MIDI system exclusive operations". That is functional equivalence — *not*
panel mirroring, and the distinction is the whole reason this project is an
editor and not a remote.

### Two places this protocol beats the sibling eosed project's

1. **The device repaints its own LCD.** Item-index bit 13 is "postpone screen
   update" and bit 12 "postpone recalculation" — opt-*out* flags. eosed had to
   reverse-engineer a forged Program Change to force a redraw because EOS has
   no such command at all. **Do not port that workaround.** Bit 12 carries its
   own hazard, in the spec's words: "the machine may be in an undetermined
   state until the same parameter is sent with this bit cleared."
   **Confirmed on hardware 2026-08-10** — the panel redrew itself on a write,
   untouched (§11 Finding J).
2. **Writes are acknowledged.** `REPLY` (`16h`) returns `0` = ok, `1` = error.
   eosed must read a value back to know whether a write landed.
   **Confirmed on hardware 2026-08-10**, and Finding A makes it essential
   rather than merely convenient: a read-back can return a stale buffer.

### Where Akai *does* have the k2kremote model: the next generation

Z4/Z8/S5000/S6000/MPC4000. Prior art is `aksy`
(<https://github.com/watzo/aksy>). Front-panel injection is genuinely SysEx,
section `0x2C` (from `data/z48/frontpaneltools`):

```
2C 01  keypress_hold(BYTE)          2C 03  move_datawheel(SBYTE)
2C 02  keypress_release(BYTE)       2C 04  set_qlink_control(BYTE, WORD)
2C 10/11  ascii_keypress_hold/release(WORD, WORD)
2C 20/21  mouseclick_at_screen / mousedoubleclick_at_screen(WORD, WORD)
```

But **the screen read is not SysEx**, and that is what rules it out here.
`get_panel_state()` is a raw USB bulk command `CMD_LCD_GET` on endpoint
`EP_IN 0x82` (`src/aksyx/aksyxusb.c:412`), returning
`PANEL_PIXEL_DATA_LENGTH = 1860` bytes of pixels (248×60 ÷ 8) plus
`PANEL_CONTROL_DATA_LENGTH = 18` bytes of control/LED state. aksy ships a
working GTK mirror client at `src/aksui/UI/lcdscreen.py` — worth reading if a
Z-series ever turns up. The S5000/S6000 SysEx spec adds front-panel
*lock/unlock* only, not a screen read.

So a k2kremote-style mirror needs the USB port this family does not have.

### Sources

- S1000: <https://lakai.sourceforge.net/docs/s1000_sysex.html>
- S2800/S3000/S3200: <https://lakai.sourceforge.net/docs/s2800_sysex.html>
- S2000/S3000XL/S3200XL: <https://lakai.sourceforge.net/docs/s2000_sysex.html>,
  and the scan in archive.org item `S3000XLOM`
- Z-series prior art: <https://github.com/watzo/aksy>

---

## §2 — Provenance of the parameter tables, and what that costs us (open, partly mitigated)

`s3k/params.py` is transcribed from the S2800/S3000/S3200 document's 251
`Parameter:` entries (Offset / Field size / Range / Description) across three
192-byte regions: Program (84 entries), Keygroup (130 after §3), Sample (35).

**There are two transcription steps between the hardware and this code, and
the first one is known to contain errors.** The lakai text is itself a
hand-typed transcription of a printed Akai document by a third party signing
as "Frank", who marked passages he believed were wrong with `FN` and
disclaimed any warranty of correctness.

Where his `FN` marks actually fall matters, and is narrower than feared:

- **Not in the parameter tables.** Grepping all 251 entries for `FN` or `???`
  returns **zero** hits. The offsets and ranges are unmarked.
- **In the message-format sections**, twice, and both touch code we ship:
  - *Request for Sample Header bytes* (`0x2B`) — the source lists a nibbled
    data portion in a **request**, marked "??? typo in specs..? - FN". He is
    almost certainly right: every other request in the family is a bare
    12-byte header plus EOX, and a request carrying data makes no sense.
    `s3k.messages.HeaderRequest` implements it as a bare header.
  - *Receive Cue-List bytes* (`0x30`) — the data lines are marked "this is not
    in the orig docs - typo? - FN". Not currently exercised; s3ked does not
    touch cue lists.

**One independent cross-check now exists**, and it is a good one — see §8: the
multi-part structure is documented in a *separate* Akai document, transcribed
separately, and all twelve fields it shares with the program header land on
identical offsets. That does not prove the program header is right, but two
independent transcriptions agreeing on twelve offsets is much better evidence
than one transcription alone.

**To close this:** read a known program header off a real machine and diff it
against the table. `s3kcli --demo header program 0` shows the shape the code
expects; the same command without `--demo` gets the real thing. Specifically
check that `PRNAME` lands at offset 3 and is 12 bytes — if the name comes back
readable, the early offsets are right and confidence in the rest rises sharply.

**Blocked on:** hardware.

---

## §3 — Keygroup offsets 161/162 documented twice (RESOLVED from the manual)

The keygroup listing defines offsets 161 and 162 **twice**:

- mid-listing as `PFXCHAN` / `PFXSLEV` — the same names the *program* header
  uses — enumerated `0 = OFF, 1 = FX1, 2 = FX2, 3 = RV3, 4 = RV4`;
- again under the heading **"S2000/S3000XL/S3200XL Common Parameters"** as
  `KFXCHAN` / `KFXSLEV`, "Keygroup override Effects Bus select", enumerated
  `0 = PRG (use the global program header selection), 1 = OFF, 2 = FX1,
  3 = FX2, 4 = RV3, 5 = RV4`.

So this was never a chronological "which came later" question — the heading
says outright that the second definition is the **XL family's**. The first is
the base S2800/S3000/S3200 definition of the same byte.

**Confirmed against the S3000XL owner's manual addendum** (Japanese;
`S3000XL_OMadd_djvu.txt`, and the page mock-up reads
`Override prog FX bus: PRG send: 25`):

> Override prog FX bus: と send: のパラメータを使うと、…個々のキーグループを
> エフェクトに送ることができます。**初期設定は PRG**（つまりプログラムの
> エフェクトバス選択を使ったルーティング）になりますが、**OFF**（選択した
> キーグループをエフェクトに送らない）、**FX1, FX2, RV3, RV4** のいずれかを
> 選ぶこともできます。

— "the default is PRG (routing using the program's effects bus selection), but
you can also choose OFF, FX1, FX2, RV3 or RV4." That is exactly the six-value
`KFXCHAN` enumeration, in order.

**Resolution:** `s3k/params.py` keeps `KFXCHAN` / `KFXSLEV` for keygroup
161/162, which is correct for the S2000/S3000XL/S3200XL machines this project
targets. The superseded names stay in `notes`. On a plain S2800/S3000/S3200
the earlier five-value enumeration applies and every value read from this byte
would be one lower — recorded rather than handled, since the project targets
the XL family.

## §4 — Note-name octave offset (RESOLVED from three sources)

The S2800/S3000/S3200 document words the note-valued fields (`PLAYLO`,
`PLAYHI`, `LONOTE`, `HINOTE`) as "21 to 127 represents **A1** to G8", which
cannot hold at both ends: if 21 is A1 then 127 is G9.

It is simply a dropped minus sign. Three sources settle it:

| source | wording |
|---|---|
| S2800/S3000/S3200 SysEx | "21 to 127 represents **A1** to G8" — the typo |
| S2000/S3000XL/S3200XL SysEx | "21 to 127 represents **A-1** to G8" |
| S3000XL owner's manual | panel renders keyspans as `C_0` … `G_8`, and splits a keyboard as `C0-B1, C2-B2, C3-B3, C4-B4, C5-G8` |

All three agree on **octave = value // 12 - 2**:

```
note  21 -> A-1      note  60 -> C3  (middle C)
note  24 -> C0       note 127 -> G8
```

`s3k.params.note_name` implements that. An earlier revision of this project
used `// 12` (rendering note 21 as `A1`), which was wrong; it was corrected
once the XL document and the manual were read.

## §5 — The miscellaneous data-index table is missing from the source (open)

**Search exhausted on lakai.sourceforge.net (2026-08-08).** The whole site was
read: the three SysEx pages, `readfloppy.html` (disk format, not protocol),
the API page (empty, "This is the next thing to fill in"), and the rest. The
site's own documentation page notes that Akai used to host these documents and
took them down. Nothing there carries the misc index table.

`RMISCDATA` / `MISCDATA` (`0x33` / `0x34`) address "miscellaneous variables
and functions" by a 14-bit Data Index, with a bank byte selecting the type
(`1=byte, 2=word, 3=dword, 4=smpte, 5=signed smpte, 6=name, 7=16byteflag`).
Note this family reuses the header shape but **not** its meaning: the bytes
that are "byte offset" elsewhere are documented as reserved zeroes here.

**The table of Data Index values is not in the transcription.** Grepping for
`Data Index` finds only the two message-format definitions; no listing of
which index is which variable or function.

The practical consequence is `BTSORT`. The spec says twice that after writing
`PRGNUM`, "Miscellaneous function BTSORT should be triggered to resort the
list of programs into order and to flag active programs" — but never says what
index invokes it. So s3ked cannot offer a `trigger_btsort()`, and
`s3k/params.py` records the requirement in `PRGNUM`'s `desc` so a user at
least knows to do it from the panel.

**To close this:** find a fuller copy of the Akai document (the printed
original, or another transcription), or discover the index by capturing what
MESA sends when it renumbers a program.

**Blocked on:** a better source, or a MIDI capture of MESA.

---

## §6 — The SysEx send gap, measured (RESOLVED on hardware, 2026-08-10)

`SEND_GAP` was 0.05 s and had never been anything but a guess. Walked down
against an S3000XL with `probes/throttle.py`. **The two gaps are different
numbers, and only one of them matters.**

### Why not the method this section originally proposed

The old plan was to loop a whole-header read while cutting the gap. That is a
fine load test but it cannot find a floor: at 31250 baud a byte costs 320 us,
so a 192-byte header returns as ~400 nibbled wire bytes -- about 128 ms -- and
any gap below that is invisible underneath the transfer. The probe uses
single-parameter frames instead (~12 bytes out, ~14 back), where the gap is
the dominant term.

### Reads: self-pacing, and the gap is free below the round trip

40 single-parameter reads at each level, zero failures all the way to 0 ms:

| gap | rate | |
|---|---|---|
| 50 ms | 20.4/s | gap-bound |
| 25 ms | 40.4/s | gap-bound |
| 10 ms | 94.8/s | **at the ceiling** |
| 5 / 2 / 1 / 0 ms | ~94/s | round-trip bound |

The round trip is ~10.6 ms and the rate saturates there. Because `ThrottledOut`
owes its gap *after* a send, the wait overlaps the blocking reply wait, so any
read gap below the round trip costs nothing at all. `SEND_GAP` is now **10 ms**
-- a little headroom, and 5x the old throughput for free.

### Acknowledged writes: the device paces us, at ~75 ms each

40 acknowledged writes at each level, zero failures to 0 ms, and the rate is
**flat at 13.3/s regardless of gap**. The write frame plus `REPLY` is only
~7 ms of wire time, so the other ~68 ms is the machine's own recalculation and
screen redraw -- which is what the `Postpone` bits exist to defer. The gap is
irrelevant here because the acknowledgement dominates it.

### Fire-and-forget writes: 75 ms, and the old guess was wrong

The only gap that bites, and the failure is silent. Measured by counting
acknowledgements rather than inspecting the final value -- every write is
answered by `REPLY`, so N sent against N returned is direct evidence, whereas
the final value only ever reveals a lost *last* write.

| burst | gap | acknowledged |
|---|---|---|
| 40 | 50 ms | 40/40 |
| 40 | 25 ms | **22/40** |
| 150 | 100 ms | 150/150 |
| 150 | 75 ms | 150/150 |
| 150 | 50 ms | **114/150** |

**50 ms passes a short burst and fails a long one.** That is the whole story:
the machine consumes writes at 13.3/s (~75 ms each), so any unacknowledged
sender faster than that grows an unbounded queue. 40 writes at 50 ms merely
fit in the buffer; 150 did not, and 36 vanished with nothing raised. The old
default was a guess and it was wrong **in the dangerous direction**.

`WRITE_GAP` is now a separate constant at **75 ms**, and `ThrottledOut` no
longer defaults it to `gap` -- inheriting a tuned-down read gap would have
silently handed a fire-and-forget caller an unsafe value.

### The conclusion that matters for callers

**Fire-and-forget buys nothing on this family.** Paced safely at 75 ms it runs
at 13.3/s, which is exactly what acknowledged writes achieve anyway, because
both are limited by the same device-side processing. So going unacknowledged
trades a guarantee for no throughput at all. Leave `confirm=True`.

Verified in use: the conformance sweep went 4.6 s -> 3.0 s on the new defaults
with identical findings. It is not 5x faster because whole-header reads are
wire-bound, exactly as the first paragraph predicts.

---

## §7 — Autodetect has no broadcast address to lean on (confirmed on hardware, 2026-08-10)

The sibling eosed project probes with a standard Universal Device Inquiry sent
to the broadcast device id. **This protocol has neither**: a device answers
only on its own exclusive channel, and the only message that reports what that
channel is (`STAT`) can itself only be obtained by addressing the right
channel. There is no way to ask "who is out there?".

So discovery is a sweep, structured like k2kremote's rather than eosed's:

1. try the remembered `send_port`/`recv_port` pair from `config.toml` first;
2. otherwise open **every** input as a listener, then send `RSTAT`
   (`F0 47 cc 00 48 F7` — harmless and read-only) out each output in turn;
3. accept a port whose reply carries operation code **`STAT` (`0x01`)**, not
   the `RSTAT` (`0x00`) that was sent. Matching on a *different* opcode is
   what distinguishes a real device from a MIDI-Thru loop echoing our own
   bytes — the same trick k2kremote uses with ALLTEXT/SCREENREPLY, and it is
   covered by
   `tests/test_bridge.py::test_autodetect_rejects_a_thru_loop_echoing_our_probe`;
4. wait the whole timeout even after an answer, so a second machine is heard;
5. collapse replies by exclusive channel (one machine on two inputs is one
   machine), and raise `AmbiguousDevice` rather than pick.

`channels=` defaults to `(0,)`, the factory value. Widening it multiplies the
sweep cost by its length, which is why it is not the default.

### Confirmed on hardware, 2026-08-10 — first live run

A real S3000XL was found on the **first attempt, on channel 0 out of the box**,
with no configuration. The sweep is as designed and the numbers are:

| measurement | value |
|---|---|
| host ports swept | 37 outputs, 37 inputs |
| full cold sweep | **40.3 s** (one 1 s probe per output) |
| cached-pair fast path afterwards | **1.1 s** |
| winning pair | one bidirectional port, send and receive names identical |

**The cached pair earns its keep**, decisively — a 36× difference on this host,
which is the question this section left open. `config.toml` is written on the
first success and hit on every run after.

The MIDI-Thru rejection also held in the wild: this host has a `Midi Through`
port and eight virtual-MIDI ports in the sweep, none of which produced a false
positive, because an echo carries `RSTAT` (`0x00`) and the match requires
`STAT` (`0x01`).

Not exercised: the two-machine `AmbiguousDevice` path, which still has never
seen two real samplers at once. Sweeping more than one exclusive channel is
also still synthetic — the machine was on 0, so `channels=` was never widened.

One byte of the reply did **not** survive the encounter; see §10.

---

## §8 — Cross-validation: the multi part IS a program header (resolved, 2026-08-08)

The S2000/S3000XL/S3200XL document defines a "Structure Of Multi Parts" with
13 fields. Twelve of them share a name with a program-header field — and every
one lands on the **same byte offset**:

| field | program header | multi part |
|---|---|---|
| PRNAME | 3 | 3 |
| PMCHAN | 16 | 16 |
| PRIORT | 18 | 18 |
| PLAYLO | 19 | 19 |
| PLAYHI | 20 | 20 |
| OUTPUT | 22 | 22 |
| STEREO | 23 | 23 |
| PANPOS | 24 | 24 |
| VOSCL | 70 | 70 |
| TRANSPOSE | 75 | 75 |
| PFXCHAN | 113 | 113 |
| PFXSLEV | 114 | 114 |

Twelve for twelve, across two documents transcribed separately by different
hands. The structural reading is that **a multi part is a program header** with
only a subset of fields meaningful, plus `PTUNOCM` at 115 which exists only in
multi mode.

Why this matters: §2's central worry is that the program-header offsets are a
transcription of a transcription with nobody to check them. This is the first
independent witness, and it agrees completely on every field the two documents
have in common — including the awkward sparse ones (70, 75, 113, 114) where a
transposition slip would be most likely and least visible.

It does not validate the ~72 program fields the multi part does not carry, nor
the keygroup and sample regions. But it materially raises confidence in the
spine of the program header.

Pinned by `tests/test_params.py::test_multi_part_offsets_mirror_the_program_header`,
so a future edit to either table that breaks the correspondence fails the build.

---

## §9 — Where the documents live (reference)

Local copies, alongside the user's own scans:

```
~/Dokumente/SYNTHS/Akai S3000XL/Docs/
  lakai_s1000_sysex.html                          S1000 protocol
  lakai_s2000_sysex.html                          S2000/S3000XL/S3200XL: multi mode
  lakai_s2800_sysex.html                          S2800/S3000/S3200: the parameter tables
  lakai_readfloppy.html                           disk format (not protocol)
  S2000S3000xlS3200xl-SysexDocumentation.pdf      Akai scan (+ _djvu.txt)
  S3000XL_OM.pdf                                  owner's manual (+ _djvu.txt)
  S3000XL_OMadd.pdf                               addendum: effects/multi (+ _djvu.txt)
```

The `_djvu.txt` files are the archive.org OCR of the same items
(`archive.org/download/S3000XLOM/…`) and are what the greps in these notes were
run against. **The owner's manual and addendum are the Japanese editions**, and
their OCR is rough — numbers get spurious internal spaces (`1 1 3 bytes` for
113), and `G` frequently reads as `6` or `S`. Read around any figure taken from
them.

Both manuals are worth more than their protocol content suggests: §3 and §4
were both settled from the addendum and the manual after the SysEx documents
alone had left them ambiguous. When a spec field is unclear, **check what the
front panel displays** before assuming it is unknowable.

---

## §10 — Parameter scales: how to measure what a value means (procedure, no results yet)

**Status: tooling built, tested synthetically, never run against a machine.
No number below is a measurement.**

### The gap

Every continuous field in the tables is a range without a unit. `FILFRQ` is
"basic filter frequency, 0 to 99"; `ATTAK1` is "attack rate of envelope 1,
0 to 99"; `PRLOUD` is "basic loudness, 0 to 99". Nothing says which hertz,
which seconds, which decibels — and the front panel shows the same integers,
so it cannot settle it either.

Two consumers need the map. This editor wants to render a value the way
`describe_value()` already renders enumerations. And a converter writing Akai
programs — the sibling mpc2emu writes S1000/S3000 programs and disk images
today — has to turn a cutoff in hertz into a 0–99 integer, and currently
guesses.

### The method, and why it is cheaper here

Set the parameter, play a note, record it, measure the audio, repeat. The
sibling projects did exactly this for the E-MU E4XT and the Kurzweil K2000R,
but there each parameter change meant rebuilding a bank and swapping an SD
card, so their published curves rest on four to six points.

This family takes the parameter over SysEx between one note and the next, so
a fifty-point sweep is one unattended run. **Sweep the whole range.** A
procedure copied from the siblings without noticing this would leave most of
the available accuracy behind.

### What was built

| file | role |
|------|------|
| `s3k/measure.py` | analysis: envelope, attack/decay/release, RMS and peak dB, spectrum, −3 dB corner, stereo balance, modulation rate, fundamental, exponential fit |
| `probes/calibrate.py` | driving: eight sweeps, each with its neutraliser list; `--dry-run` against a synthetic machine |
| `tests/test_measure.py` | every primitive against a signal whose answer is known in advance |
| `tests/test_calibrate.py` | every swept and neutralised parameter exists, is writable and is in range; the dry run recovers the fake machine's own curve |
| `docs/re_procedures/calibration.md` | order, traps, what each sweep settles |
| `HW_CALIBRATION.md` | bench checklist (machine-local) |

`measure.py` takes arrays and returns numbers — it opens no port and no file
except `read_wav`. That is what makes it testable with no sampler: a
synthesised 200 ms attack must measure 200 ms, and noise through a known
4-pole low-pass must report that corner.

### One defect the synthetic run already caught

The first dry run of the filter sweep reported **"500.6 Hz" for seventeen
consecutive settings**. The −3 dB search takes its reference level from a band
(default 100–500 Hz); once the corner falls *below* that band, the reference
is itself attenuated, the first bin above the band is already 3 dB down, and
every point returns the same value just above the band edge. A flat run of
identical numbers reads like a filter that stops moving — the worst kind of
wrong answer, because it looks like a finding.

`corner_frequency` now checks the reference band is actually flat and returns
NaN when it is not, and the sweep references at 50–100 Hz. The tolerance is
half the drop being searched for, not something tighter: fractional-octave
smoothing averages very few bins down at 40 Hz, and a genuinely flat 40–100 Hz
band still showed 1.7 dB of apparent tilt on measured noise.

Worth stating plainly: that defect was inherited from the sibling project's
version of the same function, where every corner measured happened to sit
above the band. It survived a real bench session there because the material
never exercised it.

### Two blockers that are not "no hardware"

1. **Source material.** These sweeps set parameters and play notes; they
   cannot put a sample in memory (transfer is deliberately unimplemented) and
   the family has no oscillator. Filter calibration needs broadband noise
   resident on the machine. That is a disk-image job, and mpc2emu already
   writes S1000/S3000 media — its `tests/re_banks/gen_akai_cal_disc.py` builds
   the disc.
2. **The LFO destination.** `MODSLFOT`/`MODSLFOL` are sources of modulation
   *of* LFO1, not its destination, and the routing prose is not conclusive.
   Confirm on the panel which destination LFO1 drives before running
   `lfo-rate`; a rate measured through an unconnected route comes back clean
   and fictitious.

### When results exist

Record each fitted curve here with the source sample, test note, velocity,
reference band and r2, and keep the CSV alongside. Then re-run one point per
curve at a **different note and velocity**: if the number moves, the curve
describes that note rather than the parameter, which usually means a
neutraliser was missed.

---

## §10 — The STAT "software version" field is not the OS version (open, first hardware contradiction)

**The first time this project's reading of a document has been contradicted by
a machine.** Found 2026-08-10, on the first session with real hardware.

The S1000 document is the only one of the three that describes `STAT` at all —
the S2800 and S2000 documents add operations but never redefine it. It says:

```
F0,47,cc,STAT,48,
vv,VV      S1000 software version VV.vv
```

`messages.Status` implemented exactly that: `version_minor=payload[0]`,
`version_major=payload[1]`. On a real S3000XL whose panel reports
`Operating System 2.00`, that yields **17.00**.

### The captured frame

```
F0 47 00 01 48 00 11 6E 07 68 07 00 00 00 08 00 00 78 07 00 F7
        ^^ ^^       ^^ ^^
        |  |        the version pair: vv=0x00, VV=0x11
        |  STAT
        exclusive channel 0
```

Payload (15 bytes): `00 11 | 6E 07 | 68 07 | 00 00 00 08 | 00 00 78 07 | 00`

### Why this is a decode failure and not a misalignment

Every other field in the same payload decodes correctly, so the version pair
is where the document says it is:

| field | bytes | value | plausible? |
|---|---|---|---|
| max blocks | `6E 07` | 1006 | yes |
| free blocks | `68 07` | 1000 | yes, 6 used |
| max words | `00 00 00 08` | 16777216 | exactly 2^24 = 32 MB, the family maximum |
| free words | `00 00 78 07` | 16646144 | yes, 131072 (128 K) used |
| exclusive channel | `00` | 0 | yes, matches the channel it answered on |

The payload is 15 bytes, exactly `2+2+2+4+4+1`, so no other alignment fits.

### Readings ruled out

None of these produces 2.00 from `vv=0x00, VV=0x11`:

- **17.00** — the documented `VV.vv`.
- **0.17** — the pair swapped.
- **1.7** — `VV` as version×10, the convention some Akai machines use.
- **1.1** — `VV`'s nibbles as major/minor.
- **11.00** — `VV`'s hex digits read literally.
- **nibbled data** — `0x11` has bit 4 set and is not a legal nibble byte, so
  the pair is not nibble-encoded like header data is.

The owner confirms **2.00 is the last and latest OS for this hardware**, which
rules out the field reporting a newer OS than the panel shows.

### Standing hypothesis (untested)

`STAT` reports a layer *below* the operating system — a boot ROM or EPROM
revision. The machine has two software layers: the S3000XL Operating System
Disk described in the addendum loads and flashes `OPERATING SYSTEM V2.0` over
what is burned in, and OS v2.0 EPROM upgrade kits were also sold. Under the
version×10 convention `0x11` = 17 = **v1.7**, an unremarkable ROM revision to
sit under a v2.00 OS.

Not confirmed: the owner could find no second version string anywhere on the
front panel or at power-on.

### What was done about it

**The field is no longer printed** — `s3kcli status` drops the line, and
`AmbiguousDevice` no longer labels machines by it. `Status.version_major` /
`version_minor` still decode per the document and stay in the API; only the
display is withdrawn, because a confident wrong number is worse than none.
Nothing in the codebase gates behaviour on the version, so this costs no
functionality.

Note that `demo.py` and four tests encode 2.00 as `major=2, minor=0` — that is
self-consistent, but it is now known **not** to be what a real XL puts on the
wire.

**To close this:** a second data point is the only way — the same probe against
another machine in the family, ideally one on a different OS, or an S1000
(where the document's claim may well be accurate and this an XL divergence).

**Blocked on:** a second machine. Not resolvable from the documents, which have
been exhausted: the string "software version" appears once across all of them.

Exhaustion verified 2026-08-10, against the live pages rather than only the
local cache. `STAT` is described in the S1000 document alone. The S2800
document adds operations `0x27`-`0x38` and redefines nothing; the S2000
document (<https://lakai.sourceforge.net/docs/s2000_sysex.html>) contains
exactly two operations, `0x41 REQUEST MULTI DATA` and `0x42 MULTI DATA`, and
no status message at all. There is no third description of the version field
to compare against.

---

## §11 — Read-only conformance sweep against hardware (run 2026-08-10)

`probes/conformance.py` reads a machine and judges it against the parameter
table and the three documents. It **cannot write**: every frame passes an
opcode allowlist installed in front of the bridge's output port, and
`tests/test_conformance.py` asserts that each destructive and write opcode is
refused before reaching the wire. That guard is why it is safe unattended.

First run: 68 reads in 4.6 s against an S3000XL holding one invented test
program and four synthetic waveform samples.

### Finding A — the extended layer does not bounds-check, and fails *silently*

**The most consequential result, and it contradicts the S1000 document.** That
document promises "if the program number is higher than the highest program in
the S1000, an error message will be given instead of data". On the S3000
extended operations (`0x27`-`0x38`) this machine does no such thing:

| request | expected | actual |
|---|---|---|
| `RPHEADER` program 99 (only program 0 exists) | REPLY/error | data |
| `RSHEADER` sample 99 (only 0-3 exist) | REPLY/error | data |
| `RKHEADER` keygroup 50 (program has 1) | REPLY/error | data |
| `RPHEADER` offset 2048, past a 192-byte header | REPLY/error | data |
| `RPHEADER` count 1024 | truncation or error | 1024 bytes returned |

**What it returns is the previous valid read's buffer.** Proven by priming:

```
read RSHEADER sample 2 (name field) -> 'SAWTOOTH'
read RPHEADER program 99 (invalid)  -> 'SAWTOOTH'
read RSHEADER sample 1 (name field) -> 'SQUARE'
read RPHEADER program 99 (invalid)  -> 'SQUARE'
```

So an out-of-range extended read returns **well-formed, plausible data from a
different structure**, with no error anywhere. Consequences:

1. **A wrong offset in `params.py` cannot be caught by reading.** It returns
   something that looks like a value. The parameter table cannot be validated
   by round-tripping against this layer alone.
2. **Read-back verification is actively misleading**, which turns §1's second
   point from a convenience into a requirement: `REPLY` is the *only* sound
   way to know a write landed. Do not add read-back verification as a
   fallback — it can confirm a write that never happened.
3. **Callers must bounds-check locally**, against `PLIST`/`SLIST` and
   `GROUPS`, because the device will not.

Indexing itself is fine for *valid* indices: samples 0-3 returned SINE,
SQUARE, SAWTOOTH and PULSE in `SLIST` order. The defect is only in refusal.

### Finding B — the S1000 layer *does* bounds-check, exactly as documented

The whole-block operations behave as the document promises, which makes them
the trustworthy way to ask whether something exists:

```
RPDATA program 0   -> data        RPDATA program 5/99/4095 -> REPLY ERROR
RSDATA sample 3    -> data        RSDATA sample 99         -> REPLY ERROR
RKDATA prog 0 kg 0 -> data        RKDATA prog 0 kg 50      -> REPLY ERROR
```

Use `RKDATA` to count a program's keygroups; counting with `RKHEADER` measures
the caller's own loop bound and nothing else. The probe does this.

### Finding C — every documented read operation is live on this machine

All 18 probed reads answered with their expected reply opcode. That includes
the whole extended layer, none of which had ever been confirmed on any
machine: `RFXDATA` (all five selectors), `RCUEDATA`, `RTAKEDATA`,
`RMISCDATA`, `RVOLLIST`, `RHDDIR`, and `RMULTIDATA` (both selectors). No
operation was silent, refused, or answered with the wrong opcode.

### Finding D — the two opcode layers agree byte for byte

The one check in the sweep that is not self-referential: read each header via
the S1000 whole-block operation (nibbled) and via the S3000 byte-offset
operation, and compare.

| region | S1000 layer | S3000 layer | compared | mismatches |
|---|---|---|---|---|
| program | 192 bytes | 192 bytes | 192 | **0** |
| keygroup | 192 bytes | 192 bytes | 192 | **0** |
| sample | 192 bytes | 192 bytes | 192 | **0** |

Two operation families documented years apart describe identical bytes. The
byte-offset addressing is corroborated by something other than itself, and
all three structures really are 192 bytes on this machine.

### Finding E — six range violations, and the notes field had already caught four

The sweep flagged fields whose value falls outside the range in `params.py`.
Four are not machine faults but **ranges that dropped a sentinel the
specification states in prose** — and the prose was preserved in each
parameter's `notes`, exactly as `params.py`'s docstring argued it should be:

| field | read | range | its own `notes` |
|---|---|---|---|
| `OUTPUT` (program 22) | 255 | 0..7 | "255 indicates OFF" |
| `KGMUTE` (keygroup 160) | 255 | 0..31 | "0ffh = off, mute groups 0 to 31" |
| `LDWELL2/3/4` (sample 60/72/84) | 0 | 1..9999 | "0 represents No Loop" |

**The decision to keep the specification's wording verbatim paid for itself
here.** Widening applied the same day: `OUTPUT` and `KGMUTE` now `0..255`
with `255 = off`, `LDWELL1`-`LDWELL4` now `0..9999` with `0 = no loop` and
`9999 = hold`. The sweep's range contradictions fell from 19 to 5.

Two lessons worth keeping:

- **`LDWELL1` needed the same fix and the sweep never flagged it**, because
  that loop happened to be in use. A sentinel is only observable when a field
  is set to it, so a conformance sweep finds *instances*, not *classes* — the
  fix has to be applied from the documentation the finding points at, across
  every sibling field.
- **`POLYPH` looks identical and must not be touched.** Its notes name 32,
  but "0 to 31 (these represent polyphony values of 1 to 32)" is a *display*
  mapping, not a storable sentinel. Widening it would have admitted a value
  the field cannot hold.

The other two are genuine table defects, both from the same transcription
artifact — a source that gave "a fixed value" where a range belongs:

- `VZONES` (keygroup 31) reads **4**, table says `0..0`. Four is right: the
  keygroup has `SNAME1`-`SNAME4`.
- `COHERE` (program 58) reads **0**, table says `1..1`.

### Finding F — §8's multi correspondence, confirmed on hardware

Multi mode had never been verified. `RMULTIDATA` answers on both selectors,
the multi file header reads `MULTINAME = 'MULTI FILE'`, and every field the
multi part shares with the program header returned an identical value:

```
program 0     PRNAME 'TEST PROGRAM'  PMCHAN 0  PRIORT 1  PLAYLO 24  PLAYHI 127  OUTPUT 255  VOSCL 50  TRANSPOSE 0
multipart 0   PRNAME 'TEST PROGRAM'  PMCHAN 0  PRIORT 1  PLAYLO 24  PLAYHI 127  OUTPUT 255  VOSCL 50  TRANSPOSE 0
```

§8 argued this correspondence from two documents agreeing on paper. It now
holds on a machine.

### Finding G — §3's keygroup 161 enumeration is consistent with the XL reading

`KFXCHAN` reads **0** on a keygroup that has not been overridden, which is
`PRG` under the S3000XL enumeration §3 settled on. It is consistent with that
reading rather than proof of it — an unset byte is also 0 — but the base-S3000
alternative would have shown as `OFF`, and does not.

### Finding H — POLYPH stores 31 where the panel shows 32 (fixed)

Read off the front panel, program MIDI page: **polyphony 32**, where the byte
holds 31. The table's own notes said so — "0 to 31 (these represent polyphony
values of 1 to 32)" — and it was the one field deliberately *not* widened
during Finding E precisely because that 32 is a display value.

It was worse than a display slip. `encode_field` never validated against
`minimum`/`maximum`, only against the field width, so `set POLYPH 32` wrote a
literal 32 — one voice too many — and `set POLYPH 0` would have wrapped to
255 through the two's complement branch.

Fixed with a `display_offset` on `Parameter`: `describe_value` adds it,
`encode_field` subtracts it and now range-checks the result, so everything a
user reads or types is the panel's number and the stored form never leaves
`params.py`. `minimum`/`maximum` remain the *stored* range. POLYPH is the only
field in the table that needs it, and `tests/test_params.py` asserts that
stays true.

### Finding I — §4's note-name mapping is CONFIRMED on hardware

Panel, KEYGROUP > SPAN: `KG1 -> Low 24 / High 127`, and the same keygroup
menu renders the span as **`C_0 <-> G_8`**. The keygroup header read
`LONOTE = 24`, `HINOTE = 127`, which s3ked prints as `24 (C0)` and
`127 (G8)`.

Both the numbers and the names match. That pins the whole mapping, because
the two endpoints over-determine it: if 24 is `C0` then 60 is `C3` (three
octaves up) and 21 is `A-1`, which is exactly what §4 concluded from three
agreeing documents.

**§4 moves from settled-from-documents to confirmed-on-hardware**, and with
it every note name s3ked prints. It was the checklist's stated risk — "if the
panel disagrees about the octave, §4 is wrong after all" — and the panel
agrees.

One incidental: **panel keygroups are 1-based, the wire is 0-based.** `KG1`
on the display is keygroup selector 0 in every SysEx operation. Worth
remembering before reporting a finding against "keygroup 1".

### Finding J — the device repaints its own LCD, CONFIRMED (first write, 2026-08-10)

The project's first write to real hardware: `PRIORT` on program 0, `norm` ->
`hold`, via `s3kcli --allow-write set PRIORT 3 0`.

Two claims settled at once:

1. **Writes are acknowledged.** The command printed `PRIORT = hold`, which it
   only does after the device returns `REPLY`/ok. §1's second point holds, and
   given Finding A — where read-back can return a stale buffer — `REPLY` is
   not merely the better verification, it is the only sound one.
2. **The machine redrew its own screen, untouched.** The panel was sitting on
   MIDI > priority showing `norm`; it flipped to `hold` by itself the moment
   the write landed, with no key press.

   **Scope of that claim, established over the 724-write sweep in §12:** the
   machine repaints *the page currently displayed* when a value on it
   changes. It does **not** follow a write to some other page -- there is no
   navigation, no jumping to whatever parameter was touched. So a user
   watching one page sees edits to that page live, and sees nothing at all
   for edits elsewhere. That is exactly the behaviour s3ked needs and all
   eosed's hack ever bought; it is recorded here so nobody later reads
   "repaints its own LCD" as "the display follows the editor".

**So §1's first point is confirmed and eosed's forged Program Change stays
unported.** That workaround exists because EOS has no redraw command at all;
this family repaints by default, and the postpone bits really are opt-*out*.
There is now hardware evidence behind the instruction in CLAUDE.md not to port
it.

Not tested, and deliberately: the `Postpone` flags themselves. Bit 12 is
documented to leave the machine "in an undetermined state until the same
parameter is sent with this bit cleared", and there is no reason to go looking
for that state.

### Still not covered

Writes beyond this single scalar byte — nothing has exercised a whole-header
write, and `PDATA`/`KDATA` remain destructive-until-proven-otherwise. Also:
`AmbiguousDevice`, the widened `channels=` sweep, and `RSPACK` sample-data
transfer, which the allowlist excludes on purpose.

---

## §12 — Full write round-trip sweep (run 2026-08-10)

`probes/roundtrip.py`, modelled on the sibling eosed project's first full
write test against an E4XT Ultra (its RESOLUTION_NOTES §18). Per parameter:
read the original, write **A**, read, write **B**, read, write **A** again,
read, restore, read.

**Result: 179 of 183 parameters round-tripped exactly.** 724 writes, 1663
reads, 243 s at a doubled 0.1 s gap. No dropped replies, no device error, no
crash, and **nothing left unrestored**.

### Why the toggle, and why the interleaved read

A single write-then-read proves little here, because §11 Finding A showed an
out-of-range read returns the previous read's buffer. Two defences:

- **Toggle A -> B -> A.** An echo cannot follow a value back and forth.
- **Interleave.** Between every write and its read-back, a structure nothing
  is sweeping is read -- sample 3 on this machine -- which evicts the transfer
  buffer, so the read-back has to come from the header itself.

eosed's equivalent second pass was selecting away to another preset and
returning, which tested `PRESET_SELECT` scoping. Here the same shape tests the
buffer instead. Note what it therefore does *not* test: with a single resident
program there is no way to check that writing program 0 leaves program 1
alone. That needs a second program, created from the front panel.

### The four that did not round-trip, and what they are

| field | offset | wrote | read | its own description |
|---|---|---|---|---|
| `SLOOPS` | 16 | 0 | 1 | "Number of loops" |
| `SALOOP` | 17 | 255 | 0 | "First active loop (**internal use**)" |
| `SHLOOP` | 18 | 255 | 0 | "Highest loop (**internal use**)" |
| `SSPARE` | 134 | 0 | 1 | "Used **internally**" |

All four are machine-managed. `SLOOPS` was probed separately against 0, 2, 3
and 4 and **ignored every one**, holding 1 throughout — so it is not a
minimum-of-1 constraint, it is not writable at all.

Marked `readonly=True` in `params.py`. Their descriptions said "internal use"
all along; hardware confirmed what that meant.

### The finding that matters more than the four fields

**A `REPLY`/ok does not mean the value landed.** Every one of those four
writes was acknowledged. The device accepted the message and then kept its own
value.

That refines §11 Finding A rather than contradicting it. The complete rule:

- `REPLY`/ok proves the message was **accepted** — necessary, not sufficient.
- A read-back **at a valid index and offset** is reliable, and is the only
  thing that catches a silently-ignored write.
- A read-back at an **invalid** address is worthless: it returns the previous
  read's buffer (Finding A).

So verification needs both, and needs to stay in range. Neither alone is
enough, which is not what either §1 or §11 said on its own.

### What the sweep did not touch

66 parameters were skipped, each for a stated reason: 32 whose range holds a
single value (the `0..0` placeholders of §11 Finding E — nothing to toggle),
16 wider than two bytes (memory-layout descriptors in the sample header,
plus `RESERVED` and `TEMPER`), 8 internal addresses, 6 name fields (opt-in),
3 structurally unsafe by name (`GROUPS`, `PRGNUM`, `RESERVED`), 1 read-only.

### §12a — Second run: cross-program leakage, on two programs

A second program was created from the front panel for the purpose (two
keygroups), mirroring how eosed made its ten scratch presets. The sweep then
wrote **program 1 and both its keygroups** while snapshotting every structure
it was not addressing -- program 0, program 0's keygroup, the four sample
headers, and the keygroup slots that do not exist -- and diffing them
afterwards, byte for byte rather than field by field, so a leak into a span
the table does not describe would still show.

**271 of 271 parameters round-tripped exactly.** 1084 writes, 2486 reads,
357 s. **All 11 witnessed structures came back byte-identical.** Nothing
unrestored, nothing aborted.

So a byte-offset write goes exactly where it is addressed and nowhere else.
Combined with §12's result on program 0, the write path is sound across two
programs, three keygroups and a sample header.

**Keygroup selectors address independently**, checked separately because both
keygroups were *targets* in that run and would have overwritten each other
invisibly if the selector were ignored:

```
write 7 to program 1 keygroup 0 only  ->  kg0 = 7, kg1 = 255 (untouched)
write 9 to program 1 keygroup 1 only  ->  kg0 = 7, kg1 = 9
```

Note for anyone repeating this: the panel numbers keygroups from 1 and the
wire from 0 (§11 Finding I), so panel `KG1`/`KG2` are selectors 0/1 here.

Still untested: name/text writes, and every whole-structure operation.

---

## §13 — Name fields and the 41-character set, settled both ways (2026-08-10)

Decoding was proven on the first hardware session -- names came back readable.
**Encoding never had been**, and every name s3ked writes depends on it.
`probes/names.py` settles it. All 19 checks passed; everything restored.

### All 41 characters survive a write

The set is 41 characters and a name is 12 bytes, so four names exercise every
entry. Each was written to a scratch program's `PRNAME` and read back:

```
'0123456789 A'  ->  '0123456789 A'
'BCDEFGHIJKLM'  ->  'BCDEFGHIJKLM'
'NOPQRSTUVWXY'  ->  'NOPQRSTUVWXY'
'Z#+-.'         ->  'Z#+-.'
```

`AKAI_CHARSET` is correct in both directions. Nothing in it is mis-transcribed,
which was a live possibility given §2's provenance.

### The machine's own catalogue follows the write

After every rename, `RPLIST` was asked for the program list and agreed each
time -- and `RSLIST` likewise after a sample rename. That is a **different
opcode family** from the byte-offset read, so this is not us reading back our
own bytes: the machine re-indexed. Same independence argument as §11 Finding D.

### Padding confirmed, and the table's indices with it

Writing `"ABC"` produced raw bytes:

```
0b 0c 0d 0a 0a 0a 0a 0a 0a 0a 0a 0a
A  B  C  <---------- space ------->
```

`encode_name`'s space-padding assumption is right, and the bytes incidentally
confirm the character set's own indexing: `A` = 11, `B` = 12, `C` = 13,
space = 10. A name shorter than 12 reads back trimmed, so a round trip through
s3ked is stable.

### Name fields are of two kinds, and the difference matters

* **Labels** -- `PRNAME`, `SHNAME`, `MULTINAME`, `FXFILENAME` -- name the
  thing itself. Writing anything legal is harmless.
* **References** -- `SNAME1`-`SNAME4` -- say *which sample a velocity zone
  plays*. Writing an invented name there does not mislabel a zone, it points
  it at a sample that does not exist and the zone goes silent.

Tested accordingly: zone 1 was swapped between two samples that both exist
(`SINE` -> `SQUARE` -> back), and the sample rename went to `PULSE`, the one
sample no keygroup referenced. **Any tool offering to edit `SNAME*` should
offer a list of resident samples, not a text box.**

`MULTINAME` is writable too, which is the first time anything in the multi
file has been written.

### Still not settled

Whether a byte-offset name write inherits `PDATA`'s documented
delete-on-duplicate behaviour. The specification says a `PDATA` write whose
name matches an existing program deletes that program first; nobody knows
whether `PHEADER` does the same. This probe deliberately never writes a
colliding name. Settling it means renaming one program to exactly match
another and seeing whether the other survives -- cheap with two scratch
programs, and it would lift a constraint that currently makes every
whole-header write destructive-until-proven.

### §13a — The delete-on-duplicate-name rule does NOT extend to byte-offset writes (2026-08-10)

Deliberate collision test, run with both programs snapshotted verbatim first.
The specification says of `PDATA`: *"If the program name in data is the same
as that of any existing program, that program will be deleted first."*
Whether the byte-offset write (`PHEADER`) inherits that had never been tested,
and it is why `s3ked` treats whole-header writes as destructive.

Program 1 was renamed to exactly program 0's name:

```
BEFORE  programs = ['TEST PROGRAM', 'TEST .ROGRAM']   blocks 9/1006
        write PRNAME of program 1 <- 'TEST PROGRAM'   -> REPLY/ok
AFTER   programs = ['TEST PROGRAM', 'TEST PROGRAM']   blocks 9/1006
FINAL   programs = ['TEST PROGRAM', 'TEST .ROGRAM']   blocks 9/1006
```

**Both programs survived.** No deletion, no block change, and the rename
reverted cleanly. The hazard is specific to `PDATA`, exactly as the
specification scopes it -- it is a property of the whole-structure write,
not of touching the name field.

Two consequences:

1. **A byte-offset name write is safe**, and `s3ked` need not treat renaming
   as destructive. This is the write the editor actually uses.
2. **`PDATA`/`KDATA` are still destructive-until-proven.** Nothing here tested
   them; they remain unimplemented and out of the probes' allowlists. Do not
   read this section as clearance for those.

**The machine permits duplicate program names.** It held two programs called
`TEST PROGRAM` without complaint, so there is no uniqueness enforcement at
this layer -- the specification's "the use of a duplicate program name should
be avoided" is advice, not a constraint the device imposes. Anything
addressing a program **by name** is therefore ambiguous by construction: use
the index, which is what `PLIST` order gives and what every operation takes.

---

## §14 — The block identifier at offset 0, and the trap it guards (2026-08-10)

**Found by the sibling mpc2emu project, reviewing header dumps s3ked sent it.**
The dumps were wrong and mpc2emu caught it from the bytes alone.

### What went wrong

A dump loop read program 0, then keygroup 0, then asked for **program index 1**
— and got the keygroup back, three reads running. 576 bytes of the wrong
structure went into a handoff labelled as a program and two keygroups.

The mechanism is §11 Finding A, recorded here days earlier: this layer answers
an out-of-range extended read with **the previous read's buffer** rather than
an error. The hazard was documented and then not defended against, in this
project's own code, which is the whole reason this section exists.

### The guard that was already in the table

Every structure carries a **block identifier at offset 0**:

| region | value | our table |
|---|---|---|
| program | `0x01` | *was missing* — the source document starts at offset 1 |
| keygroup | `0x02` | `KGIDENT`, "Block identifier (internal use)" |
| sample | `0x03` | `SHIDENT`, "Block identifier" |
| multipart | `0x01` | not listed; shares the program value |

Two of the three were named in `params.py` all along. `PRIDENT` is now added
for the program, with hardware cited as its source rather than the document.

`s3k.bridge.BLOCK_IDENT` checks it on **every** header read starting at
offset 0, and raises rather than returning. Verified to fire, not merely to
exist:

```
prime with a keygroup read     -> byte0 = 0x02
read program index 99          -> DeviceError: block identifier is 0x02,
                                  expected 0x01 -- this is a keygroup block
valid read afterwards          -> program 0 byte0 = 0x01
```

`multipart` cannot be told from `program` this way and is deliberately not
listed; a wrong multipart read is not detectable by this check.

### The lesson worth more than the guard

The failure was not that the hazard was unknown. It was documented, in this
file, by this project. **A finding recorded but not enforced in code is a
finding you will walk into.** §11 Finding A should have become a check the day
it was written; instead it became a paragraph, and the paragraph did not stop
576 bytes of nonsense leaving the project.

### Two answers that came out of the same review

**Program bytes 115-191 are all zero, across eleven resident programs** — one
synthetic and ten from a commercial library with five keygroups each and
populated modulation matrices. Our table describes 115 of 192; the remainder
looks like padding rather than structure.

**Sample bytes 171-191 are NOT zero and our table describes none of them.**
Our table stops at 141. On machine-authored samples 141-191 is all zero, but
library samples carry consistent structure at 171-191 (`00 08 00 00 00 ff ff
ff ff ff XX ff ...` in one shape, a different one in another). It is not text
— the values fall outside the 41-character set. **21 bytes of a structure we
read and write are undescribed.** mpc2emu's sample header comes from real
media rather than the document, so that is the source most likely to name them.

---

## §15 — Modulation sources: 0 means "no source", and reading it as data cost a correction (2026-08-10)

The thirteen program-header modulation *source* fields, plus the three for
filter 2, are single bytes whose meaning lives in a **separate table** in the
S2800/S3000/S3200 document, headed "Values used to represent Modulation
Sources". `params.py` carried the fields with the range `0..255` and no
enumeration, so they read as bare integers.

```
0: No Source          5: Note-on velocity   10: Env2
1: Modwheel           6: Key                11: !Modwheel (value at note-on)
2: Bend               7: LFO1               12: !Bend     (value at note-on)
3: Pressure           8: LFO2               13: !External (value at note-on)
4: External           9: Env1               14: Env3
```

**The cost of not carrying it.** Asked by the sibling mpc2emu project what
thirteen non-zero bytes at offsets 76-88 were, this project answered
correctly that they are modulation source assignments — and then added that a
writer leaving them zero "is assigning source 0 to every destination, which is
a real routing rather than a neutral default". That is wrong. **0 is
"No Source": the slot is off.** mpc2emu acted on it and described their prior
output as a routing nobody asked for; the correction was sent in the next pass.

Now wired: all sixteen fields carry `MOD_SOURCES`, so `describe_value` renders
`no source` and `LFO2` rather than `0` and `8`, and a test asserts it.

### What the machine actually holds, and why the fix still mattered

Both a panel-authored program and a library one assign all thirteen sources to
sensible defaults — LFO2 on pan, velocity on filter, Env2 on pitch — while
leaving nearly every *amount* at zero. So an assigned source with a zero
amount is inert, and all-zero sources are inert too: audibly identical.

The difference appears when a user raises an amount. And it bites for real on
converted material: a library program here holds `MODSPAN2 = LFO2` with
`MODVPAN2 = 25`, a live modulation. **A converter that carries an amount
across without the matching source lands the amount on "No Source", where it
is silently inert.**

### The general shape, which is the part worth keeping

This is the second time in two days a field has been misread because its
meaning lived somewhere the table did not reach — §11 Finding E was ranges
that dropped a sentinel documented only in prose, and this is an enumeration
documented only in a separate section. **Where the specification splits a
field from its meaning, the transcription has to put them back together, or
the field will be read as a number and reported as a fact.**

---

## §16 — The first filter sweep failed, and how (2026-08-10)

Attempted with everything green on both sides. **It produced no curve**, and
the three reasons are all ours, not the machine's.

### 1. `jack_rec` never exited, so the sweep blocked rather than ran

Each point spawns `jack_rec -d N` and waits. One of those sat **24 minutes
into a 7-second recording**, ignoring SIGTERM. The sweep got through a point
or two in fifteen minutes and was killed by its own `timeout`.

Worse, the orphan wedged the JACK **server**: after it, `jack_lsp` and every
new `jack_rec` hung, and killing every recorder including with `-9` did not
clear it. `jackd` stayed alive and simply stopped accepting clients. Recovering
that needs a JACK restart, which on this bench means tearing down the user's
whole graph — Carla, the mididings bridges, monitoring — so it is the user's
call, not the probe's.

**Before running a sweep again:** bound every recorder with an explicit
timeout and reap it, rather than trusting `-d` to terminate the process. An
orphaned JACK client is not merely a leaked process; it can take the server
with it.

### 2. `finally` does not survive SIGTERM — the restore did not run

The snapshot/restore added earlier the same day (§ commit `e8614c3`) was
correct and still did nothing, because `timeout(1)` sends SIGTERM and CPython
exits on it **without unwinding**. Program 0 was left with `OUTPUT = 0`
(routed off the main outputs), `ATTAK1 = 0`, `DECAY1 = 0`, `RELSE1 = 0` and a
mid-sweep `FILFRQ = 52`. Ten parameters wrong, silently — precisely the
failure the restore existed to prevent, reintroduced through the one exit path
it did not cover.

Fixed by turning SIGTERM/SIGHUP/SIGINT into an exception for the duration of
the sweep. **Any cleanup that matters and might run under a timeout, a cron or
a supervisor needs the same treatment**; a `finally` or a context manager is
not enough on its own.

### 3. The runtime estimate counts only recording time

The sweep reports "about 5.8 min unattended" for 50 points: `LEAD_IN + hold +
gap + TAIL` per point. It ignores that `_midi_out()` opens a **fresh ALSA
client per note** and `jack_rec` spawns a **JACK client per point** — 51 of
each, created and destroyed. The estimate is structurally optimistic and
should not be used to size a timeout.

### What recovered it

The 192-byte header dumps captured verbatim for the sibling mpc2emu project.
The originals were decoded straight out of the hex in this project's own
handoff file and written back, all ten verified by read-back. **Ground truth
kept for one purpose paid for itself in another** — worth remembering next
time a verbatim capture looks like an indulgence.

### Still unanswered, and the reason the sweep mattered

mpc2emu raised it and it is not settled: the filter sweep's reference band is
50-100 Hz, and a sawtooth has **no energy below its fundamental**. A 256-word
single cycle at 44100 sounds at ~172 Hz, so the band may contain nothing but
noise floor — and `ref_band` is where the **0 dB reference level** is taken,
not where the corner is measured. A band with no source energy is not a quiet
passband; it is noise being used to define zero.

`corner_frequency` already documents the signature of exactly this failure —
"a whole sweep floors at one value and looks like a filter that stops moving"
— and has a flatness guard that returns NaN. Whether it fires here is
unmeasured. **The cheap decisive test is to record one note and find the
fundamental**, which needs the audio path back.

### §16a — What the resident SAWTOOTH actually is (measured 2026-08-10)

The question §16 left open, settled by one note, one capture and one FFT — and
it went against both projects' predictions.

**Measured**: dominant fundamental **260.9 Hz** (autocorrelation), strongest
bin 260.7 Hz. Reference band 50-100 Hz sits **+60.5 dB above the same band
with the machine silent** (14.88 against 0.014), so it is emphatically not
noise.

**But the source is not a single-cycle sawtooth.** The strongest bins are

```
261, 264, 32, 524, 196, 258, 521, 100 Hz
```

and by band, 250-275 Hz reads 57.7, **20-50 Hz reads 23.8** (second strongest),
150-200 reads 17.1, 50-100 reads 14.4. That is a harmonic series spaced about
**32.7 Hz**, and 261.6 / 8 = 32.7 exactly. Every observed peak lands on it:
32, 65, 98, 131, 163, 196, 229, 262.

So the sample's **loop repeats near 32.7 Hz with roughly eight cycles inside
it**, and what looked like the fundamental is its 8th harmonic. The true
fundamental is well *below* the reference band, which is why 65 Hz and 98 Hz
are genuine harmonics inside 50-100 Hz.

### Who was wrong about what, since both of us were

- **mpc2emu** predicted 261.6 Hz for note 60 and was **right** about the
  pitch. Their inference — a sawtooth has no energy below its fundamental, so
  the band is noise — was sound reasoning that happens not to apply, because
  the source is not the waveform its name suggests.
- **This project** "corrected" them to 172.3 Hz from the single-cycle
  arithmetic (256 words at 44100) and was **wrong**: the machine retunes to the
  root pitch, so note 60 sounds at concert C4 whatever the cycle length. The
  correction was withdrawn.
- **Neither** predicted the 32.7 Hz periodicity, which is the thing that
  actually decides the question.

**The lesson is about method rather than about filters.** Two projects reasoned
carefully from a sample's *name* and its documented length, reached opposite
conclusions, and both missed the structure. A two-second capture settled it
immediately. Where a cheap measurement exists, take it before the third round
of argument.

### What this does not license

**Not** "the reference band is fine". The band contains signal; whether it is
*flat* enough to define 0 dB is a separate question, and 20-50 Hz reading 23.8
against 14.4 in 50-100 says the region slopes. That is exactly what
`ref_flat_db` exists to catch, and it remains untested here. The sweep is worth
**running** rather than redesigning — it is not yet worth trusting.

### §16b — The eight cycles are not identical, and the clustering test has a blind spot

Two refinements from mpc2emu, both verified here rather than taken on trust.

**The cycles inside the loop differ from each other.** §16a said the loop
repeats near 32.7 Hz with about eight cycles in it. The sharper statement:
*if all eight cycles were identical, the spectrum could contain only multiples
of 261.6 Hz.* Checked against the measured bins:

```
 32 Hz = 0.12 x 261.6   not a multiple
100 Hz = 0.38 x 261.6   not a multiple
196 Hz = 0.75 x 261.6   not a multiple
261, 524 Hz             multiples, as expected
```

So the sub-261.6 content can only come from cycle-to-cycle variation within
the loop. The object is an **eight-cycle sample whose cycles are not the
same**, not a single cycle looped. That is precisely why "a sawtooth has no
energy below its fundamental" failed as an argument: the reasoning was right,
the object was not a sawtooth in the sense assumed.

**The harmonic-clustering check cannot see snapping at the top of the range.**
With a 32.7 Hz series a snapping estimator is never wrong by more than
`f0/2 = 16.35 Hz`, so against measurement noise the error disappears:

| noise | snapping invisible above |
|---|---|
| 2 % | 818 Hz |
| 5 % | **327 Hz** |

mpc2emu verified it costs *power* rather than *specificity* — a genuine sweep
still sits at chance for both candidate fundamentals, so the denser series does
not manufacture false positives. But at a realistic 5 % noise the test is blind
over most of the useful filter range, which is worse than the 818 Hz figure
suggests.

**So a "clean" clustering verdict is not evidence of a clean sweep above a few
hundred hertz.** The low end of `FILFRQ` is where that check is informative;
the high end is where corruption would hide from it. Knowing an instrument's
blind spot before reading its verdict is the point — a check believed to cover
ground it cannot see is the same failure as a guard that cannot fail.

---

## §17 — `FILFRQ` does not audibly move the filter (measured 2026-08-10)

**The question the whole calibration existed to answer, answered — negatively.**
Before fitting any curve, two spot checks across the parameter's full range.

### The corner does not track the parameter

Reference taken at `FILFRQ 99`, then measured across the range:

```
FILFRQ   0 -> 14.11 kHz      FILFRQ  55 -> 14.08 kHz
FILFRQ  12 -> 14.14 kHz      FILFRQ  70 -> 13.97 kHz
FILFRQ  25 -> 14.10 kHz      FILFRQ  85 -> 14.10 kHz
FILFRQ  40 -> 14.08 kHz
```

**Ratio 1.00 over the whole range.** The ~14.1 kHz figure is stable to better
than the 0.39 % measurement noise, so it is a real feature of the signal — but
it does not depend on `FILFRQ`, which means it is the *source's own bandwidth
limit*, not the machine's filter.

### And it makes no difference to the level either

The corner measurement could in principle be at fault, so the cruder and more
robust check: RMS across the same range.

```
FILFRQ 0 -> -26.76 dB   ...   FILFRQ 99 -> -25.46 dB
```

**1.30 dB across the entire span**, non-monotonic, comparable to the run-to-run
spread. A low-pass swept from closed to fully open cannot do that.

### What this rules in and out

- **Not the measurement.** Repeatability is 0.39 % (five takes at one setting,
  sd 54.55 Hz on 13.9 kHz), `ref_flat_db` passes, and the rig restores cleanly.
  The instrument is sound; the thing it was pointed at does not move.
- **Not the write path.** §12 established writes land and are acknowledged, and
  the restore verified 23/23 by read-back each run.
- **Round-tripping proved nothing about meaning.** `FILFRQ` round-trips
  perfectly in §12's sweep — the byte stores and returns. That was never
  evidence that the byte *is* the filter frequency, which is exactly the
  hazard §2 warns about: a wrong offset writes somewhere real and reads back
  faithfully.

So either **keygroup offset 7 is not the basic filter frequency**, or the
filter needs something else engaged before it does anything — a mode, a
resonance, or a modulation amount the neutraliser sets to zero. Note the sweep
zeroes `MODVFILT1/2/3` and `VFREQ1` and `K_FREQ` deliberately; if the filter
only acts through one of those, neutralising them removes the very effect
being measured.

**To close this:** set `FILFRQ` from the front panel and watch whether the
value at keygroup offset 7 changes. That is a one-line check and it separates
"wrong offset" from "filter not engaged" outright, which no amount of
measurement from this side can.

### Why this is a better outcome than a curve

A 50-point sweep would have produced a clean exponential fit with an excellent
r² — through 50 points of a constant. The sibling mpc2emu project's warning was
exact: *an r² can be 0.999 and still be fitting the wrong thing.* Two cheap
spot checks, six recordings each, caught it before it became a number in
anyone's documentation.

---

## §18 — RETRACTION: every audio measurement before this was of the wrong program

**§16a and §16b are withdrawn.** Their spectral analysis is real data honestly
measured, and it describes something nobody was asking about.

### What was wrong

All eleven resident programs sit on `PMCHAN = 0`, MIDI channel 1. Every note
played into channel 1 therefore sounded **program 0 layered under ten
commercial library programs**, and program 0's contribution was buried.

Proven three ways:

1. `SINE`, `SAWTOOTH` and `SQUARE` in program 0's zone 1 all produced
   **identical spectra** — impossible if program 0 were being heard.
2. Setting program 0's keyrange to 100-127, excluding note 60 entirely, left
   the signal **unchanged**.
3. Moving program 0 alone to MIDI channel 2 made the sample choice matter
   immediately: `SINE` gave peaks at 261/264/258/267, `SAWTOOTH` gave
   261/264/**524**/521 — a fundamental with its second harmonic.

### So what the earlier sections actually measured

The "32.7 Hz series with eight non-identical cycles" was the **library
programs' combined output**, not the resident `SAWTOOTH`. Isolated, `SAWTOOTH`
shows a plain 261.6 Hz series with its harmonics where they belong. The
elaborate structural inference — that the loop held eight differing cycles —
described a sum of ten unrelated programs. It was arithmetically sound and
about nothing.

**§17 stands in its conclusion but not its reasoning.** `FILFRQ` did nothing
audible, but the reason is now known to be that program 0 was inaudible, not
that the parameter is inert. **Whether `FILFRQ` works is once again unknown.**

### The lesson, which is the whole point of recording this

Everything about the *instrument* was validated carefully: 0.39 % repeatability
over five takes, `ref_flat_db` passing, restores verified 23/23 by read-back,
a bounded recorder, a signal-safe teardown. Two projects then spent hours
reasoning about the numbers it produced.

**Nobody validated what the instrument was pointed at.** The one-line check —
does changing the source sample change the recording? — would have caught it
before any of it, and is now the first thing any audio measurement here must
do. It is the same failure as the wrong-DIN case the sibling mpc2emu project
warned about, arrived at from a direction neither of us was watching: not the
wrong cable, the wrong *program*.

### How to measure anything audible on this machine

**Give the program under test a MIDI channel of its own.** `PMCHAN` is program
offset 16; set it to a channel no other resident program uses, drive that
channel, and restore it afterwards. Verify isolation before trusting a single
number by swapping the zone's sample and confirming the spectrum changes.

### Still open, and now genuinely open again

- Does `FILFRQ` move the filter? Unknown. §17's measurement is void.
- The front-panel check remains the cheapest discriminator if it turns out not
  to.

---

## §19 — The filter works. `FILFRQ` measured, with isolation verified (2026-08-11)

**§17 is refuted.** `FILFRQ` moves the filter cleanly; the earlier "no effect"
was §18's buried-program problem and nothing else.

Program 0 moved to a MIDI channel no other resident program uses (`PMCHAN`,
program offset 16), zone 1 pointed at `SAWTOOTH`, `FILQ` 0, and
`verify_isolation` confirming a **55.9 dB** drop when the keygroup is silenced
*before* any number was believed.

```
FILFRQ   RMS dB   centroid Hz        FILFRQ   RMS dB   centroid Hz
     0   -90.14      10971               55   -39.69         945
    10   -87.44      10534               60   -39.28         919
    20   -78.99       9226               65   -39.21         921
    30   -67.62       6450               70   -39.13        1009
    40   -54.72       2829               75   -39.07        1175
    50   -42.59       1101               80   -38.91        1443
                                         85   -38.90        1813
```

**47.5 dB of monotonic level change from `FILFRQ` 0 to 50.** At 0 the filter is
shut: −90 dB is the noise floor, and the "centroid" of 11 kHz is broadband
noise rather than a tone. As it opens the fundamental comes through and the
centroid falls toward it.

**Above ~55 the level plateaus and the brightness takes over** — 945 Hz to
1813 Hz between 55 and 85, for less than 1 dB of level. That is exactly a
low-pass whose corner has passed the fundamental: no more level to gain, more
harmonics admitted. **RMS is the informative measure below the corner and
spectral brightness above it**, and a calibration that watched only one of them
would misread half the range.

### Two things this settles about the parameter table

- **Keygroup offset 7 is the basic filter frequency**, confirmed on hardware.
  mpc2emu writes 99 there and 99 is "wide open", which their disc images now
  carry correctly rather than coincidentally.
- **`FILQ` at 149 is not needed to make the filter act.** It rested at 0
  throughout and the filter still swept its full range.

### And why filter 2 showed nothing

Not a table error: **FILTER2, the TONE page and ENV3 require the optional
IB304F filter-bank board** (§ the manual quotes the machine's own refusal,
`2nd filter board IB304F not fitted!`). On an S3200 that LSI is standard; on an
S3000XL it is an option. The fifteen affected fields are now marked in
`params.py` — they exist in the header on every machine and simply do nothing
without the board, so nothing on the wire distinguishes them.

### Rig note: JACK wedges about every eight recordings

`jack_rec` stops exiting roughly every 8-10 captures, and the wedge takes the
server with it. The bounded recorder (§16) catches it every time — kills the
process, raises a clear error, and the restore runs — so no measurement is
corrupted and nothing is left unrestored. Recovery is
`~/autostartaudio.sh minimal`. **A long unattended sweep is not currently
possible; measure in chunks of under eight points.**

---

## §20 — `FILFRQ` calibrated: the map from an integer to hertz (2026-08-11)

**The thing the project set out to obtain.** `FILFRQ` is documented as "basic
filter frequency, 0 to 99" with no unit anywhere; an editor showing a cutoff,
or a converter turning a real cutoff into an Akai program, needs the map.

```
Hz = 6.998 * exp(0.07384 * FILFRQ)

  valid FILFRQ 50-90, max error 3.6 %, r2 0.99963 (on log Hz)
  x2.092 per 10 units  ~  one octave per 9.4 units
```

### Measured

Program 0 alone on its own MIDI channel, `verify_isolation` confirming 59 dB
before any number was taken, zone 1 on `SAWTOOTH`, `FILQ` 0, reference
spectrum at `FILFRQ` 99.

```
FILFRQ   corner Hz   fitted    err        FILFRQ   corner Hz   fitted    err
    40       155.3    134.2  -13.6 %*         75      1755     1778.1   +1.3 %
    50       281.2    280.7   -0.2 %          80      2566     2572.1   +0.2 %
    55       404.3    406.1   +0.4 %          85      3700     3720.6   +0.6 %
    60       606.4    587.4   -3.1 %          90      5511     5382.1   -2.3 %
    65       852.5    849.7   -0.3 %          95      9360     7785.4  -16.8 %*
    70      1187      1229.2  +3.6 %          30       NaN      --      guard
                                              99  2.13e4       --      degenerate
```
`*` outside the fitted interval, and shown because they establish where it ends.

### Why the range stops where it does, at both ends

- **Below ~50** the corner approaches the 50-100 Hz reference band, which
  compresses the measurement — and at `FILFRQ` 30 `ref_flat_db` returns **NaN**
  rather than a wrong number. **The guard fires on hardware exactly as its
  docstring says it should.**
- **Above ~90** the corner heads for Nyquist and the estimate saturates.
- **`FILFRQ` 99 is degenerate by construction**: the reference is taken there,
  so the take is compared with itself and no attenuation is found until the
  band edge. It reads 21 kHz and means "wide open", not a corner.

The two excluded points miss by 13.6 % and 16.8 % against the interior's
3.6 %, so they establish their own exclusion rather than being trimmed to
flatter the fit.

**A stronger reason, from mpc2emu's cold review, and it does not depend on the
fit at all.** Both excluded points deviate in the **same direction** — measured
*above* the fitted line — and for two different physical mechanisms:

- at `FILFRQ` 40 the filter is nearly shut, so the broadband **noise floor**
  contributes high-frequency energy and pulls the corner estimate **up**;
- at `FILFRQ` 95 the corner has passed the **source's own bandwidth** (~14 kHz,
  measured independently in §17), so the estimate is pulled **up** toward that
  limit.

Contamination from below and from above, both biasing upward, for unrelated
reasons. *Two endpoints failing the same way by coincidence would be
suspicious; two failing in ways that each independently predict an upward bias
is a reason to exclude them* — and that argument stands whatever the residuals
happen to look like. It is a better justification than the size of the gap,
which is all this project had offered.

(Error signs differ between the two projects only by denominator: this file
reports `(fit-measured)/measured`, mpc2emu reports `(measured-fit)/fit`.
1/1.158 = -13.7 % reproduces -13.6 %. No disagreement.)

### The note and the reference band have to be chosen together

The sweep shipped with **note 60 and a 50-100 Hz reference band**, which cannot
work: the isolated `SAWTOOTH` sounds at 261.6 Hz at note 60, so the band sits
entirely *below* the fundamental, and a sawtooth has no energy below its
fundamental. The band that defines 0 dB would have been noise.

At **note 24** the fundamental is ~32.7 Hz and its 2nd and 3rd harmonics
(65 Hz, 98 Hz) fall inside the band. The sweep now sets `note=24` explicitly
and a test asserts a harmonic of the sounding pitch lands inside the band.

### The rig fix that made a full curve possible

`jack_rec` is spawned per capture and wedges the JACK **server** roughly every
8-10 recordings (§16, §19). Replaced with an **in-process JACK client**
(`JACK-Client`, one client for the whole session, registered once). Nineteen
consecutive captures across two runs with **no wedge at all**, where the
previous ceiling was eight. `jack_rec` remains as a fallback where the binding
is missing.

**The create/destroy cycle was the problem, not the recording.** Any rig
spawning a JACK client per measurement has the same exposure.

---

## §21 — `KGTUNO` is 1/256 of a semitone, not cents — and the estimator could not see it (2026-08-11)

Two findings, and the second had to be fixed before the first was measurable.

### The tuning unit

```
cents = 0.39167 * KGTUNO - 0.31        r2 = 0.999823    (LINEAR, not exponential)

  1/256 semitone = 0.390625 cents/unit
  measured / predicted = 1.0027
```

**The low byte is a binary fraction of a semitone**, exactly as the parameter's
own note hints ("fraction is binary") and contrary to the "cent:semi" phrasing
that the sweep's hypothesis was built on. A step of 1 moves **0.39 cents**, not
one cent, and `KGTUNO = 50` gives **19.5 cents**, not 50.

`KGTUNO` is two bytes at keygroup offset 5-6: the low byte is the 1/256
fraction, the high byte semitones. Anything converting a real tuning offset
into an Akai program must multiply by 256/100, not by 1.

### The estimator could not resolve its own subject

The first run returned `0, 0, 0, 0, 0, 0, 9.486, 19.02` cents. Those are not
parameter steps — **they are autocorrelation lag steps.**

`fundamental_hz` took `int(np.argmax(...))`, so its resolution was one integer
lag: `1200*log2((lag+1)/lag)`. At note 60 the period is ~183 samples and the
floor is **9.4 cents** — coarser than the parameter being measured. The
instrument was quantising the answer to its own step size and reporting the
quantisation as data.

Fixed by parabolic interpolation through the three points around the peak:
**9.4 cents -> 0.07 cents at note 60**, and better than 0.2 cents from 32.7 Hz
to 1 kHz.

### And a second defect in the same function, found by testing the fix

With the interpolation in, 32.7 Hz still came back as **2000 Hz** — the top of
the search range, silently wrong by six octaves. Two causes:

1. `lo` defaulted to **40 Hz**, above the 32.7 Hz that note 24 sounds at —
   which is the note the filter sweep now uses. Lowered to 20 Hz.
2. Raw autocorrelation stays high for small lags, because a low-frequency
   waveform barely moves over a few samples, so a plain `argmax` lands at the
   start of the window rather than on the period. The peak search now begins
   after the correlation's **first zero crossing**, which the true period
   cannot precede.

**A pitch below the search floor was not merely imprecise, it was confidently
wrong** — the same shape as everything else this session: the instrument
answering a question it could not see.

### A sweep-level defect this exposes

`summarise` fits `a*exp(b*x)` to **every** sweep. Tuning is linear, so the
tool reported `cents = 1.05 * exp(0.067 * KGTUNO)` for a relationship that is
a straight line with r2 0.9998. The fit model belongs to the sweep, not to the
harness -- recorded in TODO rather than fixed here.

---

## §22 — The remaining sweeps: five more curves, one real pan law, one bug (2026-08-11)

Run with program 0 isolated on its own MIDI channel and `verify_isolation`
confirming ~63 dB each time.

### Loudness — the linear hypothesis held

```
dB = 0.642719 * PRLOUD - 87.63          r2 0.9933, 20 points, no NaN
```

0.64 dB per unit, ~63 dB of range across 0..99. Declared linear as a
hypothesis because the measurement is already in dB; the data agreed.

### Pan — neither of the harness's two shapes, and the disagreement flag earned itself

Declared linear. The tool reported:

> `fit_model_disagrees: declared linear (r2 0.79085) but the data prefers exp
> (r2 0.95330)`

**Both are wrong.** It is a **constant-power pan law**:

```
balance_dB = 1.2124 * 20*log10(tan(theta)) - 0.54     theta = (PANPOS+50)/100 * pi/2

  r2 0.999578, correlation 0.99979    against  exp 0.953,  linear 0.791
```

The endpoints give it away: `PANPOS` -50 measures -62 dB and +50 measures
+62 dB, i.e. one channel fully off, which no polynomial or exponential
approaches. **For editor purposes the middle is simply linear:**
`dB = 0.38488 * PANPOS - 0.58` over -30..+30, r2 0.9983.

This is the case the fit comment predicted in advance — "a real pan law is
often sin/cos, which is neither shape, so disagreement would be informative
rather than a defect". Declaring a hypothesis and having the tool contradict it
found the answer faster than declaring nothing would have.

### Amplitude envelope — three exponentials

Fitted over the range each is actually resolvable:

```
attack   s = 0.00023924 * exp(0.10463 * ATTAK1)   r2 0.99613   x2.85/10 units
decay    s = 0.00071981 * exp(0.08793 * DECAY1)   r2 0.99664   x2.41/10 units
release  s = 0.0017101  * exp(0.09797 * RELSE1)   r2 0.99982   x2.66/10 units
```

**Bounded at both ends, and both bounds are the instrument rather than the
machine:**

- **Low end.** Attack 0-30 all report 0.005-0.010 s, decay 0-20 all 0.010 s,
  release 5-30 all 0.020-0.030 s. Those are `envelope()` hop steps, not
  parameter steps — the same failure as §21's autocorrelation lag, in a
  different measurement. Below those values the envelope is faster than the
  detector's time resolution.
- **High end.** Release 80, 90 and 99 come back **NaN**, because the release
  outlasts the 2 s recording tail. Honest rather than truncated, but it means
  the release fit is measured over 40-70 and its value at 99 (27.9 s) is
  **extrapolated seventeen-fold beyond the last real point.** Do not quote it
  as measured.

### The bug that made the first release run useless

**All fourteen release points returned NaN.** The in-process recorder sent
note-off *after* the capture had finished, so the release phase was never in
the recording. A measurement of something that happens after an event needs the
event inside the window — obvious once stated, invisible in the output, and it
failed loudly (all NaN) rather than producing a plausible curve, which is the
only reason it was caught immediately.

Fixed: `record()` takes a `then`/`after` pair and fires note-off inside the
window, with a test asserting note-off precedes the end of capture.

---

## §23 — PTUNO shares KGTUNO's scale; STUNO stores and does nothing (2026-08-11)

mpc2emu asked whether `PTUNO` and `STUNO` share the 1/256-semitone scale
measured for `KGTUNO` in §21 — their writer had begun assuming so. The two
answers are different.

```
                measured c/unit    x (1/256 semitone = 0.390625)
  PTUNO  (program 65)   0.3928 / 0.3866      1.006 / 0.990    SAME SCALE
  KGTUNO (keygroup 5)   0.3928 / 0.3866      1.006 / 0.990    (re-confirmed)
  STUNO  (sample 20)    0.00003 / -0.00016   0.000            NO EFFECT
```

**`PTUNO` is 1/256 of a semitone**, same as `KGTUNO`. Assuming they share the
scale is correct.

**`STUNO` does nothing at all over SysEx** — and it is not a failed write.
Values 32, 64, 256 and 1000 were all written and read back **exactly**:

```
wrote 32   -> read 32     wrote 256  -> read 256
wrote 64   -> read 64     wrote 1000 -> read 1000
```

The byte stores and returns faithfully while the pitch does not move by a
thousandth of a cent. **This is §14's round-trip trap on a live field**: a value
that round-trips perfectly is not thereby a value that means anything.

Whether `STUNO` matters on a *disc* — applied at load time rather than
recalculated — is a question SysEx cannot reach, and it is the boundary between
this project and mpc2emu's. Their writer emits it; nothing here can confirm the
scale it should use.

### The measurement bug this exposed, which was mine and recent

The first run of this check returned **+1212 cents** for `PTUNO = 32` — an
octave, obviously wrong, which is the only reason it was caught.

`fundamental_hz` was reporting note 60 as **130.8 Hz instead of 261.6**, an
octave low. Autocorrelation is nearly as strong at twice the true period, so
the global `argmax` had landed on the sub-multiple. **The zero-crossing fix
added in §21 made this more likely, not less** — I introduced it a few hours
earlier while fixing a different failure in the same function.

It did **not** show against the synthetic tones the tests use, because three
harmonics are too clean to make the sub-multiple competitive. A real sawtooth
with forty is not.

Fixed by preferring the **shortest** period whose correlation is within 90 % of
the best, checking 1/5, 1/4, 1/3 and 1/2 of the candidate. Tests now use a
40-harmonic sawtooth rather than a 3-harmonic tone.

**Three bugs in one function in one night** — integer-lag quantisation, a
40 Hz search floor, and now an octave error introduced by the fix for the
second. Each was found by a result that was implausible rather than by reading
the code, and the octave error was caught only because 1212 cents is
recognisably an octave. **A pitch estimator that is wrong by a factor of two is
the hardest kind to notice**, because every derived number stays
self-consistent.

---

## §24 — `LFORAT` measured, and all eight sweeps are done (2026-08-11)

```
Hz = 0.11867 * LFORAT - 0.04        r2 0.99948    LINEAR
     ~0.119 Hz per unit;  0..99 spans about 0 to 11.7 Hz
     exponential fit manages only r2 0.897
```

**The one sweep the old single-model harness would have got outright wrong.**
It fitted exponentials to everything, and would have reported
`Hz = 1.4426*exp(0.02367*LFORAT)` at r2 0.897 — plausible, wrong in shape, and
flagged only as "not trustworthy" rather than as the wrong model.

### The blocker was real but misdiagnosed, in both directions

The sweep carried `blocked_on`: *"the table has MODSLFOT/MODSLFOL as SOURCES of
modulation OF the LFO, not the LFO's own destination… confirm on the panel
which destination LFO1 drives."*

That is correct about `MODSLFOT/L/D` — they are sources modulating LFO1's own
speed, depth and delay, and their resting `6,6,6` is "Key". The sibling mpc2emu
project read those as destination assignments and withdrew the reading when
challenged.

But **the panel visit was never needed.** `LFODEP` sits beside `MWLDEP`,
`PRSDEP` and `VELDEP` — modwheel, aftertouch and velocity control of LFO1
depth. That is the classic vibrato structure, and it means **LFO1 drives pitch
and there is no destination parameter to find.**

**The actual obstacle was the measurement, not the routing.** `mod_hz` measures
*amplitude* modulation. Pointing an amplitude detector at vibrato returns a
clean nothing — the sweep would have produced zeros or noise and looked like a
dead parameter, exactly as `FILFRQ` did in §17 for a different reason.

Fixed by routing LFO1 into loudness through the assignable matrix,
`MODSAMP1 = 7` with `MODVAMP1 = 50`, which uses the source/amount pairing of
§15: **an amount without a source is inert, and so is a source without an
amount.** Both halves are now in the sweep's `prepare` with the reasoning at
the point of use, and `blocked_on` is cleared.

### The eight curves

```
filter    Hz         = 6.998      * exp(0.07384 * FILFRQ)     r2 0.9996  (50..90)
tuning    cents      = 0.391667   * KGTUNO - 0.31             r2 0.9998
loudness  dB         = 0.642719   * PRLOUD - 87.63            r2 0.9933
lfo-rate  Hz         = 0.11867    * LFORAT - 0.04             r2 0.9995
attack    s          = 0.00023924 * exp(0.10463 * ATTAK1)     r2 0.9961
decay     s          = 0.00071981 * exp(0.08793 * DECAY1)     r2 0.9966
release   s          = 0.0017101  * exp(0.09797 * RELSE1)     r2 0.9998  (40..70)
pan       balance_dB = 1.2124 * 20*log10(tan(theta)) - 0.54   r2 0.9996
```

Four linear, three exponential, one a constant-power law that is neither.
**A harness assuming a single shape would have been right about three of
eight.**

### The lesson the two projects settled on

mpc2emu's summary, and it is better than either of us had separately:

> *Self-consistency is not correctness, and every one of tonight's real bugs —
> theirs and ours — was found by something **outside** the system that produced
> the number: a disc dump, a channel change, a physical implausibility of
> 1212 cents.*

Every failure this session fits it. The parameter table round-tripping proved
only that it agreed with itself (§14). The isolation failure produced perfectly
repeatable measurements of the wrong program (§18). The octave error kept every
derived number consistent while halving the pitch (§23). **None was findable
from inside the system that produced it.**

---

## §25 — LFO1 drives pitch. Measured, not inferred (2026-08-11)

§24 concluded LFO1 drives pitch from the *shape of the table* — `LFODEP` sits
beside `MWLDEP`, `PRSDEP` and `VELDEP`, which is the classic vibrato
arrangement. That is an inference, and this project has spent two sessions
learning what inferences are worth. So: set, record, measure.

### Method

One held note, LFO1 at full depth, **no assignable routing at all**
(`MODSAMP1` 0, `MODVAMP1` 0) and LFO2 silenced (`PANDEP` 0), so anything that
moves is LFO1's own wiring. Four candidate destinations tracked over time in
short windows, then each series examined for a periodicity at the LFO's own
rate:

```
pitch       fundamental per window   -> vibrato
level       RMS per window           -> tremolo
brightness  spectral centroid        -> filter modulation
balance     L/R ratio per window     -> auto-pan
```

A control run at `LFODEP` 0 must show none of them.

### Result: pitch, unambiguously

```
LFORAT   predicted   measured in the PITCH series   prominence
    15     1.74 Hz            1.83 Hz                  68x
    30     3.52 Hz            3.51 Hz                  78x
    45     5.30 Hz            5.33 Hz                  69x
    70     8.27 Hz            8.29 Hz                  86x
```

`level` and `balance` peaked at 0.57 Hz in **both** the test and the control,
so that is a common artefact and not LFO-driven. With `LFODEP` at 0 the pitch
oscillation disappears and only 4-6x noise peaks remain, against 68-86x with it
on.

**This also corroborates §24 independently.** The `LFORAT` curve was measured by
routing LFO1 into *loudness* through the assignable matrix. Measuring *pitch*
modulation directly — a completely different signal path, a different detector
— reproduces the same rates within a few percent. Two unrelated routes agreeing
is worth more than either alone.

### The measurement trap, which caught me first

The first pass used a 100 ms analysis window and reported **twice** the expected
rate at `LFORAT` 45 and 70, while matching at 15 and 30. A clean factor of two
appearing above a threshold is almost never the machine.

At `LFORAT` 70 the LFO period is 121 ms, so a 100 ms window spans **83 % of a
cycle** — the pitch estimate averages over most of the modulation and the
series is distorted. Shortening the window to 30 ms fixed it, which in turn
required narrowing `fundamental_hz`'s search to 150-600 Hz, since it needs two
periods of its lowest searchable pitch and at `lo=20` that is 100 ms of audio.

**A window comparable to the period being measured cannot measure it.** Same
family as §21's autocorrelation lag and §22's envelope hop: the instrument's
own resolution masquerading as the answer. Third time this session, and the
only reason it was caught is that a factor of exactly two is implausible.
