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

## §20 — SUPERSEDED by §54 (the FILFRQ law only; the fold still stands) — `FILFRQ` calibrated: the map from an integer to hertz (2026-08-11)

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

---

## §26 — `SUSTN1` is dB-linear, `ATTAK2` has its own law, and one detector was never measuring (2026-08-11)

Three results from mpc2emu's two asks, and a correction to §25 that came out of
answering them.

### `SUSTN1` is linear in dB, not in amplitude

```
dB = 0.60832 * SUSTN1 - 88.84          r2 0.99995
relative to full:  dB_rel = 0.60832 * (SUSTN1 - 99)
```

The amplitude hypothesis fits at 0.899. Nine points, attack and decay zeroed so
the envelope sits at sustain, measured mid-note.

**mpc2emu's writer used `fraction * 99`**, which is out by 5 dB at 0.9, **24 dB
at 0.5** and 34 dB at 0.1. The correct conversion is
`SUSTN1 = 99 + 20*log10(fraction) / 0.60832`, and note that a request for
one-tenth maps to 66, not to 10 — the bottom of a source's range lands in the
middle of the machine's.

**Corroborated by a second control.** `PRLOUD` measures 0.6427 dB/unit and
`SUSTN1` 0.6084 — two independent level parameters, both dB-linear, within 5 %.
A single fit at r2 0.99995 would still be a single fit; two unrelated ones
agreeing is what makes it a property of the machine.

VinSamLib, relayed by mpc2emu, named the shape before the measurement landed:
*amplitude-linear versus dB-linear is a factor of nothing at the ends and
everything in the middle, so it passes any round-trip and any spot check at 0
and 99.* **The endpoints are exactly where a wrong law hides.**

### Envelope 2 is the FILTER envelope, and its law differs from envelope 1's

The manual heading is "ENV 2 - SHAPING THE FILTER", and program 0 rests with
`MODSFILT3 = 10 = Env2`. So an amplitude detector cannot see it; brightness
over time can. Routed through the assignable matrix (`MODSFILT1 = 10`,
`MODVFILT1 = 50`) with the filter part-closed to leave headroom:

```
ATTAK2:  s = 0.00073832 * exp(0.08963 * ATTAK2)    r2 0.99779, x2.450 per 10
ATTAK1:  s = 0.00023924 * exp(0.10463 * ATTAK1)    r2 0.99610, x2.847 per 10
```

**They are not the same law.** ENV2 is 1.7x slower than ENV1 at value 40 and
0.8x at 90 — the curves cross. Anything that assumed envelope 1's law for
envelope 2 would be wrong at both ends and right in the middle, which is the
`SUSTN1` failure mode again. mpc2emu declined to make that assumption on
general principle and was right for a specific reason.

Bounded 40..90 at both ends by the rig, as ENV1 was: below 40 the filter is
already open before the first analysis window, and at 99 the envelope does not
finish inside a 3 s note. The control -- `MODVFILT1 = 0` -- holds the centroid
flat at ~460 Hz throughout, which is the null the positive results are measured
against.

### CORRECTION to §25: the brightness row was never measured

§25 reported four candidate destinations for LFO1 and said pitch oscillated
while level, brightness and balance did not. **Brightness was not a null
result. It was a non-measurement.**

`ms.spectrum` drops `skip_s` (0.25 s) of attack and then needs a full 16384
sample FFT frame -- about 590 ms at 48 kHz -- and **returns empty arrays**
below that rather than raising. Fed 40 ms windows it produced 0 or NaN for
every window, which reads as "nothing is moving".

The §25 conclusion still stands: pitch oscillated at the LFO rate at 68-86x
prominence across four rates, with a control, and that is a positive result
independent of the other rows. But brightness should have been reported as not
measured, and was not.

`spectral_centroid` now exists for short windows, with tests asserting it works
exactly where `spectrum` gives up. **A detector that returns "nothing" for
"could not look" is the most dangerous kind this project has met** -- it is
indistinguishable from a real null unless you check the instrument against
something you know is there.

---

## §27 — The measured laws become a feature: units in and out (2026-08-11)

Eleven sweeps produced eleven curves, and until now they lived only in this
document. §20–§26 are where they were *found*; this is where they became
something the editor does. `s3k/scales.py` holds them as data, and two things
consume it:

- **Out.** `describe_value()` appends the physical meaning, so a pane reads
  `FILFRQ 80 (~2.57 kHz)` and `ATTAK1 70 (~363 ms)` — exactly the rendering
  the TODO item asked for, next to the enumerations it already showed.
- **In.** `s3kcli --allow-write set FILFRQ 500Hz 0 --keygroup 0` writes 58.
  A plain number is still a raw value; only a unit suffix takes the physical
  path, so `50` and `50Hz` can never be confused for one another.

### Three decisions worth keeping

**1. A fitted range is part of the law.** Outside it, `to_physical` returns
`exact=False` and `describe` prefixes the answer with `?`. `FILFRQ 20` shows
`?~31 Hz`, because nothing was measured below 50 and the sampler is free to do
something else down there. The alternative — printing `31 Hz` flat — would let
a fit masquerade as a specification.

**2. But the two ends of a range are not symmetrical, and refusing is not
automatically the safe choice.** `FILFRQ` 99 is *known* wide open: the
calibration took its 0 dB reference there and found no attenuation. That fact
outlives the curve, which stops at 90, so it is recorded in `endpoints` and
shown in preference to an extrapolation. This is the concrete lesson from
mpc2emu: they clamped a "fully open filter" request to the top of the *fitted*
range and every converted program came out audibly darker than before the
calibration existed. Caution made the output worse. `endpoints` exists so that
each such fact is recorded once, rather than each consumer inventing its own
policy at the edges.

**3. Two of the fitted constants were wrong to ship as measured.**

- `KGTUNO`'s fit had an intercept of −0.31 cents, so zero detune displayed as
  `~-0.3 cents`. That intercept is measurement bias; no tuning offset is no
  detune *by definition*. The law is forced through the origin.
- `PRLOUD` and `SUSTN1` were fitted to absolute dB, which is a property of the
  bench — the converter's gain, the interface's trim — and means nothing
  anywhere else. Both are now relative to the parameter's own maximum, which
  is a property of the sampler. `PRLOUD 99` reads `+0.0 dB`, `PRLOUD 50` reads
  `-31.5 dB`, and those numbers survive leaving the room.

The pattern in all three: a fit reproduces its inputs faithfully and is still
the wrong thing to publish. Deciding what a coefficient *means* is a separate
step from computing it, and only the first one can be done at the bench.

### One bug, found by a test rather than by reading

The unit-suffix table was hand-ordered longest-first and I got it wrong:
`"cents"` ends in `"s"`, so `50cents` matched the seconds suffix, tried to
parse `"50cent"` as a number, and returned "not a quantity". The fix is not a
better hand-ordering — it is sorting by length at module scope, so a suffix
added later cannot reintroduce it, plus a test that walks every pair of
suffixes where one ends with the other.

### What is guarded

`tests/test_scales.py`, 68 cases. The ones that matter are not the arithmetic:
a coefficient typo agrees with itself perfectly. They are the anchors — one
hardware reading per law, taken from the sweeps above, so a drifting constant
fails here instead of in somebody's ears — plus round-trip identity across
every fitted range, monotonicity, and the honesty properties: an unmeasured
parameter says *nothing* rather than guessing, and an extrapolation is marked.
`test_sustain_is_linear_in_decibels_not_amplitude` pins the §26 finding that
half amplitude is `SUSTN1` 89, not 50.

Still unmeasured, and therefore silent rather than guessed: `DECAY2`,
`SUSTN2`, `RELSE2`, `FILQ`, `LFODEP`, `LFODEL`, LFO2's rate, and the
velocity-modulation depths.

---

## §28 — SUPERSEDED by §58 (three of four laws; the RATE findings stand) — Envelope 2 measured, and one law found underneath all of them (2026-08-11)

The filter envelope is complete: `ATTAK2` re-measured, `DECAY2`, `SUSTN2` and
`RELSE2` new. But the numbers are the smaller half of this section. **Six
attempts were needed, and five of them failed in the instrument rather than
in the machine.** Each failure produced clean, plausible, entirely fictitious
data, which is the only kind worth writing down at length.

### The measurements

```
ATTAK2   tau = 0.000403868 * exp(0.09785 * v) s    50..85   r2 0.9999
DECAY2   tau = 0.00090950  * exp(0.09151 * v) s    45..85   r2 0.9903
RELSE2   tau = 0.00029835  * exp(0.09801 * v) s    55..76   r2 0.9954
SUSTN2   FILFRQ shift = 0.024645 * SUSTN2 * MODVFILT1       0..70   r2 0.991
```

### Finding A — there is one time-constant law, not six

| parameter | exponent | detector |
|---|---|---|
| `RELSE1` | 0.09797 | amplitude RMS |
| `RELSE2` | 0.09801 | filter cutoff, via ruler |
| `ATTAK2` | 0.09785 | filter cutoff, via ruler |
| `DECAY2` | 0.09151 | filter cutoff, via ruler |
| `ATTAK1` | 0.10463 | amplitude, **retired method** |
| `DECAY1` | 0.08793 | amplitude, **retired method** |

Three parameters, measured through two detectors that share no code path,
agree on the exponent to within **0.16 %**. That is not self-consistency —
the amplitude route reads RMS against time, the filter route reads a spectral
centroid converted through a FILFRQ ruler — so it is the first genuinely
independent confirmation this calibration has had.

The two outliers are exactly the two still measured by the threshold method
this section retires. That is a prediction, not a conclusion: `ATTAK1` and
`DECAY1` should be re-measured, and if the pattern holds they will move onto
0.098 as well. **They have not been changed on suspicion** — a suspicious
number that was measured beats a plausible number that was inferred.

### Finding B — both sustains are linear in the log domain, each in its own

`SUSTN1` is linear in **decibels** (§26). `SUSTN2` is linear in **FILFRQ
units**, and FILFRQ is logarithmic in hertz, so `SUSTN2` is linear in
**octaves**. Two parameters with the same name and the same 0–99 range obeying
laws in different domains, and in both cases the domain is the perceptual one.

A converter that treats either as a linear fraction of amplitude or of hertz
is wrong across the whole middle of the range while looking entirely correct.

### Finding C — `RELSE2` is 5.7× faster than `RELSE1` at the same value

Same exponent, different prefactor. A program with both releases set to 60
has its filter shut long before its amplitude has finished. Flattening the two
onto one curve — which any "envelope" abstraction invites — loses that.

### The five method failures, in order

1. **The recorder raced its own callback.** Channel 0 was concatenated across
   the blocks visible at one instant and channel 1 across those visible a
   moment later, so the channels came out a period apart and `np.stack`
   refused them. Intermittent by construction; nine sweeps ran over it.
2. **The centroid of near-silence is ~sr/4.** A flat noise floor has its
   centre of mass at 12 kHz here, so when the filter closes far enough to take
   the signal away, measured brightness *climbs*. A closing filter read as an
   opening one, and the first `DECAY2` run reported 11.5 kHz "spans" that were
   entirely this.
3. **A saturated modulation depth measures nothing.** At full depth, `SUSTN2`
   60 already drove the corner past the source's own bandwidth, so 60/75/90/99
   all read ~4480 Hz — the sawtooth's brightness, not the filter's.
4. **A dropped routing line.** `MODSFILT1` — the modulation *source* — was
   omitted, so `MODVFILT1` applied its depth to whatever source the program
   already held. Eleven `SUSTN2` values came back within 1 Hz of each other
   and two further sweeps produced nothing. This is §18's lesson one level in:
   isolation asks *"is this the program I am hearing?"*, and nothing was
   asking *"is this parameter connected to what I am measuring?"*. It is now
   `verify_responds()`.
5. **The centroid is not a linear proxy for the corner.** Its floor is the
   fundamental (261 Hz at note 60 — a harmonic series' centre of mass cannot
   sit below its first harmonic) and its ceiling compresses toward the source.
   `ln(centroid)` against `SUSTN2` accelerated from 0.003 to 0.043 per unit,
   describing the detector rather than the sampler.

### The two techniques that fixed it, and generalise

**A null measurement.** Sweep `FILFRQ` directly with the envelope
disconnected, record the centroid, and then find the `FILFRQ` that produces
the same brightness as each envelope setting. Both sides pass through the
identical nonlinearity, so it cancels *exactly* rather than approximately —
and the answer arrives in FILFRQ units, which already convert to hertz. This
is what made `SUSTN2` measurable at all.

The ruler is not monotonic: it **folds**, falling to a minimum near `FILFRQ`
52 and rising after, because below that the signal is quiet enough for the
noise floor to pull the centroid up. Only the rising branch may be inverted.
Feeding the folded curve to `np.interp`, whose `xp` must be increasing,
returns nonsense *silently* — which is how one run reported `FILFRQ 77.9` for
all eleven values.

**Fit the whole curve, never a threshold crossing.** Runs 1 and 2 measured
`RELSE2` 70 as 0.510 s and 0.330 s, differing only in where timing began.
Neither was wrong; the question was underspecified. Fitting
`ln(x - floor)` against time yields a time constant with no such freedom, and
it is what moved `ATTAK2` from r² 0.9978 to r² 0.9999.

### Withdrawn

`ATTAK2 = 0.00073832 * exp(0.08963 * v)` (§26) is withdrawn. It came from a
threshold crossing on the raw centroid and was wrong by −17 % at value 50 and
+10 % at 85 — small enough to look right, which is why it shipped.

### Cost, recorded honestly

A `pkill -TERM` matched both a sweep and the `timeout` wrapper around it. The
wrapper forwarded a second SIGTERM, which raised *inside* the restore's
`finally` and stopped it partway: two fields restored, fourteen left at sweep
values. The snapshot existed only in the dead process's memory, so the
original values are unrecoverable and the machine's owner was told so rather
than having plausible defaults invented for them.

Both causes are fixed — the handler is one-shot, and `write_snapshot()` puts
the originals on disk, atomically, before anything is written. A snapshot
truncated by the same crash it exists to survive would look authoritative.

One more, from the same wreckage: `verify_isolation` reported *"something else
is sounding on this MIDI channel"* when nothing was. Both recordings were
silence, because the interrupted restore had left `FILFRQ` at 40. The check
compares two recordings and cannot distinguish a collision from a keygroup
that makes no sound — it must be called **after** neutralising, and its
message now names the likelier cause rather than the only one.

---

## §29 — RETRACTION. The envelope model in §28 is wrong, and so was my reason for believing it (2026-08-11)

§28 reported that every envelope stage follows an exponential approach with a
time constant, and that `RELSE1`, `RELSE2` and `ATTAK2` share an exponent of
0.098 to within 0.16 %. **The exponential model is wrong.** Every stage is a
straight ramp in the log domain, and the fits prove it 12 out of 12:

| | exponential r² | linear r² |
|---|---|---|
| `ATTAK2` | 0.9566 | **0.9967** |
| `DECAY2` | 0.9449 | **0.9993** |
| `RELSE2` | 0.9480 | **0.9992** |
| `DECAY1` | 0.945 | **1.000** |

### How the error was found, and why it should have been found sooner

Re-measuring `DECAY1` fitted both models and the linear one returned
**r² = 1.000** over eleven values. The exponential returned 0.941–0.949 —
mediocre at *every single value*.

**Flat mediocrity is the signature of a systematic model error, and it was
sitting in the §28 output the whole time.** `DECAY2` 0.943–0.944. `RELSE2`
0.943–0.948. `ATTAK2` 0.945–0.957. I logged those numbers across three
separate runs, noticed they were "remarkably consistent", and read that as
reassurance. Consistency was the evidence *against* the model: noise varies,
bias does not.

### The reason I trusted it was itself the mistake

§28 called the 0.16 % agreement across two detectors "the first genuinely
independent confirmation this calibration has had", because the amplitude
route and the filter route share no code.

That reasoning is wrong, and the error is worth more than the finding was.
Two detectors sharing no code rule out **detector** error. They say nothing
about a **model** error, because the model was mine and I applied the same one
to both. Three curves agreeing tells you nothing when the thing they agree on
is the assumption you brought.

The exponents do still cluster (0.0972, 0.0980, 0.1012 under the correct
model) so the *shape* of §28's claim survives — but it survived by luck, and
the argument offered for it did not.

### What the stages actually are

Holding a value fixed and varying the distance travelled separates a rate from
a duration, because they predict opposite things. Both **decays** are rates:

| | span varied | rate CV | time CV | verdict |
|---|---|---|---|---|
| `DECAY1` (amplitude, dB) | 47 % | **0.27 %** | 18.8 % | constant RATE |
| `DECAY2` (filter, FILFRQ units) | 72 % | **1.9 %** | 28.0 % | constant RATE |

**A decay value sets a slew rate, so its duration depends on how far it has to
travel.** `DECAY1` 70 is not "339 ms" — it is ~24.7 dB/s, which happens to
take 339 ms across the span that was set during calibration. A converter that
writes a decay from a target time without knowing the sustain level will be
wrong by whatever ratio the spans differ by.

The **attack** fits neither model: across a 99 % change in span the rate moved
29 % and the time 11 %, sub-linearly. It is recorded as unresolved rather than
forced into whichever fits marginally better.

### Two instrument faults found by reading rows instead of summaries

**A verdict rule with no inconclusive branch.** The first rate-or-time script
picked whichever spread was smaller and announced it, so 72.2 % against 71.9 %
was reported as a finding. That is the same fault as an isolation check that
can only say "collision" — a rule that cannot abstain will label noise. It now
requires a 3× margin.

**A circular measurement.** The filter test swept `MODVFILT1`, but the ruler
tops out at `FILFRQ` 98, so every depth above 15 clipped to the same 41 units.
Reconstructing the true span from the `SUSTN2` law then made both sides
proportional to depth, and "constant time, CV 0.2 %" came out true by
construction. The suspiciously perfect number was the tell.

### Consequences in the code

`ATTAK1`, `DECAY1`, `RELSE1`, `ATTAK2`, `DECAY2`, `RELSE2` are marked
`provisional` in `s3k/scales.py` and render with a leading `!`. The seconds
they report are the best available and were measured at one span; their
meaning is not settled. They are **not** withdrawn — a measured number with a
stated caveat is more useful than silence — but nothing should convert a time
into one of these values without knowing the distance the stage will travel.

Still to do: rate laws in dB/s for the amplitude envelope, which no sweep has
yet measured (every one of them fitted a time constant), and the attack shape.

---

## §30 — The decay rates, measured properly (2026-08-11)

§29 established that a decay value sets a slew rate but could name that rate at
exactly one point, because every sweep to that date had fitted a time constant.
These are the laws.

```
DECAY1   rate = 23525.6 * exp(-0.09776 v) dB/s              45..85   r2 0.99998
RELSE1   rate = 22055.3 * exp(-0.09683 v) dB/s              55..70   r2 0.99956
DECAY2   rate = 25200   * exp(-0.09796 v) FILFRQ-units/s    50..80   r2 0.99995
RELSE2   rate = 61190   * exp(-0.10123 v) FILFRQ-units/s    58..76   r2 0.99977
```

`time = span / rate`. FILFRQ units are octaves at 9.4 units to the octave.

### The fit quality is the point, not the constants

Every individual `DECAY1` curve fits a straight line in dB at **r² 0.999 to
1.000**, across nine values and up to 616 analysis points each. The model is
confirmed value by value rather than on average.

Set that beside the retracted model, which sat at 0.941–0.949 on the same
data at every value. **That contrast is the whole methodological content of
§28–§30:** a mediocre fit that never varies is a biased model, and only a
comparison against an alternative reveals it. Nothing about the exponential
fits looked wrong in isolation; they looked *stable*.

### The clustering claim, restated with a defensible argument

`DECAY1` −0.09776, `RELSE1` −0.09683, `DECAY2` −0.09796 — three stages across
both envelopes within 0.0012 of each other. `RELSE2` sits at −0.10123 on the
fewest points and is **excluded from the claim** rather than averaged in.

§28 made this claim and defended it by saying the detectors shared no code.
That defence was wrong: independent detectors rule out detector error, not
model error, and the model was the same on both sides. What licenses the claim
now is different and narrower — the per-curve model is verified at r² ≈ 1.000,
so the quantity being compared is the right one. The agreement is worth
reporting; the *reason it is worth reporting* had to be replaced.

### Two exclusions, both stated rather than smoothed

`RELSE1` at 45 and 50 crosses its whole range in 30–42 analysis windows, and
the rate comes out quantised — individual r² 0.986 and 0.998 against 1.000 for
the rest. Including them drops the law from r² 0.9996 to 0.9916. They are
measured, excluded, and the exclusion is in `bounds` where it travels with the
number.

Earlier runs also failed at spans below ~21 dB, where the sample's own slow
decay competes with the envelope: the fitted rate collapsed to 4.17 dB/s
against the 24.7 its neighbours agreed on. Spans are held above 12 dB now, and
that floor is in the probe rather than in someone's memory.

### What remains open

**The attacks.** `ATTAK1` and `ATTAK2` fit neither a constant rate nor a
constant duration — across a 99 % change in span the rate moved 29 % and the
time 11 %, sub-linearly. They stay `provisional`, still reported in seconds
that hold for one span only, and marked `!` in the editor. Forcing them into
whichever model fits marginally better is precisely the error §29 retracted.

**`RELSE1`'s span is not a property of the value.** A release begins wherever
the note had got to when the key was let go, so there is no single "release
time" even with the rate known. Any converter has to carry the level with it.

---

## §31 — Both attacks measured. Only one of them is a time (2026-08-11)

§30 left the attacks open. They are now measured, and they are **not the same
kind of thing as each other**.

```
ATTAK1   rise time = 0.000150326 * exp(0.11175 v) s   55..90   r2 0.99991
ATTAK2   rise time = 0.00115864  * exp(0.09850 v) s   55..85   r2 0.99967
                                                     (at MODVFILT1 18 only)
```

### Finding A — the amplitude attack is a linear ramp in amplitude

Four models fitted per curve, eight values:

| model | mean r² |
|---|---|
| **linear in amplitude** | **0.9982** |
| square-law | 0.9578 |
| linear in dB | 0.9343 |
| exponential approach | 0.9302 |

Consistent to within 0.001 value by value. So the S3000 amplitude envelope is
the classic analog pairing — **linear attack, exponential decay**.

This also explains the span-dependence that made the attack look like neither
a rate nor a duration in §29: a linear-in-amplitude ramp read on a dB axis is
a *curve*, and a slope taken from the middle of a curve depends on how far the
curve extends. That was the detector again, for the fourth time in this
sequence.

`ATTAK1` is a genuine **duration**, and the reason is structural: the
amplitude attack always travels the same distance, zero to peak. A decay's
distance depends on the sustain level, which is exactly why decays are rates.

### Finding B — the filter attack is a linear ramp in OCTAVES, not hertz

The obvious guess from Finding A was that attacks live in the linear domain,
so the filter attack should ramp linearly in hertz. **It does not:** r² 0.9980
for octaves against 0.9124 for hertz, across six values.

So only the amplitude attack is in the linear domain — and it *has* to be. A
ramp linear in decibels starting from silence starts at −∞ dB and never
leaves. The filter attack starts at the base cutoff rather than at zero hertz
and has no such constraint, so it stays log-domain like everything else.

The asymmetry is forced by arithmetic rather than chosen by a designer, which
is a more satisfying answer than "the two envelopes differ".

### Finding C — attack and decay are different laws, and the "one law" idea is dead

`ATTAK1` exponent 0.11175 against the decays' ~0.0977. Not the same. §28
claimed a single time-constant law across all six stages; §29 retracted the
model; this retires the last surviving form of the claim. There is a test
pinning it, because the idea has now come back twice under different clothes.

### `ATTAK2` stays provisional, for a reason that is now precise

The **shape** is settled. The **depth-scaling** is not. Sweeping `MODVFILT1`
with the value fixed gave neither a constant rate (78 % spread) nor a constant
duration (27 %); the rate came out as a fixed part plus a part proportional to
span. That is a two-parameter fit on four points, and promoting it to a law is
the exact move §29 exists to retract.

The measured seconds hold at `MODVFILT1` 18 and nowhere else — the same value
read 0.38 s at depth 25 against 1.14 s at depth 18. **Three times apart for
one parameter value**, which is why a settled shape does not settle a law, and
why the test suite anchors `ATTAK2` with its depth written next to it.

---

## §32 — Replication, and a retraction of my own justification (2026-08-11)

Three attempts at `ATTAK2`'s depth-scaling came back inconclusive. The cause
was not too few conditions. **It was that every measurement in this entire
calibration had been taken exactly once**, so there was never an error bar and
no way to tell a 17 % spread between conditions from 17 % of noise within one.

Three captures per condition, and the question answers immediately:

```
depth   rises (s)                mean     span
   11   2.215  2.210  2.230     2.218    27.0u
   14   2.280  2.245  2.240     2.255    34.5u
   17   2.305  2.290  2.295     2.297    40.9u

within-condition scatter (pooled sd)  0.0146 s   -- 0.65 %
between-condition scatter             0.0392 s
```

**Span varied 41 %; the rise time varied 3.5 %.** A constant-rate model needs
the rise proportional to span and is rejected at F = 904 — overwhelming, a
between/within ratio near 30. `ATTAK2` is a **duration**.

**Correction, made within the hour by the tool this section motivated.**
The small residual span-dependence (`rise ≈ 2.06 + 0.0057·span`) was reported
above as established. It is not. The verdict rule in the probe used F > 4,
i.e. a between/within ratio above 2; the reusable `beyond_noise()` helper
written straight afterwards uses a 3× bar, and the same data returns
**undecidable at 2.68**. Two thresholds, one of them chosen while looking at
the answer.

So: constant-rate is dead beyond argument, `ATTAK2` is a duration, and whether
a small residual survives is **not settled**. A test pins the marginal number
so the over-claim cannot quietly return. The first thing the replication tool
did was catch an over-claim in the finding that prompted it.

The repeatability is the headline: **0.65 %**. Every ambiguous result in
§29–§31 was fought out against noise of unknown size, and it need not have
been. Replication should have been in the first sweep this morning; it is
cheap, and it converts "these numbers look close" into a decision.

### Retraction: the threefold depth effect was mine, not the machine's

§31 and the handoff to mpc2emu both stated that `ATTAK2` 70 reads 0.38 s at
depth 25 and 1.14 s at depth 18 — "three times apart for one parameter value",
offered as the reason it stayed provisional.

**That comparison was invalid.** The 0.38 s was an *exponential time constant*
from the model retracted in §29; the 1.14 s was a *10–90 % rise time*. Two
different quantities with the same unit. There was no threefold depth effect.
The real one is 3.5 %.

This is the third time in this sequence that a "finding" turned out to be an
artefact of comparing quantities that were not the same quantity — after the
`RELSE1`/`RELSE2` ratio that divided dB/s by octaves/s, and the exponent
clustering that compared fits of a wrong model.

### But a larger problem replaces it, and it is also mine

Two implementations of "10–90 % rise" *in this project* disagree by **33 %**
on the same condition — 3.060 s (§31's sweep, depth 18) against 2.297 s
(this one, depth 17). Same nominal quantity, same machine, same rig.

That is an order of magnitude larger than the depth effect I spent three runs
chasing. It comes from small choices: whether the baseline is the first sample
or the 3rd percentile, where the curve is truncated, how the peak is
estimated. None of those were recorded as part of the measurement.

So `ATTAK2` stays `provisional`, with the reason replaced: the **exponent** is
trustworthy and the **prefactor** is not. A converter should use the shape and
the depth-independence, and treat the absolute seconds as ±33 %.

**The general lesson, which applies to every law in `s3k/scales.py`:** a
measurement is a number *plus its definition*, and this project has been
recording only the first. The `bounds` field exists because a range that
travels without its reason gets misused; the same argument applies to a
prefactor that travels without the operational definition that produced it.

---

## §33 — SUPERSEDED by §53. `FILQ` is resonance, and it peaks below the corner (2026-08-11)

> **The linear law below is wrong; §53 measures the field properly.** The
> peak-below-the-corner finding here REPLICATES and stands. Left in place
> because the reason the linear reading failed is the useful part.

```
resonant gain = 0.5764 * FILQ dB     0..15    r2 0.9366   LOWER BOUND
```

Measured: +1.8, +3.5, +4.5, +5.7, +10.0 dB at `FILQ` 3, 6, 9, 12, 15,
monotonic, with `FILFRQ` parked at 77. Forced through the origin because
`FILQ` 0 is zero gain *by definition* — it is the reference every other step
was measured against, so the free fit's −0.23 dB intercept is bias, the same
correction as `KGTUNO` in §27.

### The peak is not at the corner

The peak sits at **~1570 Hz** while the `FILFRQ` law (§20) puts the corner at
**2093 Hz** for the same setting — **0.41 octaves apart.** So `FILFRQ` and
`FILQ` do not share a frequency reference, and a converter that places
resonance at the `FILFRQ` corner will put it a third of an octave too high.

### The first attempt failed, and the failure was informative

It parked the corner on harmonic 8 and watched that harmonic alone. The result
was a 3.3 dB swing that *oscillated* — up at step 6, down at 12, up at 15 —
which is not what resonance does.

The cause: **harmonic 8 was above the peak, sitting on the transition.** At
`FILQ` 15 harmonic 6 reads −7.0 dB and harmonic 7 reads −24.8 dB — a 17.8 dB
cliff between adjacent harmonics. Measuring one point on a moving cliff reads
the edge sliding past, not the thing being swept.

The fix was to stop presupposing where the corner is: measure *every* harmonic
1–20 and let the peak appear wherever it appears. A resonance shows as a bump
that grows; a corner shift shows as the whole shelf moving. They look nothing
alike, and neither has to be assumed.

**This is the same lesson as the null measurement in §28** — the detector must
not encode the answer being sought. Measuring one harmonic encoded a belief
about where the corner was.

### Why the number is a lower bound

The probe reads the filter through a sawtooth's harmonic comb, teeth 261.6 Hz
apart. Where the true peak falls between two teeth, its height is understated
and its position is pinned only to about ±8 % near 1570 Hz. The top step also
breaks the straight line (+10.0 dB measured against +8.6 predicted), so the
linear law is a summary rather than something to trust at the ends. `FILQ`
ships `provisional`.

To do better, sweep the note so the comb slides across a fixed corner, and
take the envelope of the result — that trades run time for resolution and
needs no new capability.

### And the error bars in the first attempt were fictitious

It reported sd = 0.000 dB on fifteen of sixteen conditions. Not precision: a
fixed digital sample, unchanged settings between repeats and a recorder that
aligns to its own schedule leave a repeat nothing to resample. `beyond_noise()`
then called a ratio of 3645 "varies", which was arithmetic rather than
evidence.

**Replication only measures the noise sources the repeat actually re-runs, and
a too-small error bar is worse than none** — it makes every difference look
significant. Recorded in `replicate()`'s docstring, one section after the same
function was added to fix the opposite problem.

---

## §34 — Where a release starts, and the first real cross-validation (2026-08-11)

mpc2emu asked a precise question: they had wired `RELSE1` with
`span = 0.60832 * SUSTN1` dB, the release travelling from the sustain level
down to the floor. If instead the release always sweeps the full range, every
value they write is wrong by that ratio — invisible at sustain 99, worst at
low sustain.

It needs no clever detector. **The release's starting level is the level at
note-off**, and that is already in the capture. Sweeping `SUSTN1` with
`RELSE1` held at 60:

```
SUSTN1  level@off  slope dB/s  fall s   measured span   their span
    30     -70.72      67.79    0.255      20.76          18.25
    45     -61.26      67.73    0.390      29.25          27.37
    60     -52.20      67.42    0.520      38.78          36.50
    75     -43.20      66.73    0.640      48.29          45.62
    90     -34.21      66.90    0.765      56.56          54.75
```

**Their model is right, on all three counts:**

1. The release starts at the sustain level. Level at note-off against what
   the `SUSTN1` dB law predicts: −0.01, +0.32, +0.26, +0.13 dB. Within a third
   of a decibel across a 36 dB range.
2. The rate is constant: **CV 0.6 %** across a 35 % change in span. And it
   comes out at 67.3 dB/s where the `RELSE1` law predicts 66.1 — **1.8 %**.
3. The fall time scales with their span: `fall / (0.60832·SUSTN1)` holds to
   **CV 0.9 %**, while the fall time alone varies **35 %**.

### Why this run matters more than its answer

**It is the first genuine cross-validation in the whole calibration.** The
`SUSTN1` dB law (§26) and the `RELSE1` rate law (§30) were each fitted from
separate sweeps, and *neither was fitted to this data*. Both predict it, to
0.33 dB and 1.8 % respectively.

Every earlier claim of confirmation in these notes was weaker than it sounded.
§28 called an agreement between two detectors "independent confirmation" and
was wrong, because both ran through the same model. §32's error bars were
fictitious because the repeats resampled nothing. This is the thing those were
mistaken for: a prediction made in advance, from constants fitted elsewhere,
tested against data that could have refuted it.

The generalisable form: **a law earns trust by predicting a measurement it was
not fitted to.** Agreement among things fitted together is arithmetic.

### One residual, stated

Measured spans run ~2.3 dB larger than `0.60832·SUSTN1` at every sustain. That
is the measurement's floor sitting slightly below the level `SUSTN1` 0
corresponds to — a property of where the noise floor is, not of the machine.
It is a constant offset and does not affect the ratio.

---

## §35 — `LFODEP` and `LFODEL`, and a floor I read as data (2026-08-11)

> **The `LFODEL` half is superseded by §55**, which finds a different law
> and shows there is no fade-in to conflate. `LFODEP` stands, and §55
> cross-checks it to 2.4% on independent data.

```
LFODEP   vibrato = 19.4932 * LFODEP cents peak-to-peak   0..99   r2 0.99949
LFODEL   delay   ~ 0.018694 * exp(0.04302 * v) s         40..99  r2 0.7633
                                                          PROVISIONAL
```

### `LFODEP` is linear in cents, and joins a pattern

Linear beats exponential **0.9997 to 0.8663** — not close. Full depth is
**±9.6 semitones**, wider than the panel suggests. Forced through the origin:
`LFODEP` 0 is no vibrato by definition, so the free fit's −17.4 cent intercept
is bias — the third time that correction has been needed, after `KGTUNO` (§27)
and `FILQ` (§33).

Cents rather than hertz because a ratio is independent of the note played and
of the rig's tuning; absolute frequencies would not be.

**And linear in cents is linear in the log domain**, joining `SUSTN1` (linear
in dB) and `SUSTN2` (linear in octaves). Three independent level-like fields,
each linear in its own perceptual domain. That is now enough to *expect* it of
the unmeasured level fields — which changes what would count as a surprise,
without excusing anyone from measuring.

### `LFODEL` took three attempts, and each failure was a different way of measuring my own instrument

**Attempt one** timed the first pitch deviation above a threshold, and returned
**−0.150 s**. A negative delay is impossible; the detector was firing on the
first analysis window, before a stable pitch existed, and timing the attack
transient.

**Attempt two** replaced that with a rolling standard deviation, which removed
the impossible values and introduced a quieter falsehood: **eleven consecutive
values reported 0.090 s, and 0.090 s was the detector's warm-up time.** Those
rows said "shorter than 0.09 s" and I read them as a measurement. Only 95 and
99 cleared the floor.

**Attempt three** dropped the rolling window — with the vibrato this deep a
plain threshold suffices, provided it only looks at windows containing real
post-note-on signal, which is exactly what attempt one got wrong. Floor fell
from 0.09 s to 0.02 s and the delays came out monotonic.

Two rows still failed, and **they announce themselves**: `LFODEL` 85 and 92
reported ~4.8 and ~4.3 octaves of swing where every other row reported 2.4.
Pitch-tracker octave errors, which cross any onset threshold instantly.
Excluded as detector failures, not data.

### Why `LFODEL` ships provisional anyway

Neither shape fits: r² 0.76 exponential, 0.60 linear, and **the local exponent
varies fourfold** across the range — 0.026/unit between 40 and 80 against
0.114 between 90 and 99.

The likely cause is that the detector times *"until the vibrato exceeds 100
cents"*, which is the delay **plus** any fade-in of the depth after it. Two
quantities measured as their sum. Separating them needs an onset detector that
finds when modulation *starts* rather than when it becomes large — for
instance fitting the vibrato envelope backwards to its zero crossing.

The order of magnitude is sound: ~0.15 s at 40, 0.43 s at 80, 2.0 s at 99.

### The recurring shape, now three sections running

§33's first `FILQ` attempt read a moving filter edge as a resonance curve.
§32's error bars read a degenerate repeat as precision. This section read a
warm-up time as a delay. **In each case the instrument produced a number, and
the number was about the instrument.**

The check that would have caught all three is the same one: *before believing
a measurement, ask what value it would produce if the machine were doing
nothing at all.* A flat 0.090 s, a zero standard deviation and an oscillating
1.6 dB are all exactly what an inert machine would have given.

---

## §36 — `V_LOUD`, and a shape test that could only pick the least wrong (2026-08-11)

```
attenuation = 0.009474 * V_LOUD * (knee - velocity) dB,  zero above the knee
knee ~ velocity 66;  V_LOUD -50..50;  each unclipped region r2 1.00000
```

At `V_LOUD` 50 velocity 1 is **30.8 dB** below full; at 25, **15.8 dB**. The
slope is *exactly* proportional to `V_LOUD` — 0.4741 dB per velocity unit at
50 against 0.2366 at 25, a factor of 2.00 for a factor of 2.

Negative depths mirror it: full level below the knee, attenuating above, same
slope magnitude.

### Two findings a converter needs

**At `V_LOUD` 0, velocity does nothing** — flat to 0.00 dB across the entire
velocity range. There is no built-in sensitivity underneath, so this field is
the whole of it and writing it is sufficient. That was a free control: the
question "what would this read if the machine ignored velocity?" has an answer
that is also a row in the table.

**It is piecewise linear in VELOCITY, and clipped.** Above velocity ~66 the
level is flat; below, attenuation is linear. So `V_LOUD` is the one field
measured on this machine that is **not** linear in its perceptual domain —
`SUSTN1` is linear in dB, `SUSTN2` in octaves, `LFODEP` in cents, and this one
is linear in raw velocity. The exception is recorded in the table and pinned
by a test, because a pattern strong enough to expect is strong enough to be
assumed away.

### The shape test was worthless, and the reason generalises

The probe fitted four candidate shapes and reported:

```
dB linear in velocity        r2 0.8138
dB linear in log(velocity)   r2 0.3407
dB linear in velocity^2      r2 0.9622   <- "winner"
AMPLITUDE linear in velocity r2 0.8538
```

**Every one of those numbers is meaningless.** They were fitted across all
nine velocities including the four clipped ones, so no model could fit, and
the most curved candidate won by bending toward the flat top. On the unclipped
region alone, plain linear-in-velocity gives **r² 0.99997**.

The deeper fault: **I offered four shapes and the true one was not among
them.** A comparison between candidates can only ever return the least wrong
candidate — it has no way to say "none of these". Every model-selection step
in this calibration has that hole, and the protection is not a better
shortlist but the residuals: a fit at r² 0.96 whose errors are all at one end
is not a fit, it is a shape mismatch wearing a good score. §29 found the same
thing from the other side, where a *consistently* mediocre r² across every
condition meant systematic bias.

So: **look at where the residuals sit, not only at how large they are.**

---

## §37 — RETRACTION. `V_LOUD` boosts, it does not attenuate — and the knee was mine (2026-08-11)

§36 reported `V_LOUD` as an attenuation below a knee at velocity 66, with the
knee a property of the machine. **Both halves are wrong.** Jan asked a one-line
question — *"can't you just turn the volume of the program down?"* — and it
cost two findings.

```
gain_dB = 0.009474 * V_LOUD * (velocity - 64)
```

A **gain about a pivot at velocity 64**, not an attenuation below a knee.
Above 64 the program is *louder* than `PRLOUD` alone would suggest; below,
quieter. Predicted −29.84 dB at velocity 1 against −29.88 measured, and
+17.05 at velocity 100 against +16.96.

### What the flat top actually was

An **output ceiling**, and it moves with the program volume:

```
PRLOUD 99  full level from velocity  66
PRLOUD 80                            90
PRLOUD 60                           >100
PRLOUD 40                           >100
```

A louder program reaches the limit at a lower velocity. Every sweep in this
calibration had `PRLOUD` pinned at 99 — the maximum, against a machine default
of 80 — which is precisely the condition where a ceiling impersonates a law.
Pinning a variable to its extreme is not neutralising it.

### The route there went through a wrong hypothesis, and the wrongness is the useful part

`PRLOUD` 99 and 80 produced identical levels, so I proposed that **`PRLOUD`
saturates above 80** and found supporting evidence immediately: its r² of
0.9933 was the worst in the table, exactly what a clipped top end does to a
straight-line fit.

`PRLOUD` does not saturate. Swept at 28 values with velocity neutralised, it
is monotonic to 99 with no plateau, r² 0.99965.

**That was motivated reasoning.** I had a hypothesis and went looking for
support rather than for refutation, and support was available — the r² really
is the worst in the table. A bad fit is consistent with many faults, so
finding one that explains it is not evidence for that one.

### Then two of my own runs contradicted each other by 17 dB

The V_LOUD-50 readings were *louder* than the V_LOUD-0 readings at the same
`PRLOUD` — impossible under the attenuation model, since attenuation cannot
add gain. Rather than reason about which run was right, both configurations
were re-measured back to back in one session **with every parameter read back
after writing**, because a dropped write produces exactly that signature.

Every write took. The effect reproduced exactly. The impossibility was in the
model, not the data: `V_LOUD` really does boost, so a "louder than the
un-attenuated level" reading is not impossible at all — it was only impossible
given an assumption I had stopped noticing I was making.

### `PRLOUD`, re-measured

`0.61872 dB/unit, r² 0.99965`, superseding `0.642719 at r² 0.9933`. The
residuals still tilt (+0.081 dB across the first half, −0.081 across the
second), so a slight curvature remains and the straight line is an
approximation rather than the shape.

### What generalises

**"Neutralise the variable" is not the same as "set it to maximum."** Every
extreme is a place where something else runs out. The `FILFRQ` 99 reference,
the harmonic comb's resolution, the rolling window's warm-up, and now
`PRLOUD` 99 — four occasions where the *setup* was the finding.

**When a measurement looks impossible, suspect the model before the data.**
The 17 dB contradiction was read as an instrument fault for a full cycle
because "attenuation cannot add gain" felt like arithmetic rather than what it
was: a consequence of an unexamined premise.

---

## §38 — The headroom audit: what the ceiling did and did not damage (2026-08-11)

§37 found that every sweep here pinned `PRLOUD` to 99 while `V_LOUD` sat at its
factory default of **20** — which no envelope or filter sweep ever set. At the
rig's default velocity of 100 that is a **+6.8 dB boost** into an output
ceiling. This audits what it cost.

**Confirmed clipped.** `V_LOUD` 20 at `PRLOUD` 99 delivered **+0.94 dB** where
the law demands +6.82. The machine's output cannot exceed about −28.74 dB in
this chain, and `PRLOUD` 99 alone already sits at −29.7, so there is under a
decibel of headroom at full volume.

### The verdict, law by law

| law | published | clean re-measurement | error |
|---|---|---|---|
| `SUSTN1` | 0.60832 dB/unit | **0.60676** | 0.26 % |
| `DECAY1` rate at 70 | 25.09 dB/s | **25.25** | 0.6 % |

**Both survive.** The ceiling damaged the *velocity* work — where `V_LOUD` was
deliberately driven to 50 and the boost is large — and left the level laws
essentially intact, because only their topmost point ever approached the
limit.

### The part worth keeping: my control was worse than the thing it checked

The first audit compared the original conditions against a "clean" run at
`PRLOUD` 70, which returned 0.57888 — **wrong by 4.6 %**, against 0.26 % for
the supposedly-compromised original. At `PRLOUD` 70 the bottom of the sweep
lands at −95.7 dB with the noise floor at −93.

**A ceiling and a noise floor both flatten a curve, from opposite ends.** So
two such runs do not bracket the truth: they both understate the slope, and
the answer lies *outside* the interval they span. Reading them as "an upper
and a lower estimate" would have put the answer in exactly the wrong place.

The tell was present and I nearly missed it: the clean run had the **worse**
r² (0.99554 against 0.99995) and six times the residual tilt. The compressed
run fitted better, because a flattened line is still a line.

### The check that catches this class, added to the probe

Measure both limits in the same session, then **report every point's margin to
each of them**, so a compressed reading identifies itself instead of being
deduced afterwards:

```
SUSTN1   level dB   to ceiling   to floor
    40     -70.90       42.2       22.1
    99     -35.11        6.4       57.9
```

Thirteen points, all clear, slope 0.60676 at r² 0.99993 with residual halves
of +0.005 and −0.004 dB — the flattest in the table.

The question every level sweep here was asking was *"is the fit good?"*, and
`SUSTN1`'s clipped fit scored 0.99995. The question that catches it is **"was
each reading free to be what it wanted to be?"**

---

## §39 — RETRACTED by §52. The pan LFO does nothing on this machine, and `PANPOS` cross-validates (2026-08-11)

> **The pan-LFO half of this section is wrong and is superseded by §52.**
> All five fields work; they were tested against the pan destination, which
> is the one broken part. The `PANPOS` cross-validation below still stands.
> Left in place so the reasoning that failed stays legible.

### Auto-pan is inert

`PANDEP`, `PANRAT`, `PANDEL`, `LFO2WAVE` and `LFO2TRIG` produce **no
measurable change in the stereo balance** on this S3000XL, across roughly
thirty settings:

```
PANDEP  0..99     swing 0.71..0.84 dB   (r2 0.001 against a straight line)
PANRAT  5..99     identical at every value
PANDEL  0/50/99   swing 0.67..0.84 dB
LFO2WAVE 0..5     swing 0.72..0.79 dB
LFO2TRIG 0..3     swing 0.72..0.83 dB
```

Every one of those sits inside the **0.76 dB the depth-0 control shows**, so
the residual movement is the rig, not the machine.

This is not a routing or detector problem: **`PANPOS` drives the very same
measured balance across 118 dB** in the very same session, −59.30 dB at −50 to
+58.96 dB at +50. The path is alive; the oscillator is not reaching it.

`LFO2WAVE` is documented as *"0 Triangle, 1 Sawtooth, 2 Square"*, so waveform 0
is a real waveform rather than an off setting, and all three are equally
silent. Writes were read back at every step.

What this does **not** establish: whether the fields are unimplemented on the
S3000XL, need the IB304F board, or require an enable this survey missed. It
establishes only that nothing reachable from these five fields moves the
stereo output. A converter should treat auto-pan as unavailable rather than
write values that do nothing.

### `PANPOS` cross-validated

The §22 constant-power law was fitted from a separate sweep and predicts this
run, which it never saw:

| `PANPOS` | measured | law | diff |
|---|---|---|---|
| −25 | −10.20 dB | −9.82 | −0.38 |
| 0 | −0.62 dB | −0.54 | −0.08 |
| +25 | +9.01 dB | +8.74 | +0.27 |

Within 0.4 dB across the range, and ±50 lands hard left and right as recorded
in `endpoints`. That is the §34 standard again: a law earns trust by
predicting a measurement it was not fitted to.

### The mistake worth recording is procedural

The first pan sweep returned **eleven rows of a clean, stable 0.26 Hz at
prominence 20–30×** and I would have written down "`PANRAT` has no effect on
rate" as a fact about the machine. Only the depth-0 control saved it: the same
0.26 Hz was there with the LFO switched off, because over a 6 s window it is
1.5 cycles — the lowest bin above the analysis cutoff, i.e. drift.

**`verify_responds()` exists precisely for this** (§28), would have refused the
sweep in eight seconds, and I did not call it. Building the guard and then not
reaching for it is a worse failure than not having it, because the reasoning
that motivated it had already been done.

The control that did save it costs one capture: **measure what the rig reports
when the machine is doing nothing.** That is now three separate occasions —
§32's degenerate error bars, §35's rolling-window floor, and this — where the
answer was visible only by comparison with an inert baseline.

---

## §40 — Velocity routing at the zone level, and the whole attack family is unsettled (2026-08-11)

### `V_ATT1` works, and velocity 64 is the machine's centre

```
V_ATT1   vel 1     vel 64    vel 127
   -50    2.955     0.555     0.010
   -25    2.955     0.545     0.025
     0    0.545     0.535     0.550
   +25    0.025     0.540     2.970
   +50    0.010     0.545     2.970
```

At velocity 64 the rise is 0.535–0.555 s for **every** depth. **`V_LOUD`
pivots on 64 too** (§37), so two independent fields agree and this is the
machine's velocity centre rather than a coincidence in one measurement.

Bipolar and symmetric in sign: positive makes hard notes slower, negative
faster. **No law is recorded**, because the effect is not a simple exponential
in `(velocity − 64)`: at depth 50 the symmetric form predicts 0.100 s at
velocity 1 and the machine gives 0.010 s. The low-velocity end falls far
faster than the high end rises, and forcing a shape onto that is the error
§29 exists to prevent.

### `VLOUD1` is not connected

`verify_responds()` refused the sweep: moving it 0 → 50 changed the level by
**0.0004 dB**. The per-zone velocity-to-level field does nothing while the
program-wide `V_LOUD` works fully.

That is the second inert modulation path today after auto-pan (§39), and both
are "extra" routing beyond the basics. The guard cost eight seconds and saved a
full sweep — the same guard that was not called on the pan run, which cost an
hour and produced eleven rows describing drift.

### The attack family: two runs, r² 0.99991 each, 20 % apart

`ATTAK1` re-measured under verified headroom (9.2 dB below the ceiling at
every point):

```
clean      0.000156283 * exp(0.10885 v)   r2 0.99991
published  0.000150326 * exp(0.11175 v)   r2 0.99991
           -12.7% at 60,  -16.4% at 75,  -20.0% at 90
```

The gap **grows with value**, so the exponents differ rather than a scale
factor.

**And the obvious explanation is wrong.** The published run was clipped, but a
clipped peak makes a rise measure *shorter* — 90 % of a flattened peak arrives
early — and the clipped run is the *longer* one. So the ceiling does not
account for the direction, and the cause remains unidentified. The clean run is
adopted because its conditions are verifiable, not because the discrepancy is
understood.

`ATTAK1` is therefore marked **provisional**, joining `ATTAK2` (whose two
rise-time definitions differ by 33 %). Three fits of `ATTAK1` now exist,
spanning 20 %, each fitting its own data at r² ≥ 0.9961.

**That pattern is the finding.** A law that fits its own data almost perfectly
and disagrees with a repeat is not reporting noise — it is reporting that the
quantity is underdetermined. Every attack measurement here has been precise and
none has been reproducible, which is what an unsettled operational definition
looks like from the inside.

The decays do not share this: `DECAY1`'s rate reproduced to 0.6 % across
independent runs (§38). The difference is that a rate has an unambiguous
definition — dB per second — while "rise time" hides choices about thresholds,
baselines and where the curve is considered to start.

---

## §41 — The attacks were never underdetermined. §40's diagnosis was wrong (2026-08-11)

§40 concluded that "attack time" was an underdetermined quantity, on the
evidence that three fits of `ATTAK1` spanned 20 % while each scored r² ≥ 0.9961.
The remedy proposed was to define the attack as a *rate*, as the decays are.

**Both the diagnosis and the remedy were wrong.** Scoring one set of captures
under five definitions at once settled it:

```
  val       10-90        5-95       20-80  ramp 20-80  ramp 30-70   thr spread  ramp spread
   55       0.081       0.083       0.083       0.745       1.363        2.5%       58.7%
   75       0.688       0.672       0.700       6.946         nan        4.0%         nan
   90       3.506       3.517       3.517      19.605      37.636        0.3%       63.0%

  mean spread:  threshold 2.7%   ramp 36.3%
```

The threshold family agrees to **2.7 %**, with exponents inside
0.10742..0.10844 and r² ≥ 0.9996. It was never the problem. The *new* method
was, returning 66 s for an 0.08 s attack.

### The new primitive was broken, and my tests could not have caught it

`ramp_rate` selected **every** sample inside the fit band. With a short ramp
followed by eight seconds of sustain, any sustain sample wobbling back into
the band joined the regression, so the fit spanned the whole capture, the
gradient collapsed and the duration exploded. It now takes the contiguous
first crossing.

**The synthetic fixtures could not have found this.** They held exactly at
peak for ever, so nothing ever dipped back into the band. A noise-free
fixture cannot exercise the one failure that matters on a real capture, and
six passing tests said the function was sound. The regression tests now use a
ramp under a *wobbly* plateau.

### What the 20 % actually was

Two things, neither of them an underdetermined quantity:

1. **A scale convention.** `attak1.py` reported the raw 10-90 % time, 0.550 s
   at value 75; `attackdef.py` scaled it to the full travel, 0.688 s.
   `0.550 / 0.8 = 0.688` exactly. The same measurement twice.
2. **One clipped run.** The published exponent of 0.11175 came from a capture
   at `PRLOUD` 99 with `V_LOUD` at its factory 20; the clean runs agree on
   ~0.1084. That difference is larger than any definition produces.

### The law, with its convention stated

```
ATTAK1   0.000201173 * exp(0.10844 * v) s     55..90    r2 0.99988
```

**Defined as the time to cross the full travel**, from the 10-90 % time
divided by 0.8. A raw 10-90 % time is 0.8× these values. `ATTAK1`'s
provisional mark is removed: naming the convention retires the ambiguity, and
nothing about the shape was ever in doubt.

### What generalises

A quantity with several reasonable definitions is not thereby
underdetermined — it needs **one definition, written down where the number
travels**. That is the same argument as `Scale.bounds`, which exists because a
fitted range travelled without its reason. A constant needs its procedure for
exactly the same reason a range needs its cause.

And the sharper lesson: **I proposed a fix, implemented it, tested it green,
and it was worse than what it replaced.** What caught it was scoring the old
and new methods against the same data in the same run rather than swapping one
for the other and moving on. A replacement that is not measured against its
predecessor is an assumption wearing an implementation.

---

## §42 — `ATTAK2` settled, and the 33 % was a selection bug all along (2026-08-11)

```
ATTAK2   0.00106695 * exp(0.09966 * v) s    55..85   r2 0.99962
```

Same convention as `ATTAK1` (§41): the time to cross the **full travel**, from
the contiguous 10-90 % time divided by 0.8.

**Two definitions sharing no arithmetic agree.** The threshold above, and the
gradient method `ms.ramp_duration`, give per-point differences of 6.0 % and
exponents of 0.09966 against 0.09947 — **0.19 % apart**. That is what a settled
quantity looks like, and it is the standard §34 established.

The 33 % that kept this provisional was the same **non-contiguous selection**
bug fixed in `ramp_rate` (§41): one implementation took the last sample inside
the 10-90 band rather than the last of the first contiguous run, so a later
sample rejoining the band stretched the window. Predicted to make that
implementation read *longer*, and it did.

**The published coefficients were never far wrong** — within 1.3 % across
60..80. What was wrong was the confidence interval, which I built by comparing
two implementations one of which was broken. A disagreement between two of
your own tools bounds their difference, not the measurement's uncertainty.

`ATTAK1` 0.10844 against `ATTAK2` 0.09966: the two attacks are genuinely
different laws, each now confirmed under two definitions.

### The same fault, three times, in three hand-rolled selections

1. `ramp_rate` taking every sample in the band (§41)
2. `attack_shapes` taking the last sample in the band rather than of the run
3. the 3rd-percentile baseline collapsing when the rise was 2.8 % of the capture

All three are one mistake: **a statistic taken over the whole record when the
region of interest is a small and varying fraction of it.** At `ATTAK2` 55 the
rise occupies 2.8 % of a 9 s capture and at 85 it occupies 56 %, so no fixed
percentile can serve both — the baseline must come from the first samples
after note-on, which does not care how long the rise takes.

Writing that selection once, in `s3k/measure.py`, with tests, is the fix. I
wrote it three times in probes and got it wrong twice.

### And a note on how the diagnosis went

Two hypotheses about the NaN, each plausible, each wrong, each costing a full
hardware run: the ruler's lower bound, then the base/depth range. The third
step was a diagnostic that printed every intermediate — gate survivors,
on-ruler counts, the actual percentiles — and answered it immediately.

**After the first wrong hypothesis, instrument rather than theorise.** The
second guess is rarely better than the first, and the dump was cheaper than
either.

One more thing the failure exposed: `BASE = 52` had been copied from an
earlier run's ruler, and the ruler's bottom moves between sessions (52, then
54) because it is set by where the noise floor cuts in. **A constant carried
from one run's conditions into another run's setup** is a hidden coupling; base
and depth are now derived from the ruler measured in the same session.

---

## §43 — `K_FREQ` is exactly what the document says, once the reference is known (2026-08-11)

```
shift (FILFRQ units) = 0.06386 * K_FREQ * (note - 64)
```

**Referenced to note 64.** Slopes of +0.1310, +0.5362 and +0.8973 units per
`K_FREQ` step at notes 66, 72 and 78 extrapolate to zero shift at **note 63.8**
(r² 0.99890). At `K_FREQ` 12, one octave above that reference, the corner moves
**9.2 FILFRQ units against 9.39 for true 1:1** — 98 %.

So the field *is* semitones of corner shift per octave of key, exactly as the
document states, and 12 *is* full tracking. The apparent shortfall — 8.4
semitones per octave instead of 12 — was entirely my assumption that the
reference was note 60.

### Everything pivots on 64

| field | source | pivot |
|---|---|---|
| `V_LOUD` | velocity | 64 (§37) |
| `V_ATT1` | velocity | 64 (§40) |
| `K_FREQ` | note | 64 (here) |

MIDI velocity runs 1..127 and note 0..127; the centre of both is 64. **The
machine references modulation to the middle of the controller's range**, not
to middle C, not to the sample root. Three fields across two source types.

This is worth having because it predicts: `MWLDEP`, `PRSDEP`, `VFREQ1` and
`VPANO1` should pivot on 64 too. A rule that forecasts unmeasured fields can be
refuted by them, which is the difference between a pattern and a decoration.

### Three failed attempts, each a different way of misreading the ruler

1. **Base below the fold.** At note 84 the fold sits at FILFRQ 70, against 52
   at note 60, because higher harmonics push the noise-dominated region up. A
   base of 58 put every reading on the falling branch, so a monotonic shift
   came back as a **V**. §33 records that only the rising branch may be
   inverted; I recorded it and then hardcoded a base below it.
2. **Excursion wider than the ruler.** With the base fixed, `K_FREQ` 12 walked
   off whichever end it moved toward and returned NaN. The ruler at note 84
   spans 28 units; two octaves of tracking needs 18.8.
3. **Signal and headroom trade against each other.** Testing further from the
   reference increases the shift but shrinks the ruler, because the fold rises
   with pitch. One octave was the compromise that fits.

The fix in every case was the same: **derive the operating point from the
ruler measured in this session** instead of carrying a constant across
conditions. That is now twice in one day — `BASE = 52` in the `ATTAK2` probe
and `FIXED_FILFRQ = 58` here — where a number copied from an earlier run's
conditions silently invalidated a measurement.

### And a bonus fact the machine never states

Note 64 as the tracking reference is *measured*, not read from any field. If a
converter needs to know where filter tracking pivots — and it does, to write
`K_FREQ` at all — there is no header byte to read it from.

---

## §44 — All four LFO1 depth sources are one law, and the pivot rule is refuted for controllers (2026-08-12)

```
cents_pp = 1930 * (DEPTH/99) * (CONTROLLER/127)
```

with `MWLDEP` taking the mod wheel, `PRSDEP` channel pressure, `VELDEP`
velocity — and `LFODEP` the same full scale with no controller.

| source | full-scale depth |
|---|---|
| `LFODEP` 99 | 1930 cents (§35) |
| `MWLDEP` 99, wheel 127 | 1928 |
| `PRSDEP` 99, pressure 127 | 1931 |
| `VELDEP` 99, velocity 127 | 1929 |

Within 0.2 % of each other. The per-unit scales agree twice over too:
`MWLDEP`'s own axis gives 19.5299 cents/unit against `LFODEP`'s 19.4932, and
the wheel gives 15.2001 cents/unit against pressure's 15.20.

### The §43 prediction is refuted, and the boundary is the interesting part

§43 predicted that `MWLDEP`, `PRSDEP` and the per-zone velocity fields would
pivot on 64, as `V_LOUD`, `V_ATT1` and `K_FREQ` do. **They do not.** The wheel
is proportional to its value — 0.50 of full depth at wheel 64, r² 0.99989.

The boundary is not arbitrary. **Velocity and note have an inherent centre** —
every note carries a velocity, every key a pitch — so a bipolar modulation
about the midpoint is meaningful. **A wheel rests at zero**, and a pivot there
would mean a wheel at rest applying maximum negative modulation, which is
unusable. So the rule becomes: *sources with an inherent centre pivot at 64;
unipolar controllers scale from zero.*

`VELDEP` is the case that proves the distinction is about the **route** rather
than the source: its source is velocity, which pivots elsewhere, but as a
*depth* control it scales from zero like the wheel. `V_LOUD` pivots; `VELDEP`
does not; both are driven by velocity.

### `PRSDEP` was not inert — my timing was wrong

An earlier run sent channel pressure **before** note-on and saw nothing, and
"inert" was the available reading. It is not inert: sent 0.4 s **after**
note-on it is linear across the whole controller range, reaching the same 1930
cents.

That is the second time today a field looked dead because of how it was
driven rather than what it does, after `MODSFILT1` (§28). Worth stating as a
rule: **before recording a field as inert, vary how it is driven, not only
what it is set to.** Auto-pan and `VLOUD1` (§39, §40) were both recorded as
"nothing reachable from these fields moves the output" rather than
"unimplemented" for exactly this reason, and that caution now looks justified.

### And the new guard silently no-opped

`residual_structure()` reported *"longest sign run 0, growth nan → random"* on
a six-point fit. It needs eight, and below that it returned `False` — which
reads as "checked and fine". **A check that cannot say "I do not know" is the
fault the function was written to catch**, and it had it on the first outing.
It now returns `None`, and the test asserts `is None` rather than falsiness.

### Schedules are now written beside every capture

Per mpc2emu's night-run note: each capture writes a `.sched.json` sidecar with
note on/off times, controls, velocity, ports, window and hop, and UTC. Their
July gain captures survive without schedules, and two segmentations written
half an hour apart read the same file as −55.1/−43.1/−30.1/−19.0 dB and as
−24.8/−24.4/−24.6/−24.2/−19.2 dB. Seventeen sidecars from this run.

---

## §45 — RETRACTED by §51. The whole per-zone velocity block is inert (2026-08-12)

> **This section is wrong and is superseded by §51.** All four fields
> work; the detector below measured a velocity spread, which a static
> per-zone offset cannot produce. Left in place rather than deleted so
> the reasoning that failed stays legible.

`VLOUD1`, `VFREQ1`, `VTUNO1` and `VPANO1` — velocity to level, filter, tune
and pan, all in `keygroup.zone.1` — produce **no measurable response** on this
S3000XL, each screened against its own detector with velocity taken from 1 to
127 at both extremes of the field:

| field | detector | required | seen |
|---|---|---|---|
| `VLOUD1` | level | 3 dB | 0.0004 dB (§40) |
| `VFREQ1` | spectral centroid | 300 Hz | none |
| `VTUNO1` | fundamental | 3 Hz | none |
| `VPANO1` | stereo balance | 3 dB | none |

Meanwhile velocity works perfectly through the program and keygroup routes:
`V_LOUD` spans 30 dB (§37) and `V_ATT1` spans nearly three seconds of attack
(§40), measured on the same rig in the same conditions.

So this is not a velocity problem, a detector problem or a routing problem at
the program level. **Nothing reachable from the per-zone velocity fields moves
the output.**

### Framed as a measurement, not a verdict

Recorded as *inert in this configuration*, not as *unimplemented*. What is not
established: whether they need the IB304F board, whether they require more than
one zone to be populated, or whether some enable elsewhere gates the whole
block. Only zone 1 carries a sample here.

That caution has already paid once. `PRSDEP` looked inert for a full run and
was not — pressure simply had to arrive during the note rather than before it
(§44). The rule that came out of it is applied here: the *drive* was varied,
not only the setting, and velocity is the only drive these fields have.

### A pattern, and what would test it

Three groups now measure as inert: the pan LFO (`PANDEP`, `PANRAT`, `PANDEL`,
`LFO2WAVE`, `LFO2TRIG` — §39), the per-zone velocity block (here), and nothing
else. Everything at program level and in the keygroup envelopes works.

If one thing gates all of them it would most plausibly be the optional board,
which this machine may not have. That is not testable without the board, and
saying so is more useful than a guess: **a converter should treat auto-pan and
per-zone velocity modulation as unavailable and write neither**, rather than
emitting values that do nothing on a machine like this one.

---

## §46 — `LFO1WAVE` confirmed by shape, and a fourth waveform (2026-08-12)

LFO1 drives pitch, so the pitch track *is* the waveform. Measured:

| value | time in middle third | slope asymmetry | shape |
|---|---|---|---|
| 0 | 0.34 | 1.01 | **triangle** |
| 1 | 0.29 | 16.61 | **sawtooth** |
| 2 | 0.00 | 1.04 | **square** |
| 3 | 0.16 | 1.04 | a fourth shape, not identified |

The three documented values are confirmed exactly as written. **Value 3 is
undocumented and real**: symmetric like a triangle (asymmetry 1.04) but
spending half as long near its centre (0.16 against 0.34), so neither a
triangle nor a square. Consistent with a sine or a trapezoid; not resolved,
and recorded as such.

### Four attempts, and the third one is the instructive failure

1. **60 ms window** — "too few pitch points" at every value. Blamed the
   vibrato depth.
2. **50 ms window, slower shallower LFO** — same failure. Blamed it again.
   The actual cause: `fundamental_hz` defaults to `lo = 20 Hz`, so its
   autocorrelation needs a 50 ms lag to exist and *any* window at or below
   that returns NaN for *any* signal. **Six lines of synthetic test found it
   in seconds, after two hardware runs had not.** The function now raises on
   an impossible window rather than returning NaN, because NaN there is
   indistinguishable from "no pitch present".
3. **20 ms window at `lo = 150 Hz`** — fourteen times the shape resolution,
   and the margins moved by **0.005**. The null result is what localised the
   real fault.
4. **Shape statistics** — decisive at once.

### Why correlation could never have worked

The three candidates correlate **with each other** at 0.75 to 0.87. Correlation
is dominated by the fundamental, which triangle, sawtooth and square all share;
everything that distinguishes them lives in the harmonics, which correlation
barely weights.

So it was a weak discriminator *by construction*, and no amount of time
resolution could fix a statistic that was not looking at the difference. Two
shape statistics separate them cleanly instead: **square sits at its extremes**
(0.00 of the time near centre against 0.33), and **triangle rises and falls at
equal rates** where sawtooth does not.

**I chose correlation because it was the obvious tool, not because I had
checked it could see the distinction I needed.** That is the same fault as
§36's four candidate models fitted without asking whether the true one was
among them, and as an r² that cannot see a broken fit. The check is cheap and
I keep skipping it: *before measuring with a statistic, compute it on the
candidates themselves and confirm they differ.*

The classifier was verified on synthetic triangle, sawtooth and square before
the capture — all three classified correctly — which is the discipline the
first two attempts lacked.

---

## §47 — Five of six envelope scaling fields are inert; `K_DAR1` is not (2026-08-12)

| field | drive | detector | verdict |
|---|---|---|---|
| `K_DAR1` | key | decay rate, dB/s | **RESPONDS**, 24.6 dB/s across its range |
| `V_REL1` | velocity | release rate, dB/s | inert |
| `O_REL1` | velocity **and key** | release rate, dB/s | inert to both |
| `V_ATT2` | velocity | filter rise **time** | inert |
| `V_REL2` | velocity | filter fall **time** | inert |
| `V_ENV2` | velocity | steady centroid | inert |

`K_DAR1` also settles what the `K_` prefix means in this group: **key**, not
keygroup. That makes `K_DAR2` its likely companion on envelope 2.

### Three of those verdicts were wrong on the first pass

The first screening recorded five inert. Three of them were not established,
because the detector could not have seen the effect whether or not it existed:

- **`V_ATT2` and `V_REL2`** change a *time*, and I measured a **median
  centroid over 0.3–2.5 s**. A timing change does not move a steady-state
  median — the filter arrives at the same place, only sooner or later.
- **`O_REL1`** was driven by *velocity*, while `K_DAR1` sits beside it in the
  same group and turned out **key**-scaled. A key-scaled field driven by
  velocity shows nothing regardless of what it does.

Re-screened with a 10-90 % rise timing, a post-note-off fall timing, and a key
drive, all three are confirmed inert. The verdicts are the same; only now are
they *measurements* rather than artefacts.

**An inert reading is only as good as the detector's ability to have seen the
alternative.** That is the third time tonight — after the pan LFO and `PRSDEP`
— and the pattern is specific enough to be a rule: *before recording a field
as inert, state what the detector would show if the field worked, and confirm
the detector can produce that.* Written before the run, not after.

### What now measures as inert on this machine

Three groups: the pan LFO (§39), the whole per-zone velocity block (§45), and
five of six envelope scaling fields (here). Fourteen fields.

Everything at program level and the two keygroup routes that do work —
`V_ATT1` and `K_DAR1` — behave normally. The common factor among the dead
ones, if there is one, is not visible from here; the optional IB304F board
remains the obvious candidate and is not testable without it.

A converter should write none of the fourteen.

---

## §48 — `K_DAR1` measured, and envelope 3 is alive after all (2026-08-12)

### `K_DAR1`: key scaling of the amplitude decay

```
rate = 25.3 * exp(-0.0015286 * K_DAR1 * (note - 64)) dB/s     r2 0.9960
```

```
K_DAR1   n48    n56    n64    n72    n84
   -50    7.4   12.7   25.3   46.1  110.2
     0   24.5   25.3   25.3   25.3   25.3
   +50   82.0   46.2   25.3   12.7    5.4
```

Two things are visible in the table rather than fitted: **the `K_DAR1` 0 row is
flat at 25.3 dB/s across 36 semitones**, an exact control, and **every depth
reads 25.3 at note 64**. So the §43 pivot is not an extrapolation here — it is
the column where all five rows meet.

That makes two independent key-driven fields referenced to note 64, after
`K_FREQ`. Positive depth makes high notes decay *slower*, by a factor of 0.40
per octave at full depth.

### Envelope 3 exists, is not an ADSR, and reaches the filter through the matrix

Two mistakes stood between me and this, and both are ones already recorded
tonight in other places.

**First: I screened for the wrong names.** `ATTAK3`/`DECAY3`/`SUSTN3`/`RELSE3`
do not exist. Envelope 3 is **four rate/level pairs** — `ENV3R1..R4`,
`ENV3L1..L4` at offsets 179–186 — a different architecture from envelopes 1
and 2, and the table had it all along. The probe reported "no envelope-3
fields present", which was true of the names I asked for and false of the
machine.

**Second: I drove it with no source routed**, and got "inert" on both
destinations. Envelope 3 is **source 14** in the assignable matrix. `MODVFILT1`
applies a depth to whatever `MODSFILT1` selects, so with source 0 selected the
depth applied to nothing — the identical fault as §28, where a missing
`MODSFILT1` line made eleven `SUSTN2` values come back within 1 Hz of each
other.

With source 14 routed and source 0 kept as a control **in the same session**:

```
source  0 (control):  levels -> amplitude no response, filter no response
source 14 (env3):     levels -> filter RESPONDS 3664 Hz
                      rates  -> filter RESPONDS 3473 Hz
```

Envelope 3 is a working auxiliary envelope with no fixed destination, reaching
the filter — and presumably anything else the matrix serves — only when
selected. It does not touch amplitude directly.

### Four near-misses in one night

Auto-pan, the per-zone velocity block, five envelope scaling fields, and this.
The first three survived scrutiny; **this one did not, and it was the only one
where the control was decisive rather than corroborating.**

The rule from §47 was *state what the detector would show if the field worked*.
This adds the other half: **state what would have to be true for the field to
reach the detector at all** — routing, drive, timing — and check each before
concluding absence. Three of tonight's four inert verdicts needed that check
and passed it; the fourth needed it and failed.

The machine ships envelope 3 configured with a real shape (`ENV3L1..L3` at 99,
`ENV3L4` at 0, rates 0/50/0/45). A field the factory bothered to configure is
weak evidence against inertness, and worth treating as a prompt to look harder
rather than as noise.

---

## §49 — Measured findings cross-checked against the written specification (2026-08-12)

Every night-run finding put against the written sources. **Nothing measured is
withdrawn here** — the point is to mark where the document agrees, where it is
silent, and where the two appear to disagree.

### What was actually consulted, and the weakness in it

| URL | what it is | contributed |
|---|---|---|
| `lakai.sourceforge.net/docs/s2800_sysex.html` | S2800/S3000/S3200 SysEx parameter spec | **every confirmation below** |
| `lakai.sourceforge.net/docs/s2000_sysex.html` | S2000/S3000XL multi-mode addendum | nothing — silent on all eight questions |
| `archive.org/.../S3000XLOM/S2000S3000xlS3200xl-SysexDocumentation_djvu.txt` | **the same multi-mode addendum** | nothing |

Three fetches, two of them the same content, so **every `CONFIRMED` row below
rests on a single document**. Three limits follow and none of them is small:

1. **No owner's manual was read.** The archive.org item is named `S3000XLOM`
   and I took "OM" for owner's manual; it is the SysEx documentation. This
   bears directly on the `IB304F` row: an option board is exactly the sort of
   thing an owner's manual describes and a SysEx spec does not, so "absent from
   the documents" here means "absent from two documents that would not have
   mentioned it either way". That is much weaker than it first reads.
2. **The confirming document is for a different model.** This machine is an
   S3000**XL**; the spec that confirms everything is S2800/S3000/S3200. §3 of
   these notes already records that keygroup offsets 161/162 *differ* between
   the base S3000 and the XL, so cross-model agreement carries a known caveat.
3. **The S1000 document was not consulted at all**, though §1 lists it.

What survives the caveats: the rows the hardware independently corroborates —
source 14 routing envelope 3 to the filter, `LFO1WAVE` 0/1/2 matching the
measured shapes, `K_FREQ` reaching 1:1 at 12. Those agree with the document
*and* with the machine. The rows resting on the document alone — chiefly the
envelope-3 field names and the alias list — are single-source and cross-model.

### CONFIRMED — the document says what the machine does

| finding | document's own words |
|---|---|
| Envelope 3 is **rate/level**, not ADSR (§48) | `ENV3R1` (179) *"Attack rate of envelope 3"*, `ENV3L1` (180) *"Final level of attack phase (phase 1)"*, through `ENV3L4` (186) *"Final target level"* |
| Envelope 3 reaches the filter as **matrix source 14** (§48) | source list: *"14: Env3"* |
| `K_FREQ` is **semitones per octave**, 12 = 1:1 (§43) | offset 8, range *"0 to 12 semitones"* |
| `LFO1WAVE` 0/1/2 = triangle/sawtooth/square (§46) | *"0 = Triangle, 1 = Sawtooth, 2 = Square"* |
| `MWLDEP`, `PRSDEP`, `VELDEP` all drive **LFO1 depth** (§44) | *"Amount of control of LFO1 depth by Modwheel / by Aftertouch / by Note-On velocity"*, each 0–99 |
| `V_LOUD` is velocity→loudness, −50..+50 (§37) | *"Note-on velocity dependence of loudness"* |
| `PANRAT`/`PANDEP`/`PANDEL` are **LFO2** (§39) | *"Speed of LFO2" / "Depth of LFO2" / "Delay in growth of LFO2"* |

The measured **one-law result for LFO1 depth** (§44) is corroborated by the
document's phrasing: all three controllers are described as controlling *the
same thing*, "LFO1 depth", which is exactly what reaching the same 1930-cent
full scale by three routes means.

### GAP — measured, but the document does not address it

- **The pivot on 64.** `K_FREQ` at offset 8 is given as "0 to 12 semitones"
  with **no centre value stated**; `V_LOUD` as "−50 to +50" with no reference.
  The note-64 reference for `K_FREQ` and `K_DAR1`, and the velocity-64 pivot
  for `V_LOUD` and `V_ATT1`, are measurements with no documentary counterpart.
- **`LFO1WAVE` value 3.** The document lists three waveforms. The fourth shape
  measured at value 3 is undocumented — consistent with §46 recording it as
  real but unidentified.
- **The IB304F.** *Neither document mentions it.* This matters: the board has
  been cited repeatedly in these notes as "the obvious candidate" for the inert
  fields, and that attribution has **no support in either source consulted
  here**. It needs its own provenance check before being repeated again.
- **Envelope 3's destination.** The document lists its parameters and its
  presence as source 14 but *"does not explicitly state a fixed destination"* —
  matching the measurement that it reaches the filter only when routed.

### APPARENT CONTRADICTION — the document describes a working feature the machine does not perform

- **Auto-pan.** `PANRAT`, `PANDEP` and `PANDEL` are documented as functioning
  LFO2 controls, and §39 measured them producing no change in stereo balance
  across ~30 settings while `PANPOS` moved the same measurement 118 dB.
- **The per-zone velocity block.** `VLOUD1`/`VFREQ1`/`VTUNO1`/`VPANO1` are
  defined in the header, and §45 measured all four inert.
- **Five of six envelope scaling fields** (§47), likewise defined and likewise
  inert.

These are not contradictions *of the document's descriptions* — the fields
exist at the offsets given, and writes to them are accepted and read back. What
is contradicted is the implication that writing them does something audible on
this machine. Both readings remain open: an option this machine lacks, a
configuration prerequisite not identified, or a documented feature the XL does
not implement.

### One correction to an earlier note

§48 said envelope 3's ADSR names "do not exist". More precisely: **this
project's parameter table** uses `ENV3R1`…`ENV3L4`, while the source document
gives *both* namings, listing `ATTAK3`, `DECAY3`, `SUSTN3` and `RELSE3` as
aliases for `ENV3R1`, `ENV3R3`, `ENV3L3` and `ENV3R4`. The probe failed against
this project's table, not against the specification.

### Method note

These extractions came from an automated read of one document rather than
a human reading it end to end. The quotes above are load-bearing for
`CONFIRMED` rows and are worth one human eye before anything is built on them —
particularly *"14: Env3"*, which the hardware independently corroborates, and
the envelope-3 field names, which it does not.

---

## §50 — The IB304F is not fitted, and it was never the explanation (2026-08-12)

Jan pointed at the S3000XL owner's manual sitting in
`~/Dokumente/SYNTHS/Akai S3000XL/Docs/` — the manual, its addendum, all three
lakai SysEx documents, and the IB-304F installation sheet. §1 of these notes
says "local cache paths" and I fetched from the web instead of looking.

### The board is absent, measured

```
LSI2_ON factory value    0
FIL2FR swept 20..99      no response, with LSI2_ON at 1 AND at 0
```

No second filter exists on this machine. `LSI2_ON` accepts a write and reads
back 1, which is worth knowing on its own: **the flag is settable over SysEx
and means nothing without the hardware**, so it cannot be used to detect the
board.

### It never explained the inert fields anyway

The board adds, per the German review Jan supplied: *"Direct-to-Disc-Recording,
Hall, einen digitalen Equalizer und ein zweites, deutlich flexibleres Filter"*
— direct-to-disk recording, reverb, a digital EQ, and a second filter.

**Auto-pan and the per-zone velocity block are not among them.** So the
hypothesis repeated across §39, §45 and §47 — that the IB304F is "the obvious
candidate" for those fourteen inert fields — is **withdrawn**. It was never
supported, and §49 already flagged that it had no documentary basis; now it has
a documentary refutation. Why those fields are inert remains open.

### And envelope 3 works anyway — against the manual's grouping

The owner's manual says:

> 注意： FILTER 2、 TONE ページ、 ENV3 の **説明** は、 オプションの IB304F
> フィルター バンク LSI が 取り付けられている 時 だけにのみ 当てはまります。
>
> *"Note: the **descriptions** of FILTER 2, the TONE page and ENV3 apply only
> when the optional IB304F filter bank LSI is fitted."*

And the review lists the base machine as having *"zwei Hüllkurven, zwei LFO"* —
two envelopes, two LFOs.

Yet §48 measured envelope 3 responding on this board-less machine: source 14
routed, levels moving the filter 3664 Hz, against a same-session source-0
control that moved nothing.

The reading that fits every piece: **the manual's sentence is about its own
descriptions and about front-panel access, not about the generator existing.**
`説明` is "descriptions". Without the board the ENV3 pages are not displayed —
the manual says exactly that two lines earlier, along with the panel error
*"2nd filter board IB304F not fitted!"* — but the generator is present in
firmware and reachable over SysEx through the modulation matrix, where the
S2800 spec documents it at source 14 with no board caveat at all.

**So on an unexpanded S3000XL, envelope 3 is unreachable from the panel and
fully usable over SysEx.** That is worth having: it is a capability the manual
tells a user they do not have.

### The caveat that keeps this honest

Every byte offset in this project is an unverified transcription (§2, DISCLAIMER).
Strictly, what was measured is: *writing offsets 179–186 changes the filter when
`MODSFILT1` is 14 and not when it is 0.* That those offsets are envelope 3 rests
on the same transcription as everything else. The interpretation above is the
most economical one, not a proven one.

### What I should have done first

The documents were on the machine the whole time. Two web fetches returned the
same multi-mode addendum and told me nothing, and the answer was one `find`
away. The rule is dull and I keep needing it: **look locally before fetching**,
especially when the project's own notes say a local cache exists.

---

## §51 — RETRACTION. The per-zone fields are not inert; §45 tested the wrong thing (2026-08-12)

§45 recorded `VLOUD1`, `VFREQ1`, `VTUNO1` and `VPANO1` as inert. **All four
work, and strongly.** Measured at a single fixed velocity with the field set to
each extreme:

```
VTUNO1     0.34  ->    19.72 cents      19.39 cents across 0..50
VLOUD1   -68.65  ->   -28.84 dB         39.81 dB
VFREQ1  7827.73  ->  4481.76 Hz       3346    Hz
VPANO1   -59.64  ->    58.93 dB        118.57 dB
```

`VPANO1` spans the full 118 dB that `PANPOS` does. `VTUNO1` comes out at
**0.3878 cents/unit against `KGTUNO`'s structural 0.390625** — 0.7 % apart, the
same 1/256-semitone scale.

### What the detector could not see

§45's measurement function returned the **velocity spread** — the quantity at
velocity 127 minus at velocity 1 — and `verify_responds` compared that spread
at field −50 against field +50. For a **static** per-zone offset the spread is
zero at both extremes, so the difference is zero and the field reads inert
whether or not it works. The detector could not have produced a positive
result.

These are static offsets. **The `V` in the zone field names refers to the
velocity ZONE the field belongs to, not to velocity as a modulation source**:
`LOVEL1`/`HIVEL1` define the zone, and `VTUNO1`/`VLOUD1`/`VFREQ1`/`VPANO1` are
that zone's offsets. With a sample in zone 1 only, a velocity sweep never
leaves zone 1 and a static zone-1 offset is constant across it by construction.

Credit where it is due: mpc2emu caught this, from the document's structure —
`VTUNO1` carries the identical spec wording to `KGTUNO` and `PTUNO`, both plain
static offsets — and flagged it rather than acting on §45. They were right not
to drop those bytes from their writer.

### Why §47 survives and §45 did not

Both screened a batch of fields with `verify_responds`. The difference:

- **§47** had `K_DAR1` **respond** at 24.6 dB/s through the same detector
  shape. A positive control, in the same run.
- **§45** had **nothing** respond. Four fields, four negatives, and nothing
  validating that the detector could register anything at all.

**A screening batch in which nothing responds has not validated its own
detector.** That is the rule this cost, and it was visible at the time: an
all-negative batch should be held as suspect until something in it goes
positive, or until a known-good field is added deliberately to make one.

§47's rule was *state what the detector would show if the field worked*. This
adds the enforcement: **make it show that, on something, in the same run.**

### The inert count

Ten fields, not fourteen: the pan LFO group (§39) and five of six envelope
scaling fields (§47, positive control present). The per-zone block is
withdrawn from that list entirely.

---

## §52 — RETRACTION. LFO2 works; only its route to pan is dead (2026-08-12)

§39 recorded five fields as inert — `PANRAT`, `PANDEP`, `PANDEL`, `LFO2WAVE`,
`LFO2TRIG` — and called the finding "the pan LFO does nothing". **All five
work.** Every one of them was tested against the pan destination, which is the
single broken component.

LFO2 is **assignable-matrix source 8**. Routed to the filter it modulates
cleanly, at 25–99× prominence with 4000–10000 Hz of excursion, against controls
(source 0, and source 8 with `PANDEP` 0) that show 15 Hz of drift.

```
PANRAT    rate = 0.23708 * PANRAT Hz      5..80    r2 0.999843
PANDEP    gates the depth -- 0 silences it entirely with the route live
PANDEL    delays the growth -- early/late swing ratio 1.14, 1.05, 0.72,
          0.13, 0.17 across 0..99; at 99 the delay exceeds a 6 s capture
LFO2WAVE  changes the shape -- wave 0 middle-third 0.27 against 0.09/0.01/0.05
LFO2TRIG  mode 1 locks the phase to note-on: sd 54 Hz across five notes,
          against 307, 312 and 471 Hz for the free-running modes
```

**LFO2 runs at exactly twice LFO1** for the same parameter value — 0.23708
against `LFORAT`'s 0.11867, a ratio of 1.998.

### Three detectors in a row that could only give one answer

This topic took four runs, and three of them produced a verdict the detector
was incapable of contradicting:

1. **The rate above `PANRAT` 80** read exactly half the extrapolation — ratios
   0.502, 0.501, 0.500, 0.504. A value that lands on precisely half is an
   artefact; the analysis window smoothed adjacent peaks and the FFT took the
   subharmonic. The fold **moved** when the window shortened (above 60 at
   40 ms, above 80 at 15 ms), which proves it is mine — but it did not move
   *proportionally*, so the mechanism is still not understood and the fitted
   range simply stops at 80.
2. **`PANDEL` read −0.150 s at all five settings.** A negative onset is
   impossible, and an identical impossible value everywhere is a floor: the
   rolling window was 0.42 s and the arithmetic made any onset before 0.57 s
   return exactly that. Replaced with an early-window against late-window
   comparison, which estimates no onset and therefore has no floor.
3. **`LFO2TRIG` showed rate flat to 0.0 % and swing to 2.6 %** — both correctly
   measured and both irrelevant, because a trigger mode sets the LFO's *phase*
   and changes neither by construction.

§47's rule — *state what the detector would show if the field worked, and
confirm it can produce that* — was written by me four sections earlier and
violated three times inside one topic. **Stating a rule is not applying it.**
The rule needs to fire while the probe is being written, and the only mechanism
that has actually worked is the positive control: every run since §51 proves
the route live before reporting any negative, and that is what caught these.

### The inert count

**Five fields, not ten**: the five envelope scaling fields of §47, which still
have a positive control (`K_DAR1`) and still stand. The pan group is withdrawn
entirely.

What remains genuinely inert is **one destination**: the pan output of LFO2.
`PANPOS` moves the stereo image 118 dB, and LFO2 modulates the filter, but LFO2
does not reach pan. That is a much narrower and more useful statement than "the
pan LFO does nothing", and a converter can now write `PANRAT`/`PANDEP` for
matrix use while knowing auto-pan itself will not sound.

---

## §53 — `FILQ` sets damping, and damping is linear (2026-08-12)

§33 fitted `FILQ` as 0.5764 dB per step and said in its own text that the
number was a lower bound. It was: the true law is not linear, not in dB, and
the last three steps are worth more than the first ten put together.

```
damping   z = 0.46864 - 0.029587 * FILQ        r2 0.999975   (2985 points)
boost     dB = -20 log10(1 - FILQ / 15.84)
Q         = 1.067 / (1 - FILQ / 15.84)         1.07 at 0, ~20 at 15
corner    919 Hz, the SAME at every one of the sixteen settings
```

Damping reaches zero at **FILQ 15.84** — past the top of the field — so the
machine stops just short of self-oscillation and the top step is the loudest
resonance it can make. That single number generates all sixteen steps.

### Sliding the comb instead of the corner

Every earlier attempt held a note and read the filter through that note's
harmonic comb, 261.6 Hz apart. **Between the teeth the response is not
observable**, so a resonance narrower than the spacing is measured wherever a
tooth happens to fall. At Q 20 the peak here is 46 Hz wide against a 262 Hz
comb.

`K_FREQ` 0 stops the filter tracking the keyboard. With `FILFRQ` fixed, every
NOTE puts its harmonics at different absolute frequencies while the corner
stays put, so the comb slides across a stationary filter. Eleven notes at
three-semitone steps give 208 points; thirty-one notes at one-semitone steps
interleave nine harmonic indices through the corner and give 588.

What is measured at each point is a difference:

```
dB(f) = level(FILQ = q, note, h) - level(FILQ = 0, note, h)
```

Same note, same harmonic, same sample — so the source's spectrum, the pitch
shift, the converters and the room divide out exactly. **And because it is a
ratio, any fixed non-resonant poles cancel too**, which is what makes the
two-pole model legitimate on a filter that is not two poles: the difference
sees only the pair whose damping `FILQ` moves.

Fitting that model to all 2985 points, rather than reading the height of a
peak, is what turns a lower bound into a law. Two runs with different note
sets agree: fc 919 against 918 Hz, slope -0.029587 against -0.029688, zero
damping at 15.84 against 15.93.

### Three errors of mine, each caught by a check the previous one taught

1. **A summary statistic hid the thing it was summarising.** The first run
   reported "median spacing 10 Hz between 900 and 1700 Hz" — true, and
   useless. The harmonics cluster elsewhere in that band; the corner's own
   neighbourhood had **one** point within 40 Hz, with neighbours at 881 and
   988. The tell was that the smoothing half-width made no difference from
   6 Hz to 40 Hz, which is impossible for a peak narrower than the window.
   *Report the density where the feature is, never averaged over a band.*
2. **The error bar measured the wrong thing.** The null pass — FILQ 0 run
   twice and differenced — gave sd 0.016 dB, and that is a real number for
   what it measures: whether one note-and-harmonic path repeats. It says
   nothing about whether two DIFFERENT paths landing on the same frequency
   agree, and they scatter by 1–2 dB. The global fit's rms is 0.274 dB, 17x
   the null. *A repeat that follows the same path measures repeatability, not
   accuracy.*
3. **The estimator was biased and the model paid for it.** Reading peak
   height under-reads at high Q — 23.2 dB observed at FILQ 15 against 25.5
   fitted — and that bias appeared as systematic curvature in the residuals,
   which I first tried to fix by adding parameters to the model. The fix was
   a better estimator, not a bigger model.

The dense re-measurement is worth recording as a negative result too: it moved
the peaks by at most 0.07 dB. The under-sampling was real, the correction was
not, and it was still the right run to make — that is what checking costs when
the answer turns out fine.

### What this leaves open

The corner sits **0.42 octaves below** what §22's `FILFRQ` law predicts — 919
Hz against 1229 nominal at `FILFRQ` 70 — and that replicates §33's 0.41
octaves at `FILFRQ` 77. Two operating points, two methods, same offset: the
fields genuinely do not share a reference.

§22 fitted `FILFRQ` by inverting a spectral centroid, and a centroid is not a
corner. A resonance peak is, and this measurement locates it to about a hertz.
**Re-deriving `FILFRQ` from the resonance peak at high `FILQ` should replace
the centroid ruler** — and would also be free of the fold that makes the
centroid ruler invertible only on its rising branch (§20). That is the next
item.

---

## §54 — `FILFRQ` re-derived from the resonance peak (2026-08-12)

§20 fitted `FILFRQ` by inverting a spectral centroid. The law was wrong by
20–30%, and wrong by a growing amount:

```
                    resonance peak      §20 centroid      error
    FILFRQ 40            111 Hz            134 Hz      -0.28 octaves
    FILFRQ 70            930 Hz           1230 Hz      -0.40 octaves
    FILFRQ 99           7291 Hz          10465 Hz      -0.52 octaves
```

The replacement:

```
    Hz = 6.4597 * exp(0.07100 * FILFRQ)     44..92    r2 0.99984
    one octave per 9.76 units; 7.36% per step, about 0.81 semitones
```

### Why the old ruler was wrong, and why the error grew

A spectral centroid is the average frequency of **everything the source
contains**. It sits above the corner by an amount set by how much energy the
source has above it — so it is a measurement of the source and the filter
together, and the mix changes as the corner moves. That is a **slope** error,
not an offset: no single correction factor would have fixed §20, which is why
the discrepancy §33 first noticed (0.41 octaves at one `FILFRQ`) could not be
written off as a calibration constant.

§53's difference measurement has none of that in it. Turning `FILQ` up grows a
resonant peak AT the corner; differencing the spectrum against `FILQ` 0 at the
same `FILFRQ` cancels the source's own spectrum, the pitch shift, the rig gain
and **every fixed pole**, leaving only the pole pair whose damping `FILQ`
moves. Its frequency is the corner, and nothing about the source enters.

The old ruler also **folded** (§20): it fell to a minimum and rose again, so
only its rising branch could be inverted, and the fold moved with pitch. That
too was a property of the centroid, not of the filter. A resonance peak does
not fold. §20's fold finding stands as a description of the centroid; its
`FILFRQ` law does not.

### The cross-checks, none of which were fitted to each other

- **§53 predicted the damping at every corner.** It was fitted at 919 Hz
  alone, and says the damping at `FILQ` 13 is 0.0840. Fitted freely at each of
  six corners spanning 531–4491 Hz it came out **0.0834 ± 0.0015, −0.7%**.
- **Two runs agree on the corner itself**: 919 Hz from the `FILQ` sweep, where
  it was a nuisance parameter, and 930 Hz here, where it was the answer —
  1.2% apart, separate captures.
- **The law predicted three corners it was not fitted to.** Fitted on 62..92
  and then compared against `FILFRQ` 44, 50 and 56: −0.2%, +0.5%, +2.2%. Those
  three had been excluded as one-sided fits, one of them with 11 dB rms — the
  fit was poor and the answer was right anyway, so the exclusion cost nothing
  and the low end is now confirmed rather than assumed.

### What the range means now

44..92 is not where the machine stops. It is where **this sawtooth** has
harmonics on both sides of the corner: below 44 the corner drops under the
lowest note's fundamental and no harmonic sits beneath it; above 92 the source
runs out of harmonics above the corner. Both ends fail by becoming one-sided,
and both are limits of the source, not of the method — a brighter sample would
extend the top, a lower note the bottom.

This matters more than it looks. §20's stated bounds were also about the
source running out, and the honest form of that statement is what the two
fields' disagreement finally exposed: **a ruler that saturates against its
source is measuring the source.**

---

## §55 — `LFODEL` is a pure delay, and there is no fade-in (2026-08-12)

§31 left `LFODEL` provisional at r2 0.7633 and named its suspect: the detector
timed "until the vibrato exceeds 100 cents", which is the delay **plus** any
fade-in after it, two quantities measured as their sum.

The suspect was innocent. There is no fade-in.

```
    seconds = 0.06905 * LFODEL / (103.41 - LFODEL)      0..99   r2 0.999555
```

Two parameters, fourteen points, residuals of 0.017 s against a time
resolution of 0.008 s, sign-runs 7 of 13. The delay runs away toward a pole at
**103.4** — past the field's own top, so the machine never reaches it — which
makes the last steps far steeper than the first: 0.065 s at 50, 0.24 s at 80,
0.46 s at 90, **1.55 s at 99**.

That is the same shape as `FILQ` (§53), whose damping runs to zero at 15.84 of
a 15-step field. Two fields, same functional form, poles sited just past the
end of each range. **There is no evidence they are related** and nothing
measured connects a filter's damping to an LFO's delay; it is recorded because
the coincidence invites a unified story and none is warranted.

### Two estimators that fail differently

```
ONSET      first crossing of 5% of the final swing -- times the delay,
           insensitive to the ramp, sensitive to noise
INTERCEPT  a line fitted to the RISING edge, extrapolated back to zero --
           uses the ramp's geometry, insensitive to noise, wrong if the
           ramp is not straight
```

They agree to **-0.010 ± 0.016 s over thirteen settings**. Had there been a
fade-in the intercept would have led the onset by its duration. One estimator
alone would have produced a number and no way to know this; the disagreement
between two was the measurement.

### Three detector faults, each caught by a control rather than by inspection

The probe refused to report twice before it produced anything, and both
refusals were correct.

1. **1242 cents of "vibrato" with the LFO switched off.** That is an octave,
   and an octave is what an autocorrelation slip looks like: the tracker locks
   onto a harmonic and a max-minus-min swing reads the jump as modulation.
   Fixed by searching only 380–760 Hz around note 72's 523 Hz — which
   **excludes** 262 and 1046, so the octave is not in the tracker's reach —
   by dropping frames more than 700 cents from the capture's median, and by
   using a 95th-to-5th percentile spread instead of max minus min.
2. **325 cents of "vibrato" on a steady note, with every frame tracked and the
   median pitch dead right.** The capture ran past note-off, and once the
   release fell into the noise the tracker still returned a number — a number
   about the noise. Fixed by gating on level and analysing only the held part.
   The first fault masked the second: both would have been read as "the LFO is
   doing something".
3. **A 0.235 s floor that swallowed every delay below `LFODEL` 70.** The swing
   window has to hold about an LFO cycle, and that window *is* the detector's
   latency. Raising `LFORAT` to 99 shortened the cycle and cut the latency to
   0.187 s, and the level gate is what made the shorter pitch window
   affordable in the first place.

The latency is subtracted, and that is justified rather than assumed: fitting
the offset as a free parameter returns **0.188 s** against the 0.187 s measured
at `LFODEL` 0, where the true delay is zero by definition.

### A cross-check that came free

At `LFORAT` 50 the vibrato measured **571 cents** against the 585 that §35's
`LFODEP` law predicts for depth 30 — 2.4%, on a law fitted from different data
by a different detector. At `LFORAT` 99 the same setting reads 502 cents, and
that is the 30 ms pitch window averaging across a faster cycle, not the machine
disagreeing with itself. Only the slow-LFO figure is a valid check, and the
difference between them is a reminder that a detector tuned for one quantity
(onset time) is not automatically trustworthy for another (depth).

**With this, no law in `s3k/scales.py` is provisional.** The marking mechanism
stays and is still tested, against a scale injected for the purpose — the next
half-answered measurement should be marked, not rounded up into certainty.

---

## §56 — The tuning fields are 256x wider than this table said (2026-08-12)

Six two-byte fields — `PTUNO`, `KGTUNO` and the four per-zone `VTUNO*` — were
declared `0..50`. The document was quoted directly alongside, in the same
entry: *"-50.00 to +50.00 (fraction is binary)"*. **Those are semitones**, and
the raw unit is 1/256 of one, so the true range is `-12800..+12800`.

```
raw    256  ->   +99.8 cents        raw  -256  ->  -100.1 cents
raw    512  ->  +199.8              raw -1280  ->  -500.3
raw   1280  ->  +499.9              raw -5120  -> -2000.3
raw   2560  ->  +999.9
raw   5120  -> +1999.9
```

Measured by writing raw values and reading the **pitch** back, not just the
bytes. Every value round-tripped exactly — including 32767 and the negatives
as two's complement — so storage was never the question; the question was
whether the stored number means what the document says, and it does, to better
than 0.4 cents across ±20 semitones.

### How a dormant error became a live one

`0..50` sat in the table harmlessly for as long as nothing checked it. The
calibration swept 0..50 and fitted 100/256 cents per unit, which is correct and
which *is the evidence against the range*: 50 units is 19.53 cents, and no
sampler ships a tuning control that stops one fifth of a semitone from centre.
The number was there to be read and I did not read it.

§55's commit then tightened `encode_field` to range-check every numeric field.
That fixed a real bug and **created a worse one**: with the range at 0..50 the
guard refused every detune past 19.53 cents, so a keygroup could not be moved
by a semitone. The commit message for that change says, in its own words, that
an over-tight check would be the worse bug. It was, within the hour.

The general tell is cheap and is now a test: **a two-byte field whose declared
maximum fits in one byte** is either a display range transcribed as a value
range, or a modelling error. It caught all six, and it is the check that would
have caught them before the guard did.

### What is not established

Beyond ±5120 — twenty semitones — the scale is unverified. The pitch detector
tops out, and at +12800 the reading was nonsense rather than +5000 cents.
Whether the **sampler** transposes fifty semitones is a different question from
whether the **field** stores it, and only the second has been answered. The
range follows the document; the scale is measured over ±20 semitones and
extrapolated beyond.

### `TEMPER` is a different bug, left open

`TEMPER` is twelve bytes, one per semitone of the octave, each −50..+50 cents.
The table models it as a single twelve-byte integer, so writing it through
`set_parameter` encodes one number across all twelve: −5 becomes `FB FF FF …`,
which is C at −5 cents and **every other note at −1**. Not caused by the range
guard and not fixed here — it needs array support in the parameter model, which
is a design change rather than a patch. Recorded in TODO.md.

### Credit

The 256x disagreement was spotted by the sibling mpc2emu session, reading this
project's table after the range-check commit landed. Its proposed
discriminator — write raw 5120 and see whether it survives — is exactly the
measurement above, and its reasoning about which reading the existing
calibration supported was right before any hardware was involved.

---

## §57 — A corner tracker that is not a centroid, and envelope 3 measured with it (2026-08-12)

### The instrument, validated before it was pointed at anything

§54 established that a spectral centroid is worth little as a corner: 20–30%
high, wrong by a growing amount, and it folds. Every filter-envelope
measurement in this project used one.

§53 supplies a replacement. At `FILQ` 15 the filter grows a 25 dB peak **at**
the corner, visible in a single 40 ms window, so the corner can be read frame
by frame. Two refinements were needed and both came from failures:

1. **The peak is not the spectrum's maximum.** A sawtooth falls about
   6 dB/octave, so by 2.9 kHz the source is ~27 dB below its own fundamental
   and 25 dB of resonance cannot lift it above. Tracking the argmax returned
   **125 Hz** at the two highest corners — the fundamental, not the filter.
   Fixed by differencing each frame against the same note at `FILQ` 0, which
   removes the source's slope exactly.
2. **The difference of two noise floors is random.** Above a low corner both
   captures sit at the floor, and the argmax wandered: the right median with
   **630% frame scatter**, and 3788 Hz at `FILFRQ` 55. Fixed by looking only
   where the *reference* has signal, within 45 dB of its own peak.

Validated on static corners, where the answer is known from §54:

```
FILFRQ    §54 says   tracked   error   frame sd
    55        321       325    +1.3%     22.6%   <- comb quantum 20%, unusable
    62        527       525    -0.4%      0.6%
    70        930       925    -0.6%      0.2%
    78       1642      1650    +0.5%      1.0%
    86       2897      2875    -0.8%      0.1%
    92       4436      4525    +2.0%      0.9%
```

**Accuracy 0.9% mean, 2.0% worst; steadiness 0.1–1.0% on a corner that is not
moving; resolution set by the harmonic comb** — the peak snaps to the nearest
harmonic, so the quantum is one harmonic spacing and a low note is the right
note. Below about 500 Hz the quantum exceeds 13% and the tracker stops being
usable. Those are three different numbers and none substitutes for another.

Then I parked envelope 3's base corner at `FILFRQ` 55 — **321 Hz, below the
floor I had just measured** — and its control duly "moved" 2.03 octaves.
Validating an instrument and then operating it outside the validated range is
worth nothing.

**Replicated through the library code** (2026-08-12). The tracker was validated
as inline probe code and then moved into `probes/calibrate.py`; code that has
been moved is not code that has been checked, so the identical validation was
re-run through the library functions: **1.0% mean accuracy, 2.0% worst**,
against 0.9% and 2.0% for the original. The numbers above belong to the code
the project actually ships.

One difference is worth stating rather than smoothing over. At `FILFRQ` 55 the
first run scattered 22.6% frame-to-frame and the second 1.6% — **the
instability at the resolution floor is not reproducible**, while the 20%
quantum that causes it is structural. So the floor is a statement about
resolution, not about a repeatable amount of noise, and a single steady-looking
run there proves nothing.

### Envelope 3

```
ENV3R1   full 0..99 traverse = 0.001392 * exp(0.09669 * v) s    40..90  r2 0.99994
ENV3R3   full 0..99 traverse = 0.003815 * exp(0.09258 * v) s    10..85  r2 0.99980
ENV3L1   corner = 0.002685 * L1 * MODVFILT1  octaves            0..99   r2 0.99972
```

**The `ENV3R*` fields are rates, and higher is slower.** The table calls
`ENV3R1` "Attack rate of envelope 3"; a value of 40 gives 0.067 s for a full
sweep and 99 gives 20 s. Believing the name cost two sweeps: both pinned
`ENV3R1` 99 thinking it meant instant, so every capture spent its length still
rising and never reached the phase under test, and both produced flat readings
that looked like findings.

**A rate rather than a duration**, established by holding `ENV3R1` fixed and
sweeping the distance: the time tracked the distance 5.05x against 5.39x at
R1 70, and 4.95x against 5.11x at R1 80, with seconds-per-octave constant to
3%. A phase therefore takes `full_time * (distance / 99)`.

The two rate fields share a time base — exponents 4.3% apart — and differ by
2.7x in scale. `ENV3R2` and `ENV3R4` are **not** measured.

`ENV3L1` is linear in octaves, and the excursion is the product of level and
modulation depth: the slope per unit depth came out 0.00256, 0.00260 and
0.00269 at depths 50, 25 and 10 — three runs, 5% spread.

### The ceiling that made a perfect fit out of an artefact

At `MODVFILT1` 50 the corner saturated at 6650 Hz. That is **the top of the
filter's own range** — §54 puts `FILFRQ` 99 at 7291 Hz — not a limit of the
tracker, and full level at that depth asks for 12.7 octaves.

The timings from the clipped runs fitted an exponential at **r2 0.99999**. They
were also wrong. The tell was comparing two drive levels: depth 50 and depth 25
disagreed by a factor approaching **2.0**, which is exactly their depth ratio
and precisely what a linear ramp read through a ceiling produces — the time to
cross a fixed fraction of a *truncated* span scales inversely with drive. A law
that changes when the drive changes is not a law.

**Two runs at different drive levels cost one extra run and expose a whole
class of ceiling artefact.** An excellent r² does not: it measured how
consistently the ceiling clipped.

---

## §58 — Envelope 2 re-measured, and `ATTAK2`'s depth-dependence was mine (2026-08-12)

§28 measured all four envelope-2 laws through a spectral centroid. §54 showed
what that ruler is worth. Three of the four are re-measured here with the
resonance tracker; the fourth is marked rather than left looking equally solid.

```
ATTAK2   full 0..99 traverse = 0.001363 * exp(0.09703 * v) s    40..85  r2 0.99981
DECAY2   full 0..99 traverse = 0.002464 * exp(0.09844 * v) s    40..80  r2 0.99997
SUSTN2   octaves = 0.002075 * SUSTN2 * MODVFILT1                0..99   r2 0.99978
RELSE2   NOT re-measured -- now provisional
```

Envelope 2 is assignable-matrix **source 10** and reaches the filter only when
routed, the fact §48 had to learn the hard way about envelope 3.

### `ATTAK2` was never depth-dependent

§28 left it provisional over a **threefold** disagreement — 1.169 s at
`MODVFILT1` 18 against 0.38 s at 25 — recorded as depth-dependence, with the
domain it ramps in called unsettled.

Swept here at `MODVFILT1` 5 and 10, chosen so neither clips the 2.84-octave
ceiling:

```
ATTAK2      40     50     55     60     65     70     75     80     85
depth 5    0.200  0.290  0.410  0.560  0.810  1.230  1.910  3.020  4.810
depth 10   0.200  0.290  0.400  0.560  0.810  1.250  1.920  3.010  4.770
span 5     0.93   1.04   1.08   1.04   1.04   1.04   1.04   1.04   1.04
span 10    1.95   2.03   2.02   2.06   2.07   2.07   2.07   2.06   2.06
```

**Times agree to 1.00 ± 0.01 while the spans differ by exactly the drive
ratio.** The depth-dependence was the ceiling of §57 — the corner saturating
at the top of the filter's own range — and the provisional mark comes off. A
threefold error, sitting in the notes for a day as a property of the machine,
was a property of the ruler.

### One time base across two envelopes

`ATTAK2` and `ENV3R1` are the same measurement over the same distance, and
they give the same numbers:

```
value        40     50     60     70     80
ATTAK2     0.200  0.290  0.560  1.250  3.010 s
ENV3R1     0.200  0.300  0.550  1.250  3.010 s
```

Coefficients 0.001363 against 0.001392, exponents 0.35% apart. Two envelopes,
one time base.

**This unsettles §28's other reading.** It recorded `ATTAK2` as a DURATION
while `DECAY2` and `RELSE2` are rates. But an attack always travels
zero-to-full, so a fixed distance **cannot distinguish a rate from a
duration** — and `ENV3R1`, the same law, is a rate, proven by varying its
target level (§57). Envelope 2 has its own attack target in `ENV2L1` and the
test has not been run. Recorded as unsettled rather than quietly flipped.

`DECAY2`'s rate finding does survive: it came from varying the span by 72% and
watching the rate hold to 1.9%, a **relative** comparison, which a biased ruler
distorts far less than an absolute one. Which parts of a superseded measurement
survive is worth deciding one at a time.

### The size of the centroid's error, measured

`SUSTN2` gives a direct check, because §28 published an absolute coefficient:

```
§28 (centroid)   0.024645 FILFRQ-units per (SUSTN2 x MODVFILT1) = 0.002524 octaves
§58 (resonance)                                                   0.002075 octaves
```

**22% high, in the same direction as everything else that ruler measured.**
§28 also stopped at `SUSTN2` 70 because the corner passed the source bandwidth;
the tracker has no such limit and the law runs the full range, linear in
octaves throughout.

Envelope 3 scales differently — 0.002685 octaves per unit of level x depth
against envelope 2's 0.002075. Measured, not explained.

### `RELSE2` is marked, and the marking is the point

It is the only envelope-2 law still resting on the centroid, because release
happens after note-off and needs a capture window this run did not have.
Its three siblings were corrected by up to 22%.

Leaving it unmarked would have made it the **most trustworthy-looking law in
the group and the least checked** — its r² is 0.99977 and its neighbours now
carry visible correction history. A law does not become more reliable by
having been left alone.

---

## §59 — `RELSE2` measured, and the family turns out to be one law (2026-08-12)

```
RELSE2   full 0..99 traverse = 0.001344 * exp(0.09692 * v) s   40..80  r2 0.999975
```

The last envelope-2 law resting on the spectral centroid, and the last entry on
the provisional list. §58 put it there rather than leave it alone; §59 takes it
off. **The mark named a debt and the debt was paid** — which is the only thing
that makes marking better than silence.

### One time base, with the decays at half rate

Measuring the third stage the same way as the first two made a structure
visible that none of them showed alone:

```
                 full 0..99 traverse            at value 70
    ATTAK2    0.001363 * exp(0.09703 v)          1.25 s
    RELSE2    0.001344 * exp(0.09692 v)          1.22 s
    ENV3R1    0.001392 * exp(0.09669 v)          1.25 s
    ----------------------------------------------------
    DECAY2    0.002464 * exp(0.09844 v)          2.42 s
    ENV3R3    0.003815 * exp(0.09258 v)          2.61 s
```

**Attack and release are one law across both envelopes** — coefficients within
3.6%, exponents within 0.35%, predictions 2.2% apart at value 70. The decays
run at about **half** that rate: DECAY2 at 1.9–2.0x and ENV3R3 at 2.0–2.3x.

So the family is one time base with the decay stages halved, not five
calibrations. Worth stating carefully: `DECAY2`'s exponent is 1.5% from the
attack's and `ENV3R3`'s is 4.6% away, so "exactly half" is supported for
`DECAY2` and only approximate for `ENV3R3`.

This was not found by assuming a family and fitting one curve to all of it. It
was found by measuring five fields the same way and looking at the answers
side by side — the same move that has now produced three findings in a row.

### Two windows, one of them changed

The first run of this fitted at **r² 0.9225** and the exponent came out 27%
from `DECAY2`'s.

Release begins at note-off, and the rig records a 2.0 s tail while a slow
filter release runs past 4 s — so `calibrate.TAIL` was raised to 10 s. But the
ANALYSIS window was a separate hardcoded `note-off + 2.0`, and it was not
raised. Every row duly reported exactly **200 frames after note-off**, which is
2.0 s at the 10 ms hop: an identical count across eight settings, which is the
same tell as §55's identical −0.150 s and §53's smoothing width that changed
nothing.

With both windows widened: r² **0.999975**, spans 2.04–2.06 octaves throughout,
and the exponent 1.5% from `DECAY2`'s instead of 27%.

**Changing a capture parameter and an analysis parameter are two edits.** I
made one and believed I had made both, and the truncated fit was good enough
to look like a result rather than an artefact — 0.92 is poor by this project's
standards but it is not obviously broken.

### What the amplitude release still needs

`RELSE1` is a rate in dB/s and `RELSE2` is now a time in seconds. They are less
comparable than before, not more, and any ratio between them is meaningless.
`RELSE1` has not been re-examined and does not depend on the filter ruler, so
nothing here casts doubt on it.

---

## §60 — The structure predicted the two fields it had never seen (2026-08-12)

§59 assembled a claim from five measured fields: attack and release share one
rate across both envelopes, the falling stages run at about half of it. Two
fields of the family had never been measured, which makes them a test.

**The predictions were written into the probe before it took a reading**, so
they could not be adjusted afterwards:

```
                    predicted at value 70      measured      miss
    ENV3R4               1.20 s                 1.21 s        1%
    ENV3R2               2.22 s                 2.38 s        7%
```

```
ENV3R2   full 0..99 traverse = 0.002565 * exp(0.09762 * v) s   40..80  r2 0.99983
ENV3R4   full 0..99 traverse = 0.001515 * exp(0.09549 * v) s   40..80  r2 0.99997
```

The whole family, at value 70:

```
    attack / release    ATTAK2  RELSE2  ENV3R1  ENV3R4     1.19 .. 1.21 s
    falling             DECAY2  ENV3R2  ENV3R3             2.38 .. 2.49 s
```

**The grouping is by stage type, not by envelope.** Seven fields, two
envelopes, two rates, and the two newest were predicted before measurement.

### Both detector faults announced themselves

Neither was found by inspection, and neither would have been visible in the
fitted numbers alone.

**Frozen.** `ENV3R2`'s fall time read **0.020 s at every setting** — the first
frame — while the spans moved 2.01 to 2.64. The minimum was taken over the
whole track, and with an instant attack that minimum is the base corner in the
opening frame, so "90% below the peak" was satisfied before the fall started.
This is the third frozen series in this calibration and the first one caught by
`verify_varies` rather than by hand.

**Collapsing span.** With the estimator fixed, the fit came back r² 0.65 and
the spans fell 2.10 → 1.40 → 0.56 across the slow settings. Phase 2 happens
DURING the note, and the note was 1.5 s — inherited from the release probe,
where 1.5 s was correct. **A collapsing span is truncation showing**, the same
tell as §57's clipped envelope-3 timings, and it is visible in the span column
without looking at the fit at all.

### One reading excluded, and named

At `ENV3R2` 70 the run returned 0.010 s between neighbours at 1.04 and 2.80,
with the largest span in the set. It is excluded as a single bad take and said
so in the table's `bounds`, rather than dropped quietly — an outlier removed
without a note is indistinguishable from one that was never measured.

Its exclusion does not carry the result: the remaining seven points fit at
r² 0.99983 and the prediction test used value 70 only through the fitted law,
not through that reading.

---

## §61 — `K_DAR2` measured, predicted from `K_DAR1` (2026-08-12)

```
time = base * exp(+0.0014603 * K_DAR2 * (note - 64))   -20..+20   r2 0.99584
```

§48 measured `K_DAR1` and observed in its note that the `K_` prefix means KEY,
making `K_DAR2` and `K_DAR3` its likely companions. That was a guess about
naming. This tests it.

**Predicted before the run**, from `K_DAR1`'s coefficient applied to a time
rather than a rate:

```
                  predicted           measured      miss
    K_DAR2   0    flat across notes   1.015x        exact control
    K_DAR2 +20    time(24)/time(48) = 0.48   0.50    4%
    K_DAR2 -20    time(24)/time(48) = 2.08   2.16    4%
```

The fitted coefficient is 0.0014603 against `K_DAR1`'s 0.0015286 — **4.5%
apart**. Both envelopes scale with the key by the same law. The sign differs
because `K_DAR1` was fitted to a rate and this to a time; a converter copying
the magnitude without the sign gets a filter that brightens where it should
darken, so the test pins both.

The control is exact, as §48's was: at `K_DAR2` 0 the decay reads
**0.670..0.680 s across 24 semitones**, a spread of 1.015x. A flat control row
is worth more than a good fit — it says the rig does not tilt with the note,
and every sloped row is measured against it rather than against an assumption.

### What this run cannot see

**The pivot is taken on trust.** Every note here is below 64, so the run fixes
the slope and never sees where the depths meet. §48 could show the pivot
directly because its notes bracketed it — all five of its depths read 25.3 dB/s
at note 64. A pivot taken on trust is not a pivot measured, and it is recorded
that way in the entry.

The reason is the instrument, not the machine. **The corner tracker's
resolution is the harmonic spacing** — 3.5% of the corner at note 24, 14% at
48, 56% at 72 — so anything read through the filter is confined to the bottom
two octaves. §48 escaped that by measuring the amplitude envelope in dB, which
has no comb. This is the first time the tracker's resolution limit has cost a
finding rather than merely bounding one, and it is worth knowing that the
limit is directional: it constrains where in the KEYBOARD the filter can be
measured, not just how precisely.

The depth range is narrow for an unrelated reason: at ±50 these notes span a
400-fold range of decay times, which no single `DECAY2` setting holds inside a
measurable window.

---

## §62 — Key scaling reads the note, not the pitch — and the pivot is measured (2026-08-12)

§61 recorded `K_DAR2`'s note-64 pivot as taken on trust, and said closing it
needed a different detector. That was wrong in a useful way: **the limit is the
source, not the detector.** The sibling mpc2emu made the point — a comb spaced
`f0` apart cannot locate a peak closer than about `f0/2`, and `f0` doubles every
octave, so the resolution degrades exactly as you climb. White noise has no
comb and its resolution is the FFT bin width at every pitch.

That fix is right and is not available here: this editor speaks the header
protocol, and loading a sample needs the MIDI Sample Dump Standard. Only four
synthetic waveforms are in memory and all of them are combs.

### The way round, and the fact it turned on

**Key scaling reads the MIDI note number, not the sounding pitch.**

```
    K_DAR2        note 48, KGTUNO 0      note 68, KGTUNO -5120      ratio
         0            0.670 s                  0.670 s              1.00
       +20            0.410 s                  0.800 s              1.95
```

Two conditions that sound identical — both at 130.8 Hz — and differ only in
note number. At depth 0 they agree exactly, so detuning does not touch the
decay by any other route; at +20 they differ by 1.95, against 1.79 predicted
for the note hypothesis and 1.00 for the pitch hypothesis.

So `KGTUNO` can hold the SOUND at one low pitch, where the comb is fine, while
the NOTE sweeps across the pivot. The instrument's limit is on the *sounding
pitch*, and the quantity of interest is the *note* — they are separable, and
nothing about the tracker had to change.

### The pivot

```
  K_DAR2    note 48    note 53    note 58    note 63    note 68
     -20      1.060      0.970      0.800      0.730      0.600
       0      0.660      0.670      0.660      0.670      0.670
      20      0.420      0.500      0.600      0.670      0.800
```

Every condition sounds at note 48. The `K_DAR2` 0 row is flat at 0.660..0.670
across the whole sweep — the same exact control §48 had. The sloped rows cross
it at **note 65.0 and 62.3**, mean **63.6**, against 64 predicted.

So §43's pivot rule now rests on three key-driven fields — `K_FREQ`, `K_DAR1`
and `K_DAR2` — and on `K_DAR2` it is measured rather than inherited from a
shared prefix. An inherited pivot and a measured one look identical in the
coefficient, which is why the entry says which it is.

### What this changes about instrument limits generally

§61 concluded that the tracker's resolution limit was directional and cost a
whole class of question. The limit is real and the numbers were right. The
conclusion was not: the question was answerable, by separating the variable the
instrument constrains (sounding pitch) from the variable under study (note
number) and holding the first fixed.

**Before recording a limit as blocking, check whether the constrained variable
and the studied variable are the same one.** Here they were merely correlated
by default, and one field broke the correlation.

---

## §63 — Envelope 3's scaling set: all three work, and two nearly did not (2026-08-12)

```
K_DAR3   phase 3   coefficient 0.0015617 per semitone per unit   pivot 63.5
         release   coefficient 0.0011903                          pivot 64.0
         phase 2   coefficient 0.0000451 -- no effect
V_ENV3   velocity scales the AMOUNT, bipolar: span 0.515 vs 2.242 octaves at +50
V_ATT3   velocity scales the ATTACK, bipolar: rise 0.160 s vs 5.920 s at +50
```

`K_DAR3`'s phase-3 coefficient is within 2% of `K_DAR1`'s and 7% of `K_DAR2`'s,
so **all three envelopes scale with the key by one law**, and its pivots land
at 63.5 and 64.0 against the family's 64.

### Two verdicts that were nearly recorded and would have been wrong

The first run of this produced one finding and two failures, and both failures
were the same shape: **the detector was aimed at a phase that could not express
the field.** The route was live and the detector worked in each case.

**`V_ATT3` read 0.000 s at every velocity and every depth.** `ENV3R1` was 0 —
an instant attack — so there was no rise for velocity to scale. The only thing
that stopped "`V_ATT3` does nothing" being written down was that its velocity
control came back NaN rather than a plausible number. Given a real 1.25 s rise
it swings **37-fold**.

**`K_DAR3` read 0.660..0.670 s at every note and depth**, a spread of 1.02x
identical to its own control — a clean, well-behaved, entirely convincing null.
What was timed was **phase 2**, while the field's description reads
*"dependence of envelope 3 release and DECAY rate on key"*. Retested against
the two stages it actually names, it moves both by a factor of 2.6.

The phase-2 reading was **not wrong**. It is a true negative, and it is now
recorded as one: `K_DAR3` genuinely does not touch phase 2, at a coefficient
30x smaller than phase 3's and inside its own control. What would have been
wrong is the sentence it invited — *`K_DAR3` is inert*.

### The rule this adds to §48

§48's lesson was: state what would have to be true for the field to reach the
detector at all, and check it. That covered routing, drive and timing.

This adds the stage. **A negative needs the right stage as well as the right
route** — and the failure is invisible from inside the run, because everything
reports healthy: the route is live, the control is flat, the readings are
distinct, and the null is clean. The only signal was that a field whose own
description names two stages had been tested on a third.

**Read the field's description before believing a null.** It is the cheapest
check available and it is the one that failed here.

### On the velocity control

`V_ATT3`'s depth-0 row gives 1.110 s at velocity 20 and 1.250 s at 120 — a
ratio of 0.89 rather than 1.00. Within the band the probe would accept, but
not perfect, so **an effect smaller than about 11% could not be claimed from
this run**. The measured effects are 30-fold and 42-fold, so nothing here is
at risk; it is recorded because the next field measured this way might move by
15%.

---

## §64 — `V_REL3` and `O_REL3`: the rig could not have measured one of them (2026-08-12)

```
V_REL3   note-ON  velocity scales the release, bipolar, more than tenfold
O_REL3   note-OFF velocity scales the release, bipolar: 1.440 s at 20, 0.150 s at 120
```

Both respond. **All four of envelope 3's velocity scalers work** — `V_ENV3`,
`V_ATT3`, `V_REL3`, `O_REL3` — which is the counter-case to §47, where five of
six envelope scaling fields were recorded inert.

### The rig sent note-off velocity 0, unconditionally

`O_REL3` is *"dependence of envelope 3 release rate on note-off velocity"*.
The rig's note-off was `[0x80 | channel, note, 0]` and had been since it was
written.

So a measurement of `O_REL3` before today would have varied the depth, seen
nothing, and produced a flat null with a live route, a working detector and a
clean control — the exact shape of §63, with the modified quantity never
allowed to happen.

**It was caught by reading the field's description before the run**, not by any
check. §63's rule was written four hours earlier and this is the first time it
fired prospectively rather than as a post-mortem. `Rig.play_and_record` now
takes a `release_velocity`, range-checked, with the synthetic rig accepting it
too so the path is exercisable without hardware.

### The depth-0 rows do the orthogonality work

Each field was measured with the other at depth 0, so cross-talk between the
two fields is excluded by construction. What remained was whether either
*velocity* reaches the release by some other path — and the controls answer it:
note-on velocity alone gives 0.290 against 0.280, note-off velocity alone
0.290 against 0.290. Neither moves the release without a depth to act through.

That is worth noting because a separate cross-control was attempted and was
**mis-specified**: it re-ran the same sweep with the other velocity pinned at a
different fixed value, which tests robustness rather than orthogonality. The
depth-0 rows already had it covered, so nothing was lost, but the intended
check was not the check that ran.

### An effect too large to measure is still a result

At `V_REL3` ±50 one end of the sweep ran past an 8 s capture, so the ratio is
`NaN` and the size of the effect is **bounded below rather than measured**. The
first version of the summary crashed on that; it now reports it as what it is.

A reading that leaves the window is evidence the field works. Dropping it would
lose that, and treating it as a failed measurement would understate the field.
`V_REL3` is recorded as "more than tenfold, exact factor not measured".

### For a converter

`O_REL3` is reachable but **rarely driven** — few controllers send a note-off
velocity at all. A converter must not read a default of 0 as evidence the field
is inert: 0 is one end of its range, and it is the end that makes the release
longest at positive depth.

---

## §65 — Only slot 1 modulates the filter, and the "!" sources are not inverted (2026-08-12)

```
source through slot 1, MODVFILT1 10, corner in octaves above base
    modwheel   (1)    -0.01 -> +2.08     delta +2.09
    !modwheel (11)    -0.01 -> +2.08     delta +2.09
    bend       (2)    -0.01 -> +2.08     delta +2.09
    !bend     (12)    -0.01 -> +2.08     delta +2.09
    none       (0)    -0.01 -> -0.01     delta +0.00

envelope 3 (source 14) through each slot
    slot 1     1.68 .. 2.08 octaves   (§63, §64)
    slot 2     0.00 octaves           MODSFILT2 reads 14, MODVFILT2 reads 10
    slot 3     0.01 octaves           MODSFILT3 reads 14, MODVFILT3 reads 10
```

**`MODSFILT2` and `MODSFILT3` do not modulate the filter.** The writes were
read back and confirmed, the same source and depth move the corner two octaves
through slot 1 in the same session, and the second and third slots move it by
one part in two hundred. This is a negative with the route proved live
elsewhere, the field values verified, and the stage correct — the three things
§48, §63 and §64 each had to learn.

**Bend works as a modulation source**, which was untested before.

**The "!" variants are not inverted.** `!modwheel` and `!bend` behave like
`modwheel` and `bend`: same sign, same magnitude. Whatever the prefix means in
Akai's table, it is not "invert the source" on this machine.

### The resolution caveat, which limits the last claim

The four deltas agree to five decimal places — 2.08517 throughout — and
`verify_varies` flags them as frozen. That is expected here rather than
alarming: the corner tracker quantises to the nearest harmonic, so at note 48
adjacent readings are 0.19 octaves apart and any difference smaller than that
lands in the same bin.

So the claim that survives is about **sign and rough magnitude**: an inverted
source would read near zero where these read +2.08, and that is far larger than
the quantum. Whether the "!" variants differ from their twins in some smaller
way this detector cannot see is **open**, and the identical values are not
evidence that they are identical.

### What was not tested, and why

`external` (4) and `!external` (13) are **not measured**. The source documents
do not say what "external" is — a rear-panel control input, or a MIDI
controller assigned elsewhere — and without knowing the stimulus a null would
be worthless. That is the `O_REL3` situation from §64: the rig could not vary
the quantity the field keys on, and the failure would have been invisible.

Recorded as needing the source identified first. **Not as inert.**

### The ceiling, again

The first run used `MODVFILT1` 40 and read +2.98 octaves for every source
against a ceiling of +2.84 (§57) — all four saturated, all four identical, and
the identity meant nothing. Rerun at depth 10 the identity persisted, which is
what makes it reportable. Two runs at different drive levels, again.

---

## §66 — `TEMPER` was twelve bytes modelled as one number (2026-08-12)

A data-corruption bug in the editor itself rather than a calibration finding,
open since it was noticed during §56's range audit.

`TEMPER` is program offset 44: **twelve independent signed bytes**, one per
semitone of the octave starting at C, each a detune in cents. The table
modelled it as a single twelve-byte integer, so `encode_field` wrote one number
across the whole span:

```
    set TEMPER -5   ->   FB FF FF FF FF FF FF FF FF FF FF FF
                         C at -5 cents, and EVERY OTHER NOTE AT -1
```

Reading it back gave one meaningless large integer. A user or converter setting
a single detune silently retuned the whole octave.

### The fix, and why it is a model change rather than a patch

`Parameter` gains `elements`. Where it is greater than 1 the span is
`elements` values of `size // elements` bytes, `minimum`/`maximum` apply to
each ELEMENT, and encode/decode work in sequences.

**A scalar passed to an array field is refused, not broadcast.** That is the
whole point: broadcasting is what made this a silent corruption rather than an
error, and the refusal immediately caught two more places doing it — the demo
machine's header seeding, and three whole-table tests that assumed every field
was scalar.

```
    encode [-5, 0 x11]   ->   FB 00 00 00 00 00 00 00 00 00 00 00
    decode                ->   (-5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    encode -5             ->   TypeError: TEMPER holds 12 independent values
    encode [0] * 11       ->   ValueError: expected 12 values, got 11
    encode [99] + [0]*11  ->   ValueError: 99 is outside -50..50
```

### It is now editable rather than merely safe

Refusing the write makes the field safe and leaves it **unreachable**, which is
not a fix. Both entry points take one value per element, comma-separated:

```
    s3kcli set TEMPER 0,-14,0,-2,16,0,-12,2,-10,0,-6,14 0
    TEMPER = C# -14, D# -2, E +16, F# -12, G +2, G# -10, A# -6, B +14 cents
```

and `describe_value` names the notes, because twelve bare numbers are not a
temperament anyone can read. An untouched program says **"equal temperament"**
in two words rather than twelve zeros.

### The check that would have found it

A test now asserts `size % elements == 0` for every parameter, which is
trivially true today but pins the shape. The thing that actually found the bug
was §56's audit for **two-byte fields declaring a one-byte range** — `TEMPER`
was the one entry that tripped it and was *not* a display-range error, so it
had to be exempted from that test, and the exemption is what made me look at
why it was different.

**An exemption is a place where something is known to be unusual.** Writing one
is worth treating as a prompt rather than a nuisance.

---

## §67 — Envelope 2 is four stages too, and `ATTAK2` is a rate (2026-08-12)

Two attempts at `ATTAK2`'s rate-versus-duration question failed the same way:
the DISTANCE never varied, so the readings were clean and meant nothing. Both
were built on a guess about envelope 2's shape. The shape was free to find.

Writing each of the eight fields and reading back all eight shows **no
aliasing** — only the field written moves — and the factory values line up in
pairs:

```
    ATTAK2  = 0     ENV2L1 = 99        rate 1 / level 1
    ENV2R2  = 50    ENV2L2 = 99        rate 2 / level 2
    DECAY2  = 50    SUSTN2 = 99        rate 3 / level 3
    RELSE2  = 45    ENV2L4 =  0        rate 4 / level 4
```

**Envelope 2 is a four-stage rate/level envelope, exactly like envelope 3.**
The ADSR-flavoured names cover R1, R3, L3 and R4; the four so-called extension
fields are the missing L1, R2, L2 and L4 of the same structure. §48 found
envelope 3's architecture by reading the table; envelope 2's was hidden behind
familiar names, which is worse than an unfamiliar one.

That is exactly why both earlier attempts failed. Sweeping `ENV2L1` while
phase 2 ran to `ENV2L2` 99 meant the attack's target was overridden the moment
it was reached — the level went to L1 and straight back to full, so the
excursion stayed 2.07 octaves whatever L1 said.

### `ATTAK2` is a rate

With `ENV2L1`, `ENV2L2` and `SUSTN2` swept together, so the envelope rises to
one level and stays:

```
    ATTAK2 65:  time spread 4.47x, distance spread 4.76x, s/oct 0.316..0.345
    ATTAK2 75:  time spread 4.56x, distance spread 4.76x, s/oct 0.861..0.898
```

Time tracks distance at both fixed settings, and seconds-per-octave holds to
9% and 4%. **Both settings agree**, which is what the two failed attempts did
not do — their contradiction was the only signal that the experiment rather
than the machine was wrong.

So §28's "duration" reading is refuted, and every stage of both envelopes is a
rate: a phase takes `full_time * (distance / 99)`.

### The pattern in the three attempts

Attempt 1 and attempt 2 were *measurements*. Attempt 3 was preceded by a
two-minute read-back test that cost no audio and settled the architecture.

The distinguishing feature is not effort. It is that the first two asked the
machine a question about a parameter, and the third asked it a question about
the SHAPE the parameter lives in. **When a measurement fails twice for reasons
that look like bad luck, the next thing to test is the assumed structure, not
the parameter.**

---

## §68 — The disk is readable over MIDI. It is not loadable (2026-08-12)

`RVOLLIST` (`0x35`) answers with the volumes on the attached SCSI disk, and
the reply's shape was not documented anywhere this project has:

```
16-byte records: 12 bytes of name in the Akai charset, then
                 a type byte (3 for every volume seen) and three zero bytes
index          : the volume to start at -- a sliding window, not one item
count          : how many bytes of records to return; 256 gives 16 records
```

A 100-volume disk reads in **7 round trips, 1.2 s**. On the machine here that
is a ZuluSCSI carrying 100 volumes, and all 100 come back correctly decoded.

### The end marker is the type byte, and this matters

Past the last volume the record is all zeroes. **An all-zero name does not
decode to blank** — index 0 of the Akai charset is the character `0`, so it
reads as `000000000000`.

So a reader that stops on a falsy name never stops, and a reader that stops on
the literal string `000000000000` truncates a disk that happens to contain a
volume called that. The type byte is the only sound marker. The test fixture
includes a volume genuinely named `000000000000` so the wrong rule fails.

### What is not there

**There is no load operation.** The S1000 layer ends at `SETEX`/`REPLY`/
`CASPACK`, the S3000 extensions `0x27`–`0x38` are all reads, and the
S3000XL layer adds only multi data. `RVOLLIST` and `RHDDIR` are both marked
reply-only, and neither has a counterpart that acts on a volume.

**The manual settles this, in Akai's own words** (S2800/S3000/S3200 spec,
overview section):

> *"There are no functions within MIDI system exclusive to provide direct
> access to and from disk files. Directories and files can be loaded into the
> S3000 and the data then accessed. However, if external parties wish to get
> data directly from disk, it is available via SCSI."*

So the 23 unassigned opcodes are not hiding a load command — the manufacturer
states the functionality does not exist. This was recorded here as "could
exist and be untranscribed" for an hour before the document was read, which
is the wrong order and is left visible for that reason.

Anything wanting to move data off the disk must speak SCSI, not MIDI. That is
what the ZuluSCSI already is.

### `RHDDIR` was probed wrongly, and the manual says how

The first probe used a 64-byte read with the second byte at 0, and got
`ff`-filled records. The spec gives the real shape:

```
nn,nn  Directory entry (0-509) / item number
ss     Selector: 0=volume data, 1=program, 2=sample, 3=cue list,
                 4=take list, 5=effects file, 6=drum file
nn,nn  Number of bytes of data (24)
```

Entries are **24 bytes**, not 64, and the selector chooses the KIND of item.

Re-probed correctly, every entry was still empty — until the front panel's
LOAD source was changed from floppy to the hard disk. Then the directory
filled: 63 entries, six programs followed by their samples. **The manual's
sentence is exact**: the directory is of the volume the machine has *loaded*,
not of the disk. Loading a different volume replaced the listing entirely (63
entries starting `TECHNOTRONIC` became 98 starting `GOOD+BAD`), so it tracks
the load rather than caching.

The selector is a starting point rather than a filter: selector 1 returns the
programs and continues through the samples, selector 2 starts at the samples.

### Two ways to read this wrong, both of which I did

**The end of the list is not marked by zeroes.** Past the last entry the
device keeps answering: first with records whose four-byte extension field is
no longer four spaces, then by repeating an earlier record. A stop condition
of "all bytes zero" never fires. The first implementation returned **188
entries for a 63-entry directory** — two thirds junk that decoded as plausible
names, which is exactly the kind of output that gets believed.

Two markers are needed together: the extension field ceasing to be `20 20 20
20`, and a record repeating one already seen. The echo is not always of entry
0 — here entry 63 came back byte-identical to entry **13**, including the eight
bytes that look like a location, so two real files cannot account for it.

**`count` does not page.** `RVOLLIST` pages beautifully — 16 records a
request — so the same was assumed here. It is not the same: asking for 48
bytes returns the first program and then the first SAMPLE, not the second
program. The extra bytes are something else, and a paged reader produces a
plausible, differently-wrong list. One request per entry is the only correct
shape, and costs 1.6 s for 63 entries.

### The SCSI ID is not reachable, and here is the state of that

Not in any transcribed table: the parameter table covers program, keygroup,
sample and multi, with no system or global region. The plausible home is
miscellaneous data, and **the miscellaneous data-index table is missing from
the source** (an open TODO since §5).

**The addressing is documented; the meanings are not.** `RMISCDATA` takes:

```
dd,dd  Data Index
bb     Data bank: 1=byte, 2=word, 3=dword, 4=smpte, 5=signed smpte,
                  6=name, 7=16byteflag
nn,nn  Number of bytes (1/2/4/5/6/12/16)
```

The first probe swept `bb` while holding `dd` at 0 — that reads item 0 of
every bank, which is the wrong axis. Sweeping the INDEX within the byte bank
gives 48 readable items, and the name bank returns `DRUM INPUTS`,
`NEW NAME`, `NO VOL. READ`, `PULSE`, `TAKE 1` — so the mechanism works and
only the index-to-meaning table is missing.

**A candidate for the SCSI ID, not yet a finding.** The Japanese operator's
manual shows the settings page as `SCSI drive ID: 5` and `local SCSI ID: 6`.
The byte bank reads `byte[11] = 5`, `byte[12] = 6`, `byte[13] = 6`.

That is a value match and nothing more. Confirming it needs the differential:
change the ID on the panel and see which index moves. A baseline of 80 items
(48 byte, 16 word) is captured for exactly that. **Value-matching has been
wrong in this project before** — §61's pivot looked settled from a
coefficient and needed a measurement — so it is written down as a candidate.

---

## §69 — The directory record decoded: what a volume costs in RAM (2026-08-12)

A 24-byte harddisk-directory record, decoded and checked against the machine's
own numbers:

```
[0:12]    name, in the device charset
[12:16]   extension -- four spaces on a real entry; anything else means the
          list has ended (§68)
[16]      item type: 0x70 program, 0x73 sample
[17:20]   file size on disk, BYTES, little-endian, THREE bytes
[20:22]   location, increasing down the listing
[22:24]   0x1e 0x09 on every entry seen
```

### The number a bank builder needs

**A sample file is its audio at two bytes per word, plus exactly 150 bytes of
header.** Measured, not derived: across all 60 samples whose files could be
compared against their loaded `SLNGTH`, the difference was 150 bytes with no
exceptions and no other value appearing.

```
    audio_words = (size_bytes - 150) / 2
```

So the memory a volume will need is `sum(audio_words)` over its sample
entries. Programs contribute nothing — their size is header data.

### Checked against something it was not fitted to

Predicting the loaded memory from the directory records alone, and comparing
against the machine's own `SLNGTH` sum for the 60 samples that did load:

```
    predicted from directory records   16,424,982 words
    actual, summed from SLNGTH         16,424,982 words
    difference                                  0
```

Exact. The 150 was fitted on the same samples, so the strong part of this is
the **size field itself**: reading bytes 17–20 as a three-byte little-endian
size reproduces every length independently.

### The three-byte size is a real ceiling

The field is three bytes, not four — reading it as three is what reproduces
the lengths. That caps a single file at **16,777,215 bytes**, about 8.39
million sample words, or 3.2 minutes of mono audio at 44.1 kHz. A volume
exceeds a machine by having many files, not one enormous one.

### The case that prompted this

A CD-ROM volume reported *"insufficient waveform memory!"* on a 32 MB
S3000XL, and the machine was not at fault:

```
    volume needs    30,768,270 words = 58.69 MB   (10 programs, 88 samples)
    machine has     16,777,216 words = 32.00 MB
    loaded          16,424,982 words = 31.33 MB   53% of the volume
    over capacity   13,991,054 words = 26.69 MB
```

The volume is **183% of what the largest machine of this type can hold**. Ten
programs loaded — they are small — and 60 of 88 samples, so any keygroup
pointing at one of the missing 28 plays silence. That is worth knowing before
building a bank rather than after loading one: the shortfall is computable
from the directory in about 1.6 seconds, without loading anything.

---

## §70 — The load source, mapped: the partition is remotely settable (2026-08-12)

The front panel's LOAD page lives in the miscellaneous byte bank, and the
whole of a disk can be enumerated over MIDI without touching the machine.
Every index below was found by changing the setting on the panel and seeing
which byte moved, or by writing a distinctive value and reading the panel.

```
byte[0]    device type -- floppy / hard / flash. The volume list's
           "BOOT SYSTEM#" and "FLASH VOLnn" belong to the flash device.
byte[2]    partition, 0-based (0 = A). WRITABLE, and the machine re-reads.
byte[4]    a hold flag. 1 suppresses the re-read. See below.
byte[11]   SCSI drive ID    -- writable, confirmed on the panel
byte[12]   local SCSI ID    -- confirmed on the panel
byte[49]   volume, 1-based  -- reads the panel; writing does NOT move it
word[0]    programs in the selected volume  ) cached by the panel,
word[1]    samples in the selected volume   ) STALE after a remote
word[6]    total directory entries          ) change -- do not trust
```

Writing the partition makes the machine re-read from disk: the panel follows
and the drive's activity light flashes, both confirmed by eye. So the disk
here enumerates in one pass:

```
    A   63 items  55.17 MB      E   59 items  59.53 MB
    B   44        55.38         F   78        53.22
    C   98        58.69         G   53        59.04
    D   54        59.24         H   48        58.99      497 items
```

**Every partition is over the 32 MB the largest machine of this type holds.**

### `byte[4]` is why partition switching "stopped working"

It worked, then stopped, and the difference was a flag left set by an earlier
panel change. The panel sets `byte[4]` when the selection lands on a volume
that does not exist -- it displays "INACTIVE" -- and while it is set the
machine **accepts a partition write and does not re-read**. The directory goes
on describing the previous partition, which looks exactly like the write
having failed.

Clearing it first restores the behaviour, and `select_partition` now does.
A write that is accepted and ignored is a worse failure than one that is
refused, because the acknowledgement is real.

### Two claims withdrawn before they were committed

**`byte[0]` is not the volume.** Writing 2 made the directory go empty, and
that was read as the volume moving to an empty one. The panel said otherwise:
it had switched the device to FLASH, whose first volume is `BOOT SYSTEM#`. An
empty listing has many causes and was treated as though it had one.

**The volume is not settable.** `byte[49]` tracks the panel exactly -- it moved
1 → 2 when the volume was changed by hand, which is what identified it -- and
writing it changes what reads back. But the selection does not follow: a
partition holding 78 items reports the same 78 at volume 1 and at volume 2.

So remote enumeration reaches **volume 1 of every partition and no further**.
On this disc that is everything, because each partition holds a single volume.
On a disc with several volumes per partition it would not be, and how to move
the volume remotely is unsolved.

Both were caught by the panel contradicting a plausible reading of the data.
The pattern is the one from §63: a measurement can be correct and still be
answering a different question than the one asked.

---

## §71 — Writing the load type started a load, and wedged the machine (2026-08-12)

An unplanned finding, from a value sweep that should not have been run.

Bytes 6–9 of the miscellaneous byte bank are the LOAD page's **type of load**,
mirrored — writing one moves all four. The panel produces 1 (ALL PROGS +
SAMPLES) and 2 (ENTIRE VOLUME); 0 is the power-on default.

**Writing one of them, while the LOAD page had a partition and volume
selected, started an actual disk load.** The display showed "Loading
Sample…" in bursts between "BUSY", and after some minutes sat at "BUSY"
indefinitely until the machine was power cycled.

### What this does and does not establish

It is **not** established that this is a usable remote load trigger:

- The first value written was 0, which is also the **power-on default**, so
  "0 means go" is refuted — at boot it sits at 0 and nothing happens. What
  started the load was writing the byte in that context, and the mechanism is
  not understood.
- Throughout the load, a poller was sending `RSTAT` every 8 seconds. That is
  the hardware rule in this project's own CLAUDE.md broken against itself, and
  the observed rhythm — 30–50 s of BUSY punctuated by one or two objects
  loading — is consistent with the load being repeatedly interrupted to
  service probes. So "the machine wedged" and "the machine was being
  disrupted" are not separable from this run.
- The volume was 58.7 MB into a 32 MB machine, so it could never have
  completed. A failure at the end proves nothing about the mechanism.

A clean test needs a volume that **fits**, nothing else on the bus, and no
probing at all. Until then this is recorded as a hazard rather than a feature,
and `s3k.bridge` does not expose it.

### It is a destructive operation

A load clears RAM and replaces the resident programs and samples. If it is
ever exposed it belongs behind the arm-then-fire flow that `DELP`, `DELK` and
`DELS` get, not on a keypress.

### The SCSI drive ID does not persist

Separately, and confirmed by the same power cycle: `byte[11]` was set to 4,
the panel showed 4, and after the reboot it read **5** again. So the write
changes the running value and the display but does not survive a restart —
which is also the most likely reason changing it never altered the volume
list, since a SCSI ID is normally bound when the bus is scanned at boot.

### How it happened, which is the part worth keeping

An hour before this, these notes recorded that blind-probing unknown
miscellaneous indices needed explicit authorisation, because a wrong index
could plausibly be a format and the disc images are not covered by "nothing
volatile on the sampler".

The sweep that triggered this wrote values 0–7 into a byte whose meaning had
just been identified, inside a loop that read as routine range-checking. It
was the same act under a different description. **A stated rule did not fire
because the situation did not look like the one the rule described** — which
is §63's lesson arriving in a new place: the check has to be attached to the
action, not to the story about the action.

---

## §72 — Five of six: the load and the menu are remotely controllable (2026-08-12)

The workflow was: select a SCSI ID, open the LOAD menu, choose floppy/hard/
flash, choose a partition and volume, load it, and get the programs with their
samples. Five of those six work over MIDI.

```
SCSI drive ID    byte[11]   works LIVE, no reboot -- five discs, five listings
device type      byte[0]    floppy / hard / flash
main-menu page   byte[91]   SINGLE 0, GLOBAL 8, LOAD 10
partition        byte[2]    works; the machine re-reads, the drive light flashes
volume           --         NO REGISTER. Panel only.
trigger a load   byte[6]    works; verified end to end
```

### The load works, and the earlier disaster was mine

Writing the load-type byte on a **3.62 MB volume into an empty machine, with
nothing else on the bus**, loaded cleanly in seconds:

```
before   1 program,  4 samples,   131,072 words (0.8%)
after    2 programs, 12 samples, 1,942,384 words (11.6%)
```

The single program on that volume arrived with all eight of its samples. So
"ALL PROGS + SAMPLES" does pull the dependencies, which was the last part of
the workflow.

The earlier run that ended in "BUSY" until a power cycle was a 58.7 MB volume
into a 32 MB machine while a poller interrupted it every 8 seconds. **Neither
of my two explanations for it was right** -- the trigger was not broken and
the machine was not left in a bad state by the write.

**A load ADDS rather than replaces.** `TEST PROGRAM` and the four factory
waveforms survived it. §71 recorded that a load clears RAM and would need
arm-then-fire treatment; that is withdrawn.

### The main menu is a variable, and writing it moves the machine

There is no button injection in this protocol -- no keypress message, no panel
echo, and §1 settled that years of looking would not find one. This is not
that. The current page is a **variable**, and writing it switches the machine:

```
wrote 10 LOAD    ack ok       reads 10   TOOK
wrote  8 GLOBAL  ack ok       reads  8   TOOK
wrote  0 SINGLE  ack ERROR    reads  0   TOOK
```

The values are an internal enumeration with gaps, not button positions:
GLOBAL is the second button of the second row and reads 8, where its position
would be 5.

### The acknowledgement lies in both directions

This is the finding worth carrying furthest.

```
byte[4]    accepts a write, replies OK, and IGNORES it
byte[91]=0 replies with error code 1, and PERFORMS the write
```

Both were mistaken for their opposite. `byte[4]` produced an hour of "the
partition write has stopped working"; `byte[91]` produced a written-down
conclusion that the mode register was read-only, contradicted a minute later
by Jan saying the display had moved.

`REPLY` has been leaned on throughout this project as making a write
verifiable without reading back -- it says so in `s3k.messages`. **That is not
safe.** The reply is evidence about the message, not about the effect. Reading
the state back is the only thing that settles it, and `select_mode` therefore
swallows the error and returns what the register reads.

### `byte[49]` is not the volume

Recorded in §70 as "volume, 1-based, reads the panel but writing does not move
it". It is not a volume register at all: it holds the value of whatever field
the cursor is on -- 3 while the LOAD page showed volume 3, and **0 in SINGLE**,
which has no such field.

That explains the whole puzzle. It was never a control, so writing it could
never have done anything, and the "1 -> 2" that identified it was the cursor
sitting on the volume field at the time.

**There is no volume register.** Remote enumeration reaches whichever volume
the panel last selected in each partition. Confirmed on a 30-volume disc,
which is the one that could have shown otherwise.

### And the SCSI ID binds live

§71 said it probably bound at boot, because changing it never altered the
volume list. With five discs on the bus at IDs 0-4, sweeping the ID walks
through five different listings with no reboot. The old conclusion was drawn
on a bus with one disc, where "switched to an empty ID" and "did not switch"
look identical.

## §73 — ALL PROGS + SAMPLES *adds*, and the fit check is right for it (2026-08-12)

**Status: settled.** Measured 2026-08-12 by driving the TUI's own key path
against the machine, with no one at the front panel.

§72 left the load verified but the memory arithmetic untested against a load
in flight. This run tested it, and the prediction was wrong in the
informative direction.

The first attempt reloaded the volume that was already resident, which is a
useless experiment: the volume replaces itself, so free memory ends where it
started, and `+0 words` is what "loaded correctly" and "did nothing at all"
both look like. Partition is remotely settable (§70), and each partition on
this disc carries one volume of its own size, so stepping to a differently
sized one turns free memory into a reading that can fail.

Prediction, written before the run: loading a 15.30 MB volume leaves
16,777,216 − 8,020,426 = 8,756,790 words free. What happened:

```
resident before      1,942,384 words    3.70 MB
volume audio         8,020,426 words   15.30 MB
sum                  9,962,810 words   19.00 MB
actually resident    9,963,440 words   19.00 MB
excess over sum            630 words
```

The load did not clear memory first. It **added** to what was resident, and
landed on the sum to within 630 words — 0.008%, which is the programs' own
storage, since `audio_words` counts sample data only.

So with load type 1 (ALL PROGS + SAMPLES) the machine accumulates across
loads. This matters more than it sounds: a bank built by loading three
volumes in succession needs the *sum* to fit, and the third load is the one
that fails even though each volume fits on its own.

**The consequence for s3ked's fit check is that it was already correct.** It
compares the volume against `free_words` read fresh at the time of the check,
not against total memory, which is exactly the additive semantics. For load
type 2 (ENTIRE VOLUME) the machine clears first, so checking against current
free is conservative — it can warn that a volume will not fit when it would.
Warning about a load that would have worked is the safe direction to be
wrong, so the check is left as it is.

### The bug this run found in passing

`S3kedApp` asked `DeviceStatus` for `words_free`. The attribute is
`free_words`. The `getattr` returned `None` every time and fell through to a
fallback that assumed a 16-Mword machine, so **every** fit check was measured
against 32 MB regardless of what was installed — a 2 MB S3000XL would have
been told that everything fits. Fixed, and the fallback deleted rather than
corrected: the machine reports its own size in the same reply, so there is
nothing to fall back to. A test now drives the app with a bridge reporting
1 Mword and asserts the confirmation says 2.00 MB and refuses a 3.56 MB
volume.

This is the second time in this project a `getattr(..., default)` has hidden
a name mismatch by succeeding quietly. The pattern is the problem: a default
turns a typo into a plausible number.

### What was verified, and what was not

Verified: the `d` and `l` keys drive a real load end to end; the
confirmation's figures are the machine's own; the additive arithmetic above;
that the machine stays responsive and its directory still reads after the
load settles.

Not verified: load type 2's clearing behaviour, which was not exercised —
the panel is set to 1 and nobody was at the machine to change it.

## §74 — The load trigger has one value, and CLR is not remote (2026-08-12)

**Status: settled.** Measured 2026-08-12, RAM only, nothing written to disc.

The panel's LOAD page has two softkeys: **LOAD**, which appends to what is
already in memory, and **CLR**, which erases waveform memory and then loads.
§73 measured value 1 in the trigger register and found it appends, so the
obvious reading was 1 = LOAD, 2 = CLR.

That reading was wrong, and it is worth naming why it was tempting: it was
arrived at by elimination from a two-item list, which is the same move that
produced the wrong `byte[0]` and `byte[49]` conclusions earlier in this
sequence. Two candidates and one measurement does not determine the mapping;
it determines one entry and leaves the other a guess.

### What the register actually does

Writing 2 stored perfectly — all four of bytes 6-9 read 2 afterwards, so the
mirroring is confirmed — and moved memory by **exactly zero words**. Then
every remaining value in the byte's low range was written and waited out, one
at a time, 90 s each, with nothing else on the bus:

```
 value   resident after      MB   verdict
     0       11,861,952   22.62   inert
     3       11,861,952   22.62   inert
     4       11,861,952   22.62   inert
     5       11,861,952   22.62   inert
     6       11,861,952   22.62   inert
     7       11,861,952   22.62   inert
```

Not one of them moved a single word. Writing **1** from that same state
appended the selected volume immediately:

```
resident before      9,963,440 words   19.00 MB
volume               1,898,392 words    3.62 MB
resident after      11,861,952 words   22.62 MB
miss from the sum         +120 words
```

So bytes 6-9 are **a trigger that keeps its last value**, not a load-type
selector. The value 1 acts; every other value in 0-7 is stored and inert.
Values above 7 are untested.

The +120 words is program storage, consistent with §73's +630 across 49
items against +120 across 10 here.

### Consequences

1. **There is no remote CLR.** Memory can be reclaimed remotely only by
   deleting resident items, or not remotely at all. Since the load appends,
   a machine driven purely over MIDI fills up and stays full.
2. **The TUI must not offer one.** It briefly did — CLR was added to the
   Master screen with arm-then-fire, correctly treated as destructive, and
   built entirely on the guess above. Removed. A test now asserts the
   destructive menu offers no load at all, so the option cannot come back
   without the measurement that would justify it.
3. **§71's account is revised but not contradicted.** That sweep wrote 0-7
   into this register and a real load started; the load was value 1's doing,
   and the other seven writes did nothing. The wedge that followed remains
   attributed to the concurrent RSTAT probing, which is the part not repeated
   here — this sweep is the same shape on a quiet bus and the machine stayed
   responsive throughout.

### Method note

The reading that settled this was memory arithmetic, not the display. With
19.00 MB resident and a 3.62 MB volume selected, "clears then loads" and
"appends" differ by 19 MB, so the two are impossible to confuse and no one
needs to be at the machine. Choosing the resident state to make the two
predictions far apart is what made the experiment work; running it against
an empty machine would have made CLR and LOAD indistinguishable.

## §75 — CLR is a panel chain; the effect is reachable, the button is not (2026-08-13)

**Status: settled.** Measured 2026-08-12/13, RAM only.

§74 established that the trigger register has exactly one acting value and
concluded there was no remote CLR. That conclusion was right about the
register and wrong about the capability, and the correction came from asking
what CLR *is* rather than which value it might be.

The owner's manual describes it as a sequence, not a button:

> F7-CLR を押すと、メモリ全体をクリアしたいかどうかを尋ねるメッセージが出ます。
> F8-YES を押します
>
> ("pressing F7-CLR brings up a message asking whether you want to clear the
> entire memory; press F8-YES")

and, for a load: select the volume, `F7-CLR`, then `F8-GO`. So CLR raises its
own on-screen confirmation and is answered by a second keypress, and the load
is a third. `byte[6] = 1` is the `F8-GO` step alone. Nothing in the register
was ever going to reach the other two — which is why sweeping it found
nothing, and why the sweep was the wrong instrument rather than a
disappointing result.

### The effect, built out of what does exist

Deleting every resident sample and program reproduces it:

```
resident before   11,549,680 words   22.03 MB
cleared           60 samples, 1 program
resident after       131,072 words    0.25 MB
reclaimed         11,418,608 words   21.78 MB
```

Two facts had to be measured on the way, and the first one nearly ended the
investigation:

**DELS does return waveform memory** — but the first sample deleted was a
few-thousand-word calibration tone, and the free figure did not move at all.
Read on its own that says "the machine unlists without reclaiming", which
would have killed the idea. Deleting a sample of *known and large* size
settled it: `SLNGTH` said 312,257 words, and the free figure moved 312,272.
Picking the largest resident sample, and writing the predicted figure down
before the delete, is what made the reading interpretable.

**The last program cannot be deleted.** The delete is acknowledged OK and the
list stays at one. An earlier loop counted to its 300-iteration guard against
this and reported "nothing happened", when in fact it had taken the programs
from 9 down to 1 first. The guard hid the finding; a progress check would
have shown it immediately, so `clear_memory` stops when a delete stops
changing the list rather than counting to a number.

**0.25 MB is never returned.** 131,072 words exactly — 2^17, too round to be
anything but a fixed system reserve. Whatever it is, it is not available and
a memory budget should be figured against `free_words`, which already
excludes it, rather than against `max_words`.

### What this means for the editor

`clear_memory()` deletes samples first and programs second, since a program
header costs about a hundred words and the megabytes are all in the samples.
In the TUI it sits in the Master screen behind arm-then-fire, with the
deletes it is made of, and not next to the load — a load adds and can be
undone by deleting, whereas this is the delete.

"CLR then load" is therefore two deliberate operations here, which is
honest: it is two on the machine as well.

## §76 — The drive ID records a choice; a second write makes the machine act (2026-08-13)

**Status: settled.** Measured 2026-08-13. Reads and selection writes only.

Three defects in this project's own code, found by trying to use it at bus
scale for the first time. None of them is a defect in the machine.

### 1. Writing the SCSI ID does not send the machine to the drive

Sweeping the ID and reading the volume list after each write returned **the
same list every time** — six IDs, four volumes, identical first name. Reading
after a *following* write to `byte[2]` or `byte[4]` returned each drive's
real list, and the id-only column turned out to be the same data **one drive
behind**:

```
drive |  A: id only          B: id+partition
  0   |    4 (prev drive's)     9
  1   |    9 (drive 0's)        4
  2   |    4 (drive 1's)       31
  3   |   31 (drive 2's)       18
  4   |   18 (drive 3's)        1
```

Off by exactly one is the signature of a read taken before the machine had
gone anywhere. The ID register **records** a selection; a subsequent write to
`byte[2]` or `byte[4]` is what makes it **act**. Both trigger it equally;
`byte[4]` is now used, since it does not move the selection.

§72's "sweeping the ID walks through five different listings" was right that
the ID is not bound at boot, and wrong about the mechanism — the listings it
saw were each one step stale, and it never compared them against a known
disc.

### 2. Three registers answer with an error and perform the write anyway

`byte[2]`, `byte[4]` and the mode register all reply **error code 1** and
carry out the write, in every state tested, on both the SINGLE and LOAD
pages. `byte[4]` additionally replies OK and ignores the write in one state
(§72). So there is no state in which the acknowledgement is a reliable
account of what happened.

The bridge raised on it. That aborted a bus scan mid-way on a device that was
working perfectly, and produced "SCSI 3-7: write refused" for drives that
were simply never written to. Selection writes now use `_misc_write_verify`:
write, swallow `DeviceError`, read back, and raise only if the register did
not end up holding the value. A test asserts the error is swallowed **and**
that a genuine refusal — a register that does not move — still raises, since
the easy version of this fix swallows both.

### 3. The volume list is per-partition, not per-drive

"9, 4, 31, 18, 1 volumes on drives 0-4" is really the count in whichever
partition each drive happened to have selected. Re-reading with different
partitions current gives different counts for the same disc. Volumes live
inside partitions, so a volume count is only meaningful alongside the
partition it was read in.

### Method note, and a cost

The first bus map was run before any of this was understood, and every number
in it was wrong: stale lists attributed to the wrong drives, and whole
partitions reported as refusing writes that were never attempted. It looked
entirely plausible. What exposed it was re-reading two drives a few minutes
later and getting different answers — the check was cheap and was only done
because the numbers were being used for something else.

**A directory read that follows a selection write needs the selection to have
taken effect, and the machine does not say when it has.** Read something
twice before believing a survey of it.

## §77 — What a power cycle settles: five defaults and one reserve (2026-08-13)

**Status: settled.** Measured 2026-08-13 across a power cycle, with the state
snapshotted beforehand so the boot could be diffed rather than described.

### The built-in waveforms are regenerated at boot

`SINE`, `SQUARE`, `SAWTOOTH` and `PULSE` came back on their own. They are not
loaded from any volume — the search that preceded this looked at flash,
floppy and all 24 hard-disk partitions on the bus and found them nowhere.
They are the machine's own, and a boot restores them.

That matters practically, because the calibration probes address the source
by name (`SNAME1 = "SAWTOOTH"`) and had been left without one after
`clear_memory` deleted them. A power cycle is the repair. **`TEST PROGRAM` is
also created at start-up**, which is why it is the program the machine
refuses to delete (§75).

### The 0.25 MB is a genuine reserve, and it holds the built-ins

§75 recorded 131,072 words never returned and called it a fixed reserve on
the strength of the number being round — reasoning, not measurement. The boot
settles it, and adds the mechanism:

```
the four built-in waveforms    256 words each,   1,024 total
held by a freshly booted machine                131,072
difference                                      130,048
```

The figure is **131,072 both before and after the boot** — with no samples
resident and with all four built-ins resident. So the built-ins live *inside*
the reserve rather than in user waveform memory, and 130,048 words of it are
something else again.

This also explains the reading that nearly derailed §75: deleting `SINE`
freed exactly zero words, which looked like a machine that unlists without
reclaiming. It reclaims fine — a 312,257-word sample returned 312,272 — but a
built-in is not in the pool being reclaimed from. The free figure is not
coarsely quantised; the sample was simply not there to free.

**Usable waveform memory is `free_words`, and never `max_words` minus what
you think you loaded.**

### Power-on defaults, measured

| register | before | after boot | |
|---|---|---|---|
| `byte[0]` device type | 1 | **0** | |
| `byte[2]` partition | 7 | **0** | A |
| `byte[6..9]` load trigger | 7 | **0** | confirms the comment's claim |
| `byte[11]` SCSI drive | 1 | **5** | |
| `byte[49]` cursor value | 5 | **0** | |
| `byte[91]` mode | 10 | **0** | SINGLE |

Two of these were assertions in this project's code that nobody had checked.
`byte[6]`'s "0 is the power-on default" is now measured rather than assumed.

**The drive ID is not persisted — it is reset to 5.** It read 1 before the
cycle and 5 after. This is the third and final correction to §71's "the SCSI
ID binds at boot": the ID does not bind at boot (§72), it does not act until
a following write (§76), and it does not survive a boot at all. The original
claim was wrong in every part, and each part failed for a different reason,
which is why it took three passes to dismantle.

## §78 — Sweeping pages with absent media wedges the machine (2026-08-13)

**Status: settled as a hazard.** The machine required a power cycle.

Sweeping `byte[91]` from 0 upward: values 0-8 took the write cleanly. Value 9
took it and returned **one malformed reply on arrival** — a short body where a
data frame was expected — after which the machine was fine. Continuing into
10-15 left it answering nothing at all, on any port.

The explanation is not "value 9 is bad". `byte[0]` boots to **0**, which is
FLOPPY, and there is no floppy in the drive. Value 9 is SAVE. A save page
opening on an empty floppy drive has nothing to render, and LOAD gives the
same behaviour for the same reason. The sweep walked the machine onto disk
pages while its device selector pointed at absent media, repeatedly.

**This was avoidable and the information to avoid it was already in hand.**
The power-on default of `byte[0]` had been measured twenty minutes earlier
and recorded in §77. What was missing was connecting a measured default to
what the next probe was about to do — the page sweep was designed as a test
of the mode register in isolation, and pages are not isolated from the device
selector.

### The rule

**Select a device that has media before moving to any page that reads one.**
`byte[0]` to 1 (HARDDISK) first. A sweep across pages is not a read-only
operation, however read-only the register looks: arriving at a page makes the
machine do the page's work.

### What the sweep established before it died

| value | result |
|---|---|
| 0-8 | all took the write; 0 is SINGLE, 8 is GLOBAL |
| 9 | took it, one malformed reply on arrival — SAVE |
| 10 | LOAD, known already |
| 11-15 | never cleanly reached |

The document's "eleven modes available from the eight mode keys" therefore
remains unconfirmed against the register: whether 11-15 are refused is
exactly what was not reached.

### A discriminator that is now gone

`RMULTIDATA` (0x41) answers in **every** mode, so it is not gated on MULTI
and cannot be used to identify that page. That was the best eyes-free
candidate for naming an unknown mode, and it does not work. `byte[49]`
fingerprints differ between pages (mode 2 read 1, mode 4 read 7, mode 6 read
1, the rest 0) but a field value cannot name the field's page.

Naming the remaining modes probably does require somebody at the display.

## §79 — Eleven modes, confirmed; and 11 is a crash, not a refusal (2026-08-13)

**Status: settled.** Measured 2026-08-13, with HARDDISK selected on a drive
with media — deliberately, because §78's wedge came from sweeping pages over
an empty floppy drive and that confound had to be removed before anything
here could be attributed to the value itself.

The S2000/S3000XL document says **"there are now eleven modes available from
the eight mode keys"**: SINGLE, MULTI, SAMPLE and EFFECTS, an EDIT variant of
each of those four, plus LOAD, SAVE and GLOBAL.

The register takes **0-10** without incident. That is exactly eleven values,
and the three known names sit in it consistently — SINGLE 0, GLOBAL 8, LOAD 10, with SAVE
at 9 on the evidence of §78's malformed reply. The count is now a property of
this machine and not only a sentence in a manual.

**Writing 11 stops the machine answering at all.** Not an error reply and not
a clamp — no reply to anything, on any port, until a power cycle. Media was
present and the device selector was on HARDDISK, so the wedge is attributable
to the value rather than to §78's empty-drive confound.

**What that does NOT establish is that 11 is out of range.** Two readings fit
the silence equally: the value is past the end of the enumeration, or it is
one of the eleven real pages and cannot initialise for a reason of its own —
the same shape as SAVE opening on an empty floppy drive, which also produced
a malformed reply rather than a refusal. A hang looks identical either way
over the wire, and separating them needs somebody watching the display.

Eleven values taking the write is consistent with the document's eleven
modes, and that is as far as the evidence goes. Recorded in `TODO.md` as
needing visual confirmation.

### The consequence for the code

`select_mode` passed any integer through. The machine's response to a bad one
is to die, so **the range check in the bridge is the only thing that says
no**. It is not defensive tidiness standing in for a device that would have
rejected the write.

That is a different situation from the other guards in this project. Elsewhere
a range check duplicates a refusal the device would have made anyway and
mainly improves the error message; §51 even had to be careful not to make one
tighter than the machine's own. Here there is nothing to duplicate.

### Cost, and what it says about page probes

Two power cycles, for one number. Worth it — an editor that can move the
machine between pages needs to know where the pages stop, and the answer was
never going to come from a reply code.

But both wedges came from the same class of action: **writing a value to a
page register and finding out what happens.** §78's came from a page with no
media under it, §79's from a value with no page behind it. A parameter write
that is out of range gets an error reply; a page write that is out of range
gets silence. Pages are not parameters, and probing them costs a power cycle
each time the guess is wrong.

## §80 — The cross-reference, and a fixture that was a false claim (2026-08-13)

**Status: settled.** Built and verified 2026-08-13 against a real bank.

`s3k/analysis.py` answers the two librarian questions the sibling eosed
answers — *who uses this sample*, and *what points at nothing* — and exists
because of §73: a load that exceeds free memory reports "insufficient
waveform memory" once and then behaves normally, leaving programs resident
and selectable whose samples never arrived. They play silence. Nothing on the
machine distinguishes them from a program that is merely quiet.

### Samples really can share a name (measured 2026-08-13)

`Audit.ambiguous()` was written on the strength of §13a, which made two
**programs** share a name and saw neither deleted. That says nothing about
samples, and this project extended it to samples because it seemed obvious --
then told the sibling mpc2emu so, as a fact, in a handoff they were about to
build a control against.

Measured instead. Renaming sample 1 to sample 0's name, with ten resident:

```
before  ['SINE', 'SQUARE', 'SAWTOOTH', 'PULSE', ...]
after   ['SINE', 'SINE',   'SAWTOOTH', 'PULSE', ...]
```

Ten before, ten after, two carrying the name. So the duplicate state is real
and the check guards something that can occur. Right answer, reached the
wrong way: a parenthetical doing work it had not earned.

### A comparison this project does looser than the sibling

`ambiguous()` compares `name.strip()` -- no case folding, no truncation.
mpc2emu upper-cases and truncates to 12 before comparing.

Neither difference matters for material the machine produced: the Akai
charset has no lowercase and names arrive exactly 12 characters. But
`.strip()` means **two names differing only in trailing whitespace collide
here and not there.** If a bank writer emits `BASS` and `BASS ` as distinct
fields, this audit reports one duplicated name and the writer is not at
fault.

### A zone is disabled by its velocity pair, not by its name

From mpc2emu, measured over 54,488 zones from real discs: `hi_vel == 0`
means a zone can never be selected, because MIDI velocity 0 is note-off.
Two spellings occur and mean the same — `lo=1, hi=0` inverted, and
`lo=0, hi=0` — and **both leave a leftover name in the slot**, often a ROM
waveform's. So a disabled zone still names a sample, and reporting that name
as missing is a fault the user cannot act on and did not cause.

Their zone-relative offsets (+0c, +0d) are this project's `LOVEL` and
`HIVEL` at `SNAME`+12 and +13 — independent agreement on the layout, from a
different source, before trusting the semantics.

**Not reproduced here.** Every zone on every bank this project has loaded
reads `lo=0, hi=127`; a scan of all 66 references on the resident bank found
none disabled. `dangling()` excludes unreachable zones by default and
`suppressed()` lists what it hid, rather than silently dropping them.

**Correction, from mpc2emu:** this note first said "the semantics are
theirs", carrying the rule on their 54,488-zone corpus. That was the weaker
half of the evidence. The rule follows from **velocity 0 being note-off in
MIDI** and holds on any machine implementing MIDI correctly — no corpus
required. Their measurement establishes which *spellings* occur in practice,
and they add that the distribution is library-dependent and should not be
generalised. Attributing a general rule to a particular corpus understates
it and quietly makes it look contingent.

The velocity pair costs **no extra round trips**: it is contiguous with the
name, so one 14-byte read gets all three where the name alone took 12.
Fetching them separately would have tripled a walk that already costs four
reads per keygroup.

**An inverted range is dead too, and that one is measured.** `lo=100, hi=50`
selects nothing by the same logic, but that is a claim about what the machine
does with a range it was never meant to hold, and machines clamp, swap or
wrap such things. Neither project had tested it — mpc2emu's note said so
explicitly. Note 60 at velocity 75 with the built-in sawtooth:

```
setting                 lo   hi        RMS
control, full range      0   127    0.00711
outside the range      100   127    0.00003
inverted               100    50    0.00003
```

The gate is real (247×) and inverted is as silent as out-of-range. The
machine stored `100..50` as written, so it neither swapped nor clamped the
pair. `lo == hi` stays reachable — a one-velocity zone is not an empty one.

**How often a dead zone names something absent**, from mpc2emu over 54,488
zones. **Historical, and not re-derivable.** They searched for the corpus on
2026-08-13 and it is not on their disk: the reference was corrected against
40 real library discs when those figures were taken, most likely physical
CD-ROMs read once with only the derived numbers retained. So this is a
recorded measurement neither project can re-run, and a future result that
disagrees with it cannot be adjudicated by re-measuring — treat it as
provenance, not as a check that can be repeated:

| | zones | naming a sample present on the disc |
|---|---|---|
| `hi_vel == 0` | 10,825 | 4.43% |
| `hi_vel > 0` | 43,663 | 97.13% |

The **qualitative** self-check survives the figures being historical, and is
the part worth keeping: a dead zone holds a leftover name, so a `suppressed()`
list consisting mostly of zones naming *resident* samples means the
suppression is wrong rather than the bank. That follows from what a disabled
zone is, not from anyone's corpus. The percentages sharpen it and can no
longer be reproduced. Their measure of the false-positive class removed: trusting the
name alone invents up to three phantom zones per keygroup, 65% and 67% of two
discs' zones.

**The other half, measured 2026-08-13.** mpc2emu asked for it, and the reason
was better than curiosity: their writer clamps `lo_key` and `hi_key`
independently — `_clamp(lo, 24, 127)`, `_clamp(hi, 24, 127)`, no relational
check — so a malformed source with lo=72 hi=60 is written verbatim. Clamping
members of a pair separately cannot catch a constraint that exists *between*
them. The velocity result made that worrying rather than academic: the
machine had stored `100..50` as written, so it does not defend itself.

Note 60, velocity 100, isolation 52.5 dB:

```
key range                    stored        RMS
24..127, spans the note      24..127    0.00711
72..127, above it            72..127    0.00003
24..48,  below it             24..48    0.00003
72..48,  INVERTED             72..48    0.00003
60..60,  one key ON it        60..60    0.00712
61..61,  one key beside it    61..61    0.00003
```

Same three answers as velocity, one level up: an inverted key range is dead,
a single-key range is alive on its own note, and the pair is stored verbatim
rather than swapped or clamped — so a reader can see the state, not only a
writer create it. `verify_isolation` did double duty here, since the way it
silences a keygroup *is* by moving its key range off the note: without it a
silent result would have been uninterpretable.

**A dead key range kills the whole keygroup**, so all four of its zones go
unreachable, where a dead velocity pair kills only its own zone.
`reachable` is now the conjunction of both, at a cost of one 2-byte read per
keygroup — `LONOTE` and `HINOTE` are adjacent at offsets 3 and 4.

What is still not covered: whether some *other* keygroup shadows this one,
and what the machine does with overlapping ranges. Neither project has
tested that.

### A third way a name collides with an empty zone

Found by mpc2emu, from this project's twelve-spaces measurement. Their
encoder **substitutes** characters the Akai alphabet lacks with spaces
rather than refusing, so a sample named entirely of unsupported characters —
CJK, punctuation — encodes to twelve spaces:

```
name '日本'        -> 0a x12
name '!!!'         -> 0a x12
unassigned zone    -> 0a x12
```

A zone naming such a sample is byte-identical to a zone naming nothing, so
this walk reads it as unassigned and the sample becomes invisible. Their
guard was `stem or 'SAMPLE'`, which catches the empty string only — a
non-empty name that *encodes* to nothing is still truthy in Python.

`Audit.indistinguishable` now covers both collision forms, all-zero and
blank, rather than all-zero alone.

**The two projects fail this in opposite directions from the same design
choice.** `encode_name` here *refuses* what the device cannot store, which
took the whole audit down over one lowercase character; theirs *substitutes*,
which silently wrote a sample whose name cannot be told from an empty slot.
Neither default is obviously right.

### Two bugs this found in code that already passed its tests

**The demo's zones could never sound.** A blank keygroup header leaves
`HIVEL1` at 0, so once the suppression landed, the demo's deliberate
dangling reference was correctly hidden and the integrity screen went empty.
The fixture had been unrealistic in a way nothing exercised until a check
depended on it.

**One odd sample name aborted the entire audit.** `collect` classified every
resident name by calling `encode_name` on it, and that function refuses
anything the device cannot store rather than substituting — right for
writing, wrong for classifying names that already exist. A lowercase
character or an over-long name raised, and the whole walk died. It cannot
arise from names the machine produced, and it did arise from a caller
passing its own list.

### What differs from eosed, and why it is not a port

EOS voices reference a sample by **number**, and keep `E4_GEN_SAMPLE = N`
after sample N is erased, so a dangling reference there is a number pointing
at the device's "Empty Sample" placeholder. Akai keygroup zones reference by
**name**. That makes the check a set membership rather than a placeholder
comparison, and it makes duplicate names a genuine ambiguity — the machine
enforces no uniqueness (§13a), so a zone naming a duplicated sample cannot be
resolved to one of them. `Audit.ambiguous()` reports that rather than
silently picking the first.

### An unassigned zone holds twelve SPACES

Measured: an unused zone reads `[10]*12` and decodes to blank, while the
assigned zone beside it holds a twelve-character name.

This module was written assuming twelve **zeros**, and tested against a
fixture that invented them. Nine synthetic tests passed. The first real bank
produced **248 references, 182 of them to `''`** — every empty zone on the
two programs, reported as a dangling sample with an empty name.

**The fixture was a claim about the device, and it was wrong.** No amount of
testing against it could have found this, because it and the code agreed. What
found it was running the walk against hardware and looking at the *count*:
248 references across two programs is not a plausible number, and it was
wrong in the direction that produces alarming output rather than silence,
which is the only reason it was noticed at once.

Both encodings are now accepted as unassigned — zeros are what an unwritten
field would hold — but blank-after-decode is the check that matters.

### The zero case still has a trap, and it is unresolvable

Index 0 of the Akai charset is the character `0` (the §68 trap), so twelve
zero bytes decode to `000000000000`, **not** to blank, and
`encode_name("000000000000")` is likewise twelve zeros. A sample genuinely
named `000000000000` and an unwritten field are therefore the same bytes and
cannot be separated at all. The convention is "zeros mean unassigned", and
`Audit.indistinguishable` names any resident sample whose name collides, so
usage counts for it are reported as a lower bound rather than as fact.

### Verified against hardware, including the failure case

A check that only ever reports "no problems" has not been tested. So: load a
volume, audit it clean, then **delete two samples the audit says are used**
and require it to name exactly the affected zones.

```
before   66 references, 2 programs, 12 samples, 0 dangling
predict  deleting the two most-referenced samples dangles 24 references
after    24 DANGLING in 1 program — exactly those two names
```

The walk is bounded by `GROUPS` and never by a guess: the extended layer does
not bounds-check reads and returns the previous valid read's buffer instead
of an error (§11), so a fixed upper bound would manufacture references by
re-reading the last real keygroup — and they would look entirely plausible.

Cost: 4 reads per keygroup plus one per program. A 61-keygroup bank audits in
4.3 s.

## §81 — Overlapping keygroups layer; neither shadows the other (2026-08-13)

**Status: settled.** Measured 2026-08-13, RAM only.

§80 left one half of reachability open: whether a keygroup that overlaps
another on the same note still sounds, or whether one wins. The consequence
is mpc2emu's — their writer builds one keygroup per key span, so overlaps
arise from any layered multisample. If only one answers, every layered
program they write loses voices silently and no file inspection would show
it.

### Built rather than found

Program 1 has 61 keygroups; all were parked at 24..24 except two, which were
made to overlap on note 60. A program found in a library carries whatever its
author intended; this needed the case with nothing else varying.

### The discriminator is the sample, not the level

Two keygroups playing the same sample at the same pitch sum coherently, and
+6 dB is hard to distinguish from one louder voice. So keygroup A got `SINE`
(one partial) and keygroup B got `SQUARE` (strong third harmonic), making
`3f0/f0` a marker for *which* voice is present rather than merely how loud
the result is.

```
case                        RMS    3f0/f0
A only  (SINE)          0.01999     0.000
B only  (SQUARE)        0.01981     0.335
BOTH overlapping        0.03377     0.184
```

**Both answer.** Three things agree, which is why this is settled rather than
suggestive:

1. `0.03377` exceeds the loudest single (`0.01999`) and also the incoherent
   power sum (`0.02814`), sitting below the fully coherent amplitude sum
   (`0.03980`) — two voices sharing a fundamental, partly in phase.
2. The third harmonic survives at `0.184`. If `SQUARE` alone were sounding it
   would read `0.335`; if `SINE` alone, `0.000`. Predicted for both present:
   `0.335 × 0.01981/0.03980 = 0.167` against `0.184` measured.
3. `SINE` alone reads `3f0/f0 = 0.000`, which validates the marker itself —
   a spectral ratio that never reached zero would say nothing about which
   voice was present.

### Consequence

`ZoneRef.reachable` needs no priority model: a zone in an overlapping
keygroup stays reachable. The caveat §80 carried — "whether some other
keygroup shadows this one" — is closed, and closed in the direction that
required no code change, which is worth stating because the alternative
would have required modelling voice priority to avoid reporting live zones
as dead.

Not covered: what happens when overlapping voices exceed polyphony. That is
a resource limit rather than a routing rule, and it is not what an audit of
a program's references is asking about.

## §82 — Refusing the reads the device answers with somebody else's data (2026-08-13)

**Status: closed client-side.** The device is unchanged and still behaves
this way.

§11 found that an out-of-range `RPHEADER`/`RKHEADER`/`RSHEADER` returns the
previous valid read's buffer instead of the documented error. Re-measured
2026-08-13 before building against a three-day-old finding, and it is worse
than §11 recorded:

```
reading 8 bytes at offset 20, on a machine with 2 programs / 10 samples
  primed with program 0:        [127, 0, 255, 99, 0, 80, 20, 0]
  program 42                    byte-identical to the primed read
  keygroup 31 of a 1-keygroup   byte-identical
  sample 50                     byte-identical
  offset 200 of a 192-byte      real-looking data from past the header
  program 42 at OFFSET 0        byte-identical -- the guard did not fire
```

**The existing block-identifier guard never caught a bad index.** It compares
byte 0 against the region's identifier, so it catches reading a keygroup and
getting a program block. But an out-of-range *program* read answers with a
*program* block, so the identifier is correct and the check passes. It only
ever caught cross-region confusion, and the comment beside it implied more.

### The guard

`_check_bounds` refuses before sending, on reads and writes:

- **offset + count against the region's header size** — free, no round trip,
  and it catches the case that returns data from past the structure.
- **index against the program or sample count**, and **selector against that
  program's `GROUPS`** — one round trip per region, cached.

`program_list` and `sample_list` now record their own counts, so ordinary use
warms the cache and the guard usually costs nothing at all.

### The cost, pinned rather than discovered later

Refusing needs to know how many programs exist, and that is a round trip. It
broke two tests asserting "a whole header must cost one round trip" — which
were right to break, so the property is now stated as the steady-state cost
with the one-time read pinned by its own test. A walk of hundreds of headers
pays it once.

Every operation here that changes the structure calls `invalidate_structure`:
the three deletes, `clear_memory`, and `trigger_load`, since a load replaces
the whole bank.

### What this does not fix

The cache cannot know about changes made at the front panel. Being wrong
about it is survivable and that is deliberate: a stale count produces a
**refusal naming the counts it used**, never a silent wrong answer. The
failure mode is loud and the message says what to do.

`bounds_check=False` turns the index checks off, for probes that need to ask
the device what it actually does — the one job the guard gets in the way of,
and how the table above was produced.

### A fixture that claimed the impossible, again

The fake sampler's blank program header left `GROUPS` at 0, and the new guard
correctly refused to index into a program with no keygroups. `GROUPS`' range
is 1..99: zero is not a state the device can be in. Third time in two days
that a fixture asserted something no machine produces (twelve-zero names §80,
`HIVEL1` at 0 §80), and each time it was invisible until a new check read
that exact field.

## §83 — The undescribed sample-header tail has visible structure (2026-08-13)

**Status: characterised, not decoded.** Read-only, six library samples from
one multisample plus the four built-ins as a control.

Our table stops at sample-header offset 141. §14 recorded that real library
samples carry consistent non-zero structure in 171-191 while machine-authored
ones are all zero. This pass asks what shape it has, without pretending to
decode it.

**The built-ins are all zero across 141-191**, confirming §14's control: what
the machine authors itself puts nothing here.

Six library samples, one multisample, identified by `SPITCH` (their names are
a commercial library's and are not recorded):

```
SPITCH  168 169 170 171 172 173 174 175 176 177 178 179 180 181 182..187 188 189 190 191
   43     0   0   0   0   8   0   0   0 255 255 255 255 251 239 all 255  239 175 255 255
   51     0   0   0 255 215 223 255 255   0   0   0   0   1  80 all   0    4  68   0   0
   55     0   0   0 255 223 223 255 255   0   0   0   0   0  80 all   0    4  68   0   0
   63     0   0   0 255 223 223 255 255   0   0   0   0   0  80 all   0    4  68   0   0
   71     0   0   0 255 215 223 255 255   0   0   0   0   0  80 all   0    4  68   0   0
   84     0   0   0   0 130   8   0   0 255 255 255 255 235 191 all 255  254 191 255 255
```

### Three things the bytes say on their own

**It is signed, and two samples are negative.** Positions holding `0xFF` in
one group hold `0x00` in the other, which is what sign extension looks like.
Bytes 182-187 read **exactly 0 or exactly -1** across all six — a six-byte
run that is only ever all-zero or all-ones is sign extension, not data.

**Byte 188-191 as signed little-endian is identical for four of the six**:
`17,412` for `SPITCH` 51, 55, 63 and 71, against `-20,497` and `-16,386` for
43 and 84. A value constant across most samples and different at the extremes
looks like a default with the outliers carrying something real.

**The split tracks pitch, and specifically the extremes.** The two samples
that differ are the lowest and highest of the multisample. That is a
suggestive correlation and nothing more: six samples from one library, and
"outermost of a multisample" and "negative value" cannot be separated on this
evidence.

### What this deliberately does not claim

No decoding. The candidate interpretations — 16- and 32-bit, signed and
unsigned, both endiannesses — were enumerated and none produced sensible
numbers across both groups except the sign-extension reading above, which
identifies *structure* rather than *meaning*.

Six samples from one multisample on one disc cannot distinguish a per-sample
field from a per-volume stamp, nor a library convention from a format rule.
**mpc2emu has a corpus derived from real media**; this is the shape to match
against it, and the question for them is whether their headers show the same
0/-1 six-byte run and the same 17,412 default. Raised in the handoff.

### §83a — The corpus that would have answered this no longer exists (2026-08-13)

mpc2emu's sample header was derived from real media rather than from the
document, which made them the right place to send §83's three questions. They
searched properly and the material is gone: everything reachable on their
side is test fixtures, the largest collection 28 files. Their format notes
cite a reference "corrected against 40 real library discs" and 54,488 named
zones, so it existed when those figures were taken — most likely physical
CD-ROMs read once, with only the derived numbers retained.

Two consequences, and the second is the one worth carrying:

**§83 stays open, blocked on media rather than on analysis.** A second
library would answer it. Six samples of one multisample cannot separate a
per-sample field from a per-volume stamp.

**Their 4.43%/97.13% figures are historical.** They offered them to this
project as a self-check on the dead-zone classifier, and told me plainly they
cannot re-derive them. So a future result disagreeing with those numbers
cannot be adjudicated by re-measuring. §80 now records them as provenance
rather than as a repeatable check, and keeps the qualitative version, which
follows from what a disabled zone *is* and needs no corpus at all.

They also offer a control this project should be honest about declining to
lean on: their writer emits zero across 141-191, so an mpc2emu-written file
is a negative control. **A negative control made of zeros cannot distinguish
"correctly read as empty" from "not read at all"** — their words, and right.
It would pass just as well against a reader that never issued the request.

### The transferable part of how the handoff corruption was found

I described mpc2emu's watcher as the mechanical advantage I lack. They
corrected it, and the correction is the useful part:

> The watcher did not detect the corruption. It reported "handoff file
> updated". What made it worth chasing was that the newest SECTION HEADING
> had not changed — an update with no new pass. Two facts reported
> separately, and the anomaly lived in their disagreement.

So the lesson is not "run a watcher". It is **report the components
separately rather than collapsing them into one status**, because the
informative event was neither component alone. A watcher that said "s3ked
changed" would have led them to the newest pass, which they had already read,
and they would have moved on.

That generalises past monitoring. `select_mode` returns what the register
reads *and* the caller compares it against what was asked, precisely because
collapsing those into "success" is what the device's own acknowledgement
does — and that acknowledgement is wrong in both directions on this machine
(§76). The same shape, arrived at from a different direction.

## §84 — All eleven main-menu pages, named at the panel (2026-08-13)

**Status: settled.** Read off the LCD by a person while the register stepped.
No eyes-free route exists — §78 killed the only candidate when `RMULTIDATA`
turned out to answer in every mode.

| value | page | | value | page |
|---|---|---|---|---|
| 0 | SINGLE | | 6 | EFFECTS |
| 1 | SINGLE + EDIT | | 7 | EFFECTS + EDIT |
| 2 | MULTI | | 8 | GLOBAL |
| 3 | MULTI + EDIT | | 9 | SAVE |
| 4 | SAMPLE | | 10 | LOAD |
| 5 | SAMPLE + EDIT | | | |

### EDIT is a modifier lamp, and that is what makes the count work

The document's "eleven modes available from the eight mode keys" had been
quoted in this project three times without anyone being able to make eleven
and eight relate. The panel settles it: **EDIT is not a page.** The eight
buttons are SINGLE, MULTI, SAMPLE, EFFECTS, EDIT, GLOBAL, SAVE, LOAD — seven
modes and one modifier, and the modifier lights *alongside* four of them.

```
7 modes + (EDIT × 4 of them) = 11
```

Jan supplied that: value 1 showed **both** SINGLE and EDIT lit, and the
correction — "EDIT is additive to what is selected" — is what turned a
sequence of names into a structure.

### The prediction, and why it was worth writing down

After value 1 came back as SINGLE + EDIT, the pairing was predicted in full
before measuring anything else: 2 MULTI, 3 MULTI+EDIT, 4 SAMPLE, 5
SAMPLE+EDIT, 6 EFFECTS, 7 EFFECTS+EDIT, 9 SAVE. **Every one held.**

That is worth more than the table. A guessed mapping that happens to be right
is indistinguishable from a wrong one until it is tested; a mapping predicted
from a structure and then confirmed at seven independent points is a claim
about how the machine is organised. It also retro-explains the two facts that
made the enumeration look arbitrary — GLOBAL at 8 where its button position
would be 5, and the gaps — as base/edit pairs consuming 0-7.

### A revision to §78

§78 attributed the SAVE-page wedge to arriving there with `byte[0]` on FLOPPY
and no disc in the drive. Value 9 was set again here with HARDDISK selected
and 30 volumes visible, and **the machine stayed responsive** — same value,
same register, different device selector. That is the §78 reading confirmed
rather than assumed: the hazard is the page having nothing to read, not the
value.

Value 11 remains untested and still costs a power cycle (§79). Whether it is
past the end or a twelfth page that cannot initialise is now the only open
question about this register — and with all eleven documented modes accounted
for, "past the end" is the strong favourite.

### Method note

Stepping the register on a timer and asking afterwards did not work: the
values were printed to a terminal the reader was not looking at, because the
reader was looking at the sampler. Setting one value, asking, waiting, then
setting the next cost eleven exchanges and produced eleven unambiguous
answers. **Where a person is the instrument, their attention is the
bottleneck and the loop has to run at their pace, not the bus's.**

## §85 — Value 11 crashes the firmware, and the LCD is what proved it (2026-08-13)

**Status: settled.** Cost one power cycle, which was the expected price.

§79 established that writing 11 to `byte[91]` stops the machine answering, and
left open whether that is because 11 is past the end of the enumeration or
because it is a real twelfth page that cannot initialise. **Over the wire the
two are identical**: both are silence. §84 having named all eleven documented
modes made "past the end" the favourite, but a favourite is not a finding.

The display separates them, and it did so unambiguously. With HARDDISK
selected and 30 volumes visible — so §78's empty-media confound was
definitely absent — writing 11 left the LCD **flooded, and still flooding,
with a repeating `0054`**.

The machine is in a runaway loop writing to its own screen. It is also frozen
to its **front panel** — no button or dial does anything — so this is a total
firmware crash and not a MIDI-side hang.

**What that does NOT establish is which of the two hypotheses is right, and
the first version of this entry claimed it did.** It argued that a page
failing to initialise would leave the display static or half-drawn rather
than flooding, so 11 could not be a page. Jan pointed out the hole: an
S3000XL takes expansion boards (the IB304F filter/effects board among them),
and **a page whose initialisation touches hardware that is not fitted would
crash exactly like an out-of-range index does.** A crash is a crash; the
display tells you the firmware died, not what it was reaching for.

So the honest position is narrower than the one first written here:

* **Established.** Writing 11 crashes the firmware completely — no MIDI
  reply, no front panel, a flooding display, recoverable only by power
  cycling. The guard in `select_mode` is therefore load-bearing.
* **Not established.** Whether 11 is past the end of the enumeration or a
  real page for hardware this machine does not have. §84 accounts for all
  eleven modes the *document* describes, which favours "past the end" — but
  the document describes a base machine, and an expansion board is exactly
  the kind of thing it would not enumerate.

Settling it needs a machine with the board fitted, which is a hardware
question rather than a protocol one.

A second detail, not captured in §79: **the write itself got no reply.** Not
an error code, not an OK — a timeout. So the firmware died *inside* the write
handler rather than moving to a page and then failing to talk. Together with
the flooding display that makes the sequence unambiguous: the write is
executed, it goes somewhere it should not, and the machine never returns.

`0054` is recorded verbatim and not interpreted. It could be an error code, an
address, a character pattern, or whatever happened to be in a register — six
samples of one repeating value on one crash is not evidence for any of those,
and guessing would be exactly the kind of decoration this project keeps
retracting.

### What this makes of the range check

`select_mode` refuses anything outside 0-10, and §79 already noted that the
check is unusual in this project: elsewhere a range guard duplicates a refusal
the device would have made anyway and mainly improves the error message. Here
there is nothing to duplicate. The measurement upgrades that from "the device
does not refuse" to **"the device crashes"**, which makes the guard the only
thing standing between a caller's typo and a power cycle.

Worth stating plainly because it inverts the usual reasoning about defensive
checks: this one is not belt-and-braces over a device that would have coped.

### The overclaim, and how it happened

The first draft of this entry reasoned from the *manner* of the failure to
its *cause*: flooding rather than static, therefore not a page. That is a
real inference and it is not a sound one — it rules out "a page that drew
and then failed to talk", which was never the interesting hypothesis, while
saying nothing about "a page that crashed on init". The alternative was
supplied by the person holding the machine, who knew it takes expansion
boards, within a minute of the entry being written.

Three sessions of this project have now produced the same error: reaching a
conclusion by eliminating the alternatives that came to mind. §74's `2 = CLR`
came from a two-item list. §80's sample-name uniqueness came from
generalising a program-level finding. Both were wrong for the same reason
this was — the alternatives that come to mind are the ones you already have
the vocabulary for, and hardware you have never seen is not in that
vocabulary.

### The general shape, which is the transferable part

Two sessions of wire-level probing could not distinguish these two
hypotheses, because the observable they differ in is not on the wire. **A
protocol has no opinion about a device that has stopped implementing it.**
Ten seconds of somebody looking at the front panel settled what no amount of
further SysEx could have.

That is the same lesson as §84's method note from the other direction: there
the person was the instrument and had to set the pace; here the person was
the only instrument that could see the relevant channel at all.
