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
2. **Writes are acknowledged.** `REPLY` (`16h`) returns `0` = ok, `1` = error.
   eosed must read a value back to know whether a write landed.

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

## §6 — The SysEx send gap is a guess (open)

`s3k.bridge.SEND_GAP` is **0.05 s and unverified**. It is labelled as such at
the point of use.

What is *not* transferable: k2kremote's 120 ms floor was reverse-engineered
against a Kurzweil K2000, whose CPU garbles its LCD under a MIDI flood. That
is a Kurzweil finding. Nothing equivalent is known for this family, and
vintage samplers are entirely capable of hanging when flooded.

`ThrottledOut` already separates the read gap from the write gap, so the two
can be tuned independently once there is evidence — reads are paced by their
own round trip and can usually be cut, writes are fire-and-forget and are the
ones that need protecting.

**To close this:** with something unimportant loaded, walk the gap down
(50 → 25 → 10 → 5 ms) while reading a whole header in a loop, and watch for
garbled replies or a hung front panel. Then repeat for a write loop, which is
the riskier of the two.

**Blocked on:** hardware.

---

## §7 — Autodetect has no broadcast address to lean on (designed, never run live)

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

**To close this:** confirm a real machine answers `RSTAT` on channel 0 out of
the box, and time one full sweep.

**Blocked on:** hardware. The multi-machine paths are additionally
synthetic-only — no two real samplers have ever been connected at once.

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
