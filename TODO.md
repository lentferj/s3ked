<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# TODO

*What* is open. `docs/RESOLUTION_NOTES.md` tracks *how* to resolve each item.

## Status, 2026-08-08 (first session, second pass)

The project exists and is complete as a piece of software: protocol codec,
parameter tables, transport, CLI and TUI, **216 tests, all passing, all
synthetic**. No hardware was available, and the plan was built so that none
was needed — every phase is exercised through fakes and `--demo`.

What that means honestly: *the code agrees with the specification as
transcribed.* Nothing more. The specification is a third-party hand
transcription of a printed Akai document, so the single largest open item is
not a feature — it is that **no byte offset in this project has ever been
confirmed against a real machine.**

The one thing worth knowing before anything else: the question that started
this project — "is there a k2kremote-style screen-mirror protocol for the
S3000XL?" — is **settled, and the answer is no**. That is written up in
RESOLUTION_NOTES §1 with its sources, precisely so nobody spends an evening
re-deriving it. The family has an editor/librarian protocol only; the panel
protocol Akai does have arrived a generation later and reads its screen over
USB, not MIDI.

A second pass over the source documents — prompted by re-reading the
S2000/S3000XL/S3200XL spec and the owner's manual — closed two items, fixed a
bug, and added a feature:

- **§3 resolved**: the keygroup 161/162 double definition is a model split, not
  a chronology, and the manual confirms the XL enumeration
  (PRG/OFF/FX1/FX2/RV3/RV4) outright.
- **§4 resolved, and a bug fixed**: the note-name octave offset was wrong. The
  S2800 spec's "A1 to G8" drops a minus sign; three sources agree on
  `// 12 - 2`, so note 21 is `A-1` and middle C is `C3`.
- **§8 added**: the multi-part structure and the program header agree on all
  twelve shared offsets, across two separately transcribed documents — the
  first independent check on §2's central worry.
- **Multi mode implemented** (opcodes `0x41`/`0x42`, 19 more fields).

In rough order of value, the open items are now: the missing miscellaneous
index table (§5), the two ranges hardware contradicted (§11 Finding E), and
`PDATA`/`KDATA`, which remain destructive-until-proven. The live offset diff
and the throttle floor are both closed.

Next time there is hardware, the two cheapest items are the `RSTAT`→`STAT`
round trip and reading one program header — perhaps ten minutes, and between
them they either validate or demolish the foundation everything else rests on.

---

## Live hardware verification — done, and here is what it changed (CLOSED 2026-08-12)

**Status:** closed. This section said "no part of this project has exchanged a
byte with a real sampler" until 2026-08-12, by which point it had been wrong
for weeks. Kept rather than deleted, because what it predicted is worth
comparing against what happened.

The seven steps it listed as the cheapest route in all passed: `RSTAT`
answered on exclusive channel 0, the name lists came back readable (validating
the 41-entry non-ASCII character set), and `PRNAME` decoded against the front
panel.

**What it did not predict is where the errors actually were.** It expected
offset errors in the transcription. Those were rare. The errors that mattered
were:

- **Ranges transcribed from the DISPLAY rather than the value**: six tuning
  fields declared 0..50 where the field holds -12800..12800, a factor of 256
  (§56).
- **A field's shape mis-modelled**: `TEMPER` is twelve independent bytes and
  was one integer, so writing it corrupted eleven semitones (§66).
- **Measurements wrong because the INSTRUMENT was wrong**, not the table: a
  filter law 20-30 % high from a spectral centroid (§54), five fields called
  inert that were one dead destination (§52), a threefold "depth-dependence"
  that was a saturating detector (§58).

Offsets were the thing this section worried about, and they were mostly right.
Ranges, shapes and instruments were the thing it did not mention, and they
were mostly wrong. Worth remembering the next time a plan lists what to check.

What remains unverified is now itemised per field rather than blanket: most of
~300 parameters have still never been written to a machine. See
`HW_PANEL_CHECKS.md` for what only a person at the LCD can settle.

## Keygroup offsets 161/162 — a model split (RESOLVED)

The two definitions are the base S2800/S3000/S3200 one and the
S2000/S3000XL/S3200XL one, under the spec's own model heading. The S3000XL
owner's manual addendum confirms the XL enumeration in words: default **PRG**,
then OFF, FX1, FX2, RV3, RV4. `s3k/params.py` uses `KFXCHAN`/`KFXSLEV`, correct
for the machines this project targets.

Residual, recorded rather than handled: on a plain S2800/S3000/S3200 the
earlier five-value enumeration applies and this project would read one value
high. RESOLUTION_NOTES §3.

---

## Note-name octave numbering (RESOLVED — was a bug)

`note_name` used `value // 12`, rendering note 21 as `A1`. Wrong. Three sources
agree on `value // 12 - 2`: the S2000/S3000XL spec writes "A-1 to G8", the
owner's manual shows panel keyspans as `C_0` … `G_8`, and the S2800 spec's
"A1" is simply a dropped minus sign. Fixed. RESOLUTION_NOTES §4.

---

## The SysEx send gap, measured (CLOSED 2026-08-10)

**Status:** walked down against an S3000XL with `probes/throttle.py`. The two
gaps turned out to be different numbers and only one of them matters.

- **`SEND_GAP` 0.05 -> 0.010.** Requests are self-pacing (each blocks for its
  reply); reads ran clean to a 0 ms gap and saturate at ~94/s, a 10.6 ms round
  trip. Any gap under that is free, because the gap is owed *after* a send and
  overlaps the wait.
- **`WRITE_GAP` is new, at 0.075**, and no longer inherits `gap`. It is the
  only gap that bites, and **the old guess was wrong in the dangerous
  direction**: a 150-write fire-and-forget burst lost 36 writes at 50 ms and
  none at 75 ms, silently. 40 writes at 50 ms passed only by fitting in the
  buffer.
- **Fire-and-forget buys nothing here.** The machine consumes writes at
  13.3/s either way, so a safely-paced unacknowledged burst is exactly as fast
  as an acknowledged one. Leave `confirm=True`.

Method note for anyone repeating it: whole-header reads cannot find a floor,
being wire-bound at ~128 ms. Use single-parameter frames. RESOLUTION_NOTES §6.

---

## The miscellaneous data-index table is missing from the source (OPEN)

**Status:** the generic `RMISCDATA`/`MISCDATA` path is understood and the
message shape is implemented, but no table of index values exists in the
transcription.

The named cost is `BTSORT`: the spec says twice that it should be triggered
after writing `PRGNUM`, and never says with what index, so s3ked cannot offer
`trigger_btsort()`.

That cost is now measured and is smaller than it read. §92 found the machine
does BTSORT's *flagging* half by itself for a SysEx write and only its
*sorting* half is missing — and the sort is a no-op for the one write s3ked
makes, which assigns numbers in list order. The index is still worth finding;
nothing currently depends on it.

**To close this:** find a fuller copy of the Akai document, or capture what
MESA sends when it renumbers a program.

**Blocked on:** a better source, or a MIDI capture. RESOLUTION_NOTES §5.

---

## The load type: register, names and execution (RESOLVED 2026-08-14)

**Status:** the register is **bytes 6-9**, the same ones this project had
written off as a bare trigger. They track the panel's LOAD page selection,
measured through five values as a person stepped it (§93). `load_type()`
reads it.

All eight values are named, read off the panel — `ENTIRE VOLUME`,
`ALL PROGS+SAMPLES`, `programs only`, `all samples`, `Cursor Prog+Samps`,
`Cursor Item only`, `Operating System`, `Multi+progs+Samps` — in
`s3k.messages.LOAD_TYPES`. `load_type_name()` renders it.

Settled and not re-openable: only type 1 is triggerable, because triggering
is writing 1 and writing 1 sets the type — so every load s3ked fires is
`ALL PROGS+SAMPLES` and overwrites the panel's selection as it goes.

**Closed 2026-08-14, and it went further than the question asked.** Writing
the register does not merely move the panel — it **performs the load type
written**, measured at two settings with predictions recorded first (§94).
So all eight types are remotely performable and the load screen offers them.

§74's "only value 1 acts" is retracted. Its sweep ran against an
already-resident volume, so every type reloaded what was in memory and netted
zero words — the useless experiment §73 had thrown out one section earlier.

---

## Does a `PRGNUM` write over SysEx re-sort and re-flag by itself? (RESOLVED 2026-08-14)

**Status: settled, and the answer is half each.** Measured with
`probes/btsort.py`: the machine **reflags** on its own — the panel's "now
active" count followed a SysEx write immediately, 1 to 2 and back on restore
— and it does **not** re-sort; `RPLIST` came back in the same order with the
renumbered program still last.

The missing half turns out to be the half `renumber_programs()` does not
need. It assigns numbers in list order, so the list is already in
program-number order when it finishes and the sort has nothing to do. The
caveat that was drafted for it was dropped rather than shipped.

A caller writing `PRGNUM` **out of** list order does not get that for free.

RESOLUTION_NOTES §92.

---

## Autodetect: no broadcast address exists in this protocol (WORKS ON HARDWARE)

**Status:** confirmed live 2026-08-10 — found a real S3000XL on the first
attempt, on channel 0 out of the box. Full cold sweep 40.3 s across 37 output
ports; cached-pair fast path 1.1 s afterwards. No false positive from the
host's Midi Through or virtual-MIDI ports.

Because there is no broadcast address, discovery sweeps ports at one exclusive
channel (0 by default). A machine set to another channel will not be found
without widening `channels=`, and the error message says so.

**Still open:** the two-machine `AmbiguousDevice` refusal and the widened
`channels=` sweep are both still synthetic-only.

**Blocked on:** a second sampler. RESOLUTION_NOTES §7.

---

## Write path verified across two programs (CLOSED 2026-08-10)

**Status:** `probes/roundtrip.py` swept every safe parameter with
read -> write A -> read -> write B -> read -> write A -> read -> restore ->
read. Two runs: 183/183 on program 0, then 271/271 on program 1 and both its
keygroups, 1808 writes total at a 0.1 s gap. No dropped replies, no device
error, nothing left unrestored, and every structure the sweep was not
addressing came back byte-identical. Keygroup selectors address
independently.

Four sample-header fields turned out to be machine-managed -- `SLOOPS`,
`SALOOP`, `SHLOOP`, `SSPARE` -- and are now `readonly=True`. They exposed the
rule that matters: **a REPLY/ok means the message was accepted, not that the
value landed.** Verification needs REPLY *and* a read-back at a valid
address. RESOLUTION_NOTES §12, §12a.

**Name fields settled too** (RESOLUTION_NOTES §13): all 41 characters
round-trip, `RPLIST`/`RSLIST` follow a rename, space-padding confirmed. Note
`SNAME1`-`SNAME4` are *references* to samples, not labels -- a UI should offer
a list of resident samples there, never a free text box.

**Delete-on-duplicate-name does NOT extend to byte-offset writes** (§13a),
tested deliberately: two programs held the same name, nothing was deleted,
block count unchanged. So renaming is safe and the machine enforces no name
uniqueness -- address programs by index, never by name.

**Still untested:** every whole-structure operation (`PDATA`/`KDATA`), which
stays destructive-until-proven. §13a is not clearance for those.

---

## Filter calibration: FILFRQ mapped to hertz (CLOSED 2026-08-11, REDONE §54)

**The law recorded here was replaced on 2026-08-12.** It was fitted by
inverting a spectral centroid and read 20-30 % high by an amount that grew
with frequency. The corner is now measured from the resonance peak:
`Hz = 6.4597 * exp(0.07100 * FILFRQ)`, 44..92, r2 0.99984. The account below
is the original one and is left for the faults it records, which were real.

**Status:** first sweep attempted 2026-08-10 and produced no curve. Three
faults, all ours: `jack_rec` never exited so the sweep blocked; the restore
did not run because `finally` does not survive SIGTERM; and the runtime
estimate counts only recording time. All three fixed.

**Blocking now:** the JACK server is wedged — `jackd` alive but refusing new
clients, after an orphaned recorder. Clearing it means restarting the user's
whole audio graph, which is his call. MIDI is unaffected (ALSA, not JACK) and
the sampler is fine; program 0 was restored from the captured header dumps.

**Open regardless of the rig:** whether the 50-100 Hz reference band contains
any source energy at all. A sawtooth has none below its fundamental, and a
256-word single cycle sounds at ~172 Hz. If the band is noise, every curve
fitted through it is meaningless however good its r². Raised by mpc2emu.

**Now measured (§17):** the audio path is back and the rig works -- 0.39%
repeatability, ref_flat_db passes, restores 23/23. But `FILFRQ` moves the
corner by a factor of 1.00 and the level by 1.30 dB across its whole 0..99
range. It is not the filter, or the filter is not engaged.

**Retracted 2026-08-10 (§18):** all eleven resident programs are on MIDI
channel 1, so every note sounded program 0 buried under ten library programs.
§17's "FILFRQ does nothing" is void -- program 0 was inaudible. §16a/§16b's
spectral analysis described the library programs, not our SAWTOOTH.

**Now measured (§19):** with program 0 isolated on its own MIDI channel and
`verify_isolation` confirming 55.9 dB, `FILFRQ` sweeps 47.5 dB of level from 0
to 50 and then 945->1813 Hz of brightness from 55 to 85. Keygroup offset 7 is
the basic filter frequency, confirmed. Filter 2 showed nothing because it needs
the optional IB304F board.

**Done (§20), then REPLACED (§54):** the centroid-derived
`Hz = 6.998 * exp(0.07384 * FILFRQ)` read 20-30% high and its error grew with
frequency, because a spectral centroid is an average of everything the source
contains rather than the corner. Now measured from the resonance peak:
`Hz = 6.4597 * exp(0.07100 * FILFRQ)`, 44..92, r2 0.99984, one octave per 9.76
units.

The JACK wedging is also fixed: the recorder is now an in-process JACK client
rather than a `jack_rec` per capture, and ran 19 consecutive captures with no
wedge where the ceiling was eight.

**Seven of eight sweeps measured** (§20, §21, §22): filter, tuning, loudness,
pan, amp-attack, amp-decay, amp-release. Pan turned out to be a constant-power
law rather than either shape the harness fits.

**All eight sweeps are measured** (§24). `lfo-rate` is `Hz = 0.11867 * LFORAT`,
linear, r2 0.9995.

**LFO1's destination is now measured rather than inferred (§25):** it drives
**pitch**. Tracking pitch, level, brightness and balance over a held note, only
pitch oscillates at the LFO rate -- 68-86x prominence against a control -- and
it reproduces the §24 rates within a few percent through a completely different
signal path. No panel visit was ever needed.

**Also open:** the envelope measurements floor at low parameter values
(`envelope()` hop resolution) and the release sweep NaNs above 70 because the
release outlasts the 2 s recording tail. Both are instrument limits, both
recorded in §22, neither yet fixed.

---

## All three envelopes measured (CLOSED 2026-08-12)

Envelopes 1, 2 and 3 are all measured, and both filter envelopes turned out to
be four-stage rate/level structures rather than ADSRs (§67, §57).

The ADSR-flavoured names on envelope 2 are R1, R3, L3 and R4 of that
structure; `ENV2L1`/`ENV2R2`/`ENV2L2`/`ENV2L4` are the missing L1, R2, L2 and
L4. Envelope 3 was found the same way a section earlier and its names are
unfamiliar enough to invite reading -- envelope 2's hid behind ATTAK/DECAY/
SUSTAIN/RELEASE, and stayed hidden two attempts longer for it.

Stages group by TYPE and not by envelope: everything that rises runs at one
rate and everything that falls at about half of it. See RESOLUTION_NOTES
§57-§64 and §67.

## Model selection: comparing candidates cannot say "none of these" (OPEN)

The harness picks the best of the shapes it is offered, which is the least
wrong one, not necessarily a right one. §36 caught it: four candidates were
fitted to a clipped velocity curve, all fitted badly, and the most curved won
by bending toward the flat top. The true shape -- piecewise linear -- was not
among them.

Partly mitigated. `beyond_noise()` has an explicit undecidable branch, and
several probes now report residual halves so a systematic tilt shows up. What
is still missing is a general "the residuals are not random" test applied to
every fit rather than to the ones somebody remembered to check.

**The rule that catches it by hand:** look at where the residuals sit, not
only how large they are. A fit at r2 0.96 whose errors all lie at one end is a
shape mismatch wearing a good score, and a fit that is equally mediocre at
every point is bias rather than noise (§29).

## Sample header bytes 171-191 are undescribed (OPEN, narrowed 2026-08-14)

**Status:** our table stops at offset 141 of the sample header. On real
library samples, bytes 171-191 carry consistent non-zero structure; on
machine-authored samples the whole tail is zero. Not text — the values fall
outside the 41-character set. 21 bytes of a structure s3ked both reads and
writes have no description at all.

By contrast **program bytes 115-191 are zero across all eleven resident
programs**, so that tail really does look like padding.

**Characterised 2026-08-13 (§83), not decoded.** The region is signed: bytes
182-187 read exactly 0 or exactly -1 across six library samples, which is
sign extension rather than data, and 188-191 as signed little-endian is
identical at 17,412 for four of six with the two extremes of the multisample
negative. The built-ins are all zero across 141-191, confirming the control.

**Asked mpc2emu and the answer is that they cannot help.** Their sample
header was corrected against 40 real library discs, and that material is no
longer on their disk — most likely physical CD-ROMs read once with only the
derived numbers kept. They can offer one control and they rate it themselves
as weak: their writer emits zero across the whole range, so an
mpc2emu-written file is a negative — but a negative made of zeros cannot
distinguish "correctly read as empty" from "not read at all".

**Blocked on:** more real media, from any source. Six samples of one
multisample on one disc cannot separate a per-sample field from a per-volume
stamp, nor a library convention from a format rule. A second library would
answer it. RESOLUTION_NOTES §14, §83.

---

## The extended layer does not bounds-check reads, and fails silently (RESOLVED client-side)

**Status:** found 2026-08-10 by `probes/conformance.py`. An out-of-range
`RPHEADER`/`RKHEADER`/`RSHEADER` — bad program number, bad keygroup, offset
past the header, count up to 1024 — returns **the previous valid read's
buffer** instead of the documented REPLY/error. The S1000 whole-block
operations do bounds-check correctly.

Consequences: a wrong offset cannot be detected by reading; read-back
verification of a write can confirm something that never happened; callers
must bounds-check locally against PLIST/SLIST/GROUPS.

**Closed 2026-08-13 by refusing client-side.** `S3kBridge._check_bounds`
rejects the request before it is sent, for reads and writes alike. The device
is unchanged and still behaves this way; nothing here fixes the machine.

Re-measured before writing the guard, and it is worse than this entry said:
the block-identifier check does **not** catch a bad index even at offset 0,
because an out-of-range program read answers with a *program* block and the
identifier is therefore correct. It only ever caught cross-region confusion.

**Still open:** `bounds_check=False` exists for probes that need to ask the
device what it really does, and the counts are cached, so a structural change
made at the front panel can leave the cache stale. A stale count produces a
refusal naming the counts it used, never a silent wrong answer.
RESOLUTION_NOTES §11, §82.

---

## Two parameter ranges contradicted by hardware (narrowed 2026-08-10)

**Done:** the sentinel widenings are applied. `OUTPUT` and `KGMUTE` now run
`0..255` with `255 = off`; `LDWELL1`-`LDWELL4` run `0..9999` with `0 = no
loop` and `9999 = hold`. Each sentinel was stated in that parameter's own
`notes` and merely missing from its range. `LDWELL1` was widened too although
the sweep never flagged it — its loop happened to be in use. **A sentinel is
only visible when a field is set to it**, so the fix came from the notes, not
from the findings.

`POLYPH` was deliberately **not** widened: its notes name 32, but that is the
*displayed* value for a stored 31, not a storable one.

**Still open:** two genuine transcription defects, both from a source that
gave "a fixed value" where a range belongs — `VZONES` reads 4 against `0..0`,
`COHERE` reads 0 against `1..1`. Around twenty other fields carry the same
`fixed value in the specification` placeholder and are equally suspect; these
two are simply the ones caught reading something else.

**To close this:** re-derive VZONES and COHERE from a better source, and treat
every `0..0` range as unverified rather than as a constraint.

**Blocked on:** a better copy of the document. RESOLUTION_NOTES §11 Finding E.

---

## STAT's "software version" field does not match the panel (OPEN, hardware finding)

**Status:** the S1000 document's `vv,VV → VV.vv` decodes to 17.00 on a machine
whose panel reports OS 2.00. Every other field in the same reply is correct, so
it is not a misalignment. No reading of the two bytes yields 2.00.

The field is no longer printed anywhere. It is still decoded per the document.

**To close this:** the same probe against a second machine in the family —
ideally on a different OS, or an S1000, where the document may be accurate and
this an XL divergence.

**Blocked on:** a second machine. The documents are exhausted.
RESOLUTION_NOTES §10.

---

## Multi mode — implemented, unverified (BUILT, never run live)

Opcodes `0x41`/`0x42` and both structures (`multi` file header, `multipart`)
are implemented, with 19 transcribed fields. They reuse the same 12-byte
extended header as the S3000 block, distinguished by the selector byte
(0 = file header, 1 = part), which `_REGION_SELECTOR` fixes per region so a
caller cannot pass the wrong one.

Multi mode exists only on the S2000/S3000XL/S3200XL; every field carries
`models="S2000/S3000XL/S3200XL"`.

Not yet surfaced in the TUI — the CLI reaches it via `s3kcli header multipart 3`.
A Multi pane should wait until there is reason to trust the offsets.

**To close this:** read a multi part off a real XL and diff it.

**Blocked on:** hardware.

---

## Sample data transfer — not implemented (OPEN, by choice)

`RSPACK` / `ASPACK` / `CASPACK` (`0x0C`, `0x0D`, `0x1D`) move sample *audio*
over MIDI. Not implemented, and not currently planned: at 31250 baud this is
famously slow, existing tools do it well, and SCSI is the sane path for bulk
audio on this family.

The header side of samples (names, loop points, rates) *is* implemented — that
is the part an editor needs.

**To close this:** decide whether it is wanted at all before writing any of it.

---

## Whole-header PDATA/KDATA writes — deliberately not exposed (OPEN, by choice)

`PDATA`/`KDATA`/`SDATA` can create or replace a whole structure in one
message. They are in `s3k.messages.DESTRUCTIVE_ON_WRITE` and no bridge method
sends them, for a specific reason: the spec states that writing a program
whose *name* matches an existing one **deletes that existing program first**.
That is a destructive side effect the caller never asked for.

The byte-offset writes (`0x27`–`0x38`) do everything an editor needs without
it.

**To close this:** if whole-structure restore is ever wanted (a librarian
feature), it needs its own arm-then-fire flow and a name-collision check
first.

---

## Panel/screen-mirror protocol — closed, with findings (RESOLVED)

**Do not reopen this for the S1000/S3000 family.** There is no display read,
no button injection and no panel echo in the documented command set, verified
against the Akai scan itself. RESOLUTION_NOTES §1 has the evidence and the
sources.

If a Z4/Z8/S5000/S6000/MPC4000 ever turns up, that *is* the machine for a
k2kremote-style mirror — but its screen read is a USB bulk transfer, not
SysEx, so it would be a different project with a different transport. §1
records the opcodes and the aksy prior art.

---

## Parameter scales -- what a value MEANS (RESOLVED for 19 fields)

**Status: 35 laws measured on an S3000XL and shipped in `s3k/scales.py`,
none provisional.**
The editor now shows `FILFRQ 80 (~2.57 kHz)` and accepts `set FILFRQ 500Hz`.
Remaining fields are listed at the bottom; the tooling to do them is built and
proven, so each is a bench session rather than a research problem.

The tables carry each parameter's *range* and none of its *meaning*. `FILFRQ`
is "basic filter frequency, 0 to 99" -- not one word about which hertz. Two
consumers wanted the answer: this editor, and any converter writing Akai
programs -- the sibling mpc2emu builds S1000/S3000 programs and disk images
and had to guess how a cutoff in hertz becomes a 0-99 integer.

Measured (see RESOLUTION_NOTES §20-§26 for each sweep and its bounds):

| field | law | measured over | r2 |
|---|---|---|---|
| `FILFRQ` | `6.4597 * exp(0.07100 v)` Hz | 44..92, 99 = wide open | 0.99984 |
| `FILQ` | `-20 log10(1 - v/15.84)` dB | 0..15, all values | 0.999975 |
| `LFODEL` | `0.06905 v / (103.41 - v)` s | 0..99, all values | 0.999555 |
| `KGTUNO`, `PTUNO` | `0.391667 v` cents | 0..50 | 0.9998 |
| `PRLOUD` | `0.642719 (v - 99)` dB | 0..99 | 0.9933 |
| `SUSTN1` | `0.60832 (v - 99)` dB | 10..99 | 0.99995 |
| `LFORAT` | `0.11867 v - 0.04` Hz | 10..99 | 0.9995 |
| `ATTAK1` | `0.000150326 * exp(0.11175 v)` s (a real rise time) | 55..90 | 0.99991 |
| `DECAY1` | `23525.6 * exp(-0.09776 v)` dB/s | 45..85 | 0.99998 |
| `RELSE1` | `22055.3 * exp(-0.09683 v)` dB/s | 55..70 | 0.99956 |
| `ATTAK2` | `0.00115864 * exp(0.09850 v)` s | 55..85 | 0.99967 |
| `DECAY2` | `25200 * exp(-0.09796 v)` octaves/s | 50..80 | 0.99995 |
| `RELSE2` | `61190 * exp(-0.10123 v)` octaves/s | 58..76 | 0.99977 |
| `SUSTN2` | `FILFRQ shift = 0.024645 * SUSTN2 * MODVFILT1` | 0..70 | 0.9908 |
| `PANPOS` | constant-power, `1.2124 * 20log10(tan t) - 0.54` dB | -45..45 | 0.9996 |

Four things these numbers taught that no document says:

1. **`SUSTN1` is linear in decibels, not in amplitude.** A half-amplitude
   sustain is 89, not 50. A converter writing `sustain * 99` is wrong by
   30 dB in the middle of its range, and it never looks wrong.
2. **`KGTUNO` is 1/256 of a semitone per unit**, not one cent per unit.
3. **A decay value is a slew RATE, not a duration** -- so its time depends on
   how far the stage travels (`time = span / rate`). Confirmed by holding the
   value fixed and varying the distance: the rate held to 0.27% (amplitude)
   and 1.9% (filter) while the duration moved 19% and 28%. A converter that
   writes a decay from a target time without knowing the sustain level is
   wrong by the ratio of the spans. `DECAY1`, `RELSE1` and `DECAY2` share an
   exponent near 0.0977; the **attacks fit neither model and stay open**.
4. **Both sustains are linear in the log domain, each in its own.** `SUSTN1`
   is linear in decibels; `SUSTN2` is linear in FILFRQ units, which is to say
   in octaves. Same name, same range, different domain.

**A fitted range is not the usable range, and its two ends are not
symmetrical.** `s3k/scales.py` marks an extrapolated conversion with `?` and
records separately those endpoints that are independently known -- `FILFRQ` 99
is *measured* wide open even though the curve stops at 90. This is not
pedantry: mpc2emu clamped "fully open" to the top of the fitted range and made
every converted program audibly darker than before the calibration existed.
Caution made the output worse.

**The prediction that `ATTAK1`/`DECAY1` would move onto 0.098 was tested and
the test overturned something bigger** -- see §29/§30. `DECAY1` did land on
0.09776, but the exponential model underneath the whole table was wrong: every
stage is a straight ramp in the log domain. §28's "one time-constant law" is
retracted, along with its argument that two detectors sharing no code made it
independent confirmation (they rule out detector error, not model error).

**The attack shapes are settled (§31), and they differ.** `ATTAK1` is a
linear ramp in *amplitude* (r2 0.9982 against 0.9343 for linear-in-dB), which
makes it a genuine rise time -- the amplitude attack always travels zero to
peak, a fixed distance. `ATTAK2` is a linear ramp in *octaves* (0.9980 against
0.9124 for hertz). Only the amplitude attack is in the linear domain, and it
must be: a ramp linear in dB from silence starts at minus infinity.

**`ATTAK2` is a duration** (§32). Replicated three captures per depth: span
varied 41%, rise time 3.5%, and constant-rate is rejected at a between/within
ratio near 30. The earlier claim of a threefold depth effect is **withdrawn**
-- it compared an exponential time constant against a 10-90% rise time, two
different quantities carrying the same unit.

**Closed (§58).** `ATTAK2` was provisional over a threefold disagreement
between `MODVFILT1` 18 and 25, recorded as depth-dependence. It was not
depth-dependence: read through a spectral centroid, a linear ramp through a
saturating corner gives a time that scales inversely with drive and fits
beautifully at the wrong answer. Re-measured with the resonance tracker at
two depths chosen so neither clips, the times agree and the law is
drive-independent. Nothing in `scales.py` is provisional now.

**The gap behind three inconclusive runs: no sweep had ever replicated.**
Every condition was measured once, so nothing carried an error bar. Three
captures per condition gave 0.65% within-condition scatter and settled the
question immediately. `probes/calibrate.py` now has `replicate()` and
`beyond_noise()`, the latter with an explicit undecidable branch.

**Retired:** the "one time-constant law" claim, in all its forms. Attack and
decay have different exponents (0.11175 vs ~0.0977) and are different kinds of
quantity. A test pins this because the idea has returned twice.

Still unmeasured: envelope 3, six assignable-matrix sources, and the ~20
fields the document lists as fixed placeholders.

See RESOLUTION_NOTES §20-§55.

### Added since, and worth reading before extending any of it

- **Only sources with an inherent centre pivot on 64** (§43, refuted and
  replaced by §44). `V_LOUD`, `V_ATT1` and `K_FREQ` pivot; `MWLDEP` and
  `PRSDEP` do **not** -- the wheel is proportional to its value, 0.50 of full
  depth at 64, r² 0.99989. The boundary is not arbitrary: velocity and note
  have an inherent centre, so bipolar modulation about the midpoint is
  meaningful, while a wheel rests at zero and pivoting there would mean a
  wheel at rest applying maximum negative modulation.
  `VELDEP` shows the distinction is about the **route**, not the source: it is
  driven by velocity, which pivots elsewhere, but as a *depth* control it
  scales from zero like the wheel.
  The prediction's other two names, `VFREQ1` and `VPANO1`, turned out not to
  be modulation at all (§46): the `V` is the velocity ZONE they belong to, so
  they are static offsets and the pivot question does not apply.
- **One inert DESTINATION, not five inert fields** (§52, retracting §39).
  All of `PANDEP`/`PANRAT`/`PANDEL`/`LFO2WAVE`/`LFO2TRIG` work -- LFO2 is
  assignable-matrix source 8 and runs at exactly twice LFO1 -- but LFO2 does
  not reach pan. `PANPOS` moves the image 118 dB, so neither end is broken,
  only the connection. The per-zone `VLOUD1` remains inert (§40).
- **Nothing is provisional** (§53 `FILQ`, §55 `LFODEL`, §58 `ATTAK2`'s
  depth-dependence, §59 `RELSE2`). Envelope 2 is fully re-measured off the
  spectral centroid.
- **Envelope stages group by TYPE, not by envelope** (§59, tested §60). Seven
  fields, two envelopes, two rates: `ATTAK2`/`RELSE2`/`ENV3R1`/`ENV3R4` all sit
  at 1.19-1.21 s for a full traverse at value 70, and `DECAY2`/`ENV3R2`/
  `ENV3R3` at 2.38-2.49. The last two were **predicted before measurement** --
  1% and 7% off -- which is the strongest confirmation in the calibration.
  Neither was settled by fitting harder; each needed a different measurement.
  The marking mechanism stays and is still tested against an injected scale,
  because the next half-answered measurement should be marked rather than
  rounded up into certainty.
- **`LFODEL` is a pure delay** (§55): `s = 0.06905 * v / (103.41 - v)`, r2
  0.999555 over the whole field. There is no fade-in -- two estimators that
  fail differently agree to 0.010 s -- so §31's suspected delay-plus-ramp
  conflation was not the problem. It runs to a pole past the field's top, the
  same shape as `FILQ`, with no evidence the two are connected.
- **`FILQ` is damping, and damping is linear** (§53). One number generates
  all sixteen steps: damping reaches zero at FILQ 15.84, just past the top of
  the field. Q runs 1.07 to ~20; the last three steps are worth more than the
  first ten together. Its corner sits 0.42 octaves below what the `FILFRQ`
  law predicts, replicating the same offset measured at a different `FILFRQ`
  -- **which means the `FILFRQ` law itself wants re-deriving from the
  resonance peak.** That is the next open measurement.
- **Untouched:** `ZPLAY1` and the velocity-crossfade fields.
- **Envelope 2 is four-stage rate/level** (§67), like envelope 3:
  `ATTAK2`/`DECAY2`/`SUSTN2`/`RELSE2` are R1/R3/L3/R4 and
  `ENV2L1`/`ENV2R2`/`ENV2L2`/`ENV2L4` are L1/R2/L2/L4. `ATTAK2` is a RATE --
  §28's "duration" reading is refuted -- so every stage of both envelopes
  takes `full_time * (distance / 99)`.
- **`TEMPER` is fixed** (§66). It is twelve independent signed bytes and the
  parameter model now supports arrays; a scalar is refused rather than
  broadcast, and both the CLI and the TUI take a comma-separated list.
- **Only matrix slot 1 modulates the filter** (§65). `MODSFILT2` and
  `MODSFILT3` move the corner by 0.00-0.01 octaves where slot 1 moves it 2.08,
  with the writes read back and verified. A converter should route everything
  through slot 1.
- **`bend` works as a source; the `!` variants are NOT inverted** (§65).
  `!modwheel` and `!bend` match their twins in sign and magnitude. Whether they
  differ in some way below the tracker's 0.19-octave quantum is open.
- **`external` (4) and `!external` (13) are UNTESTED, not inert** (§65). The
  documents do not say what "external" is, so the stimulus is unknown and a
  null would be worthless. Identifying the source is the blocker.
- **All four of envelope 3's velocity scalers work** (§63, §64) -- `V_ENV3`,
  `V_ATT3`, `V_REL3`, `O_REL3`. The counter-case to §47's five-of-six inert.
  `O_REL3` could not have been measured at all before the rig learned to send
  a note-off velocity, and it is **reachable but rarely driven**: few
  controllers send one, and 0 is one end of its range rather than an absence.
- **Envelope 3's scaling set works** (§63). `K_DAR3` scales phase 3 and the
  release but NOT phase 2, on the family law -- all three envelopes now agree
  on the key-scaling coefficient within 7%. `V_ENV3` and `V_ATT3` both scale
  by note-on velocity, bipolar, by factors of 8 and 37.
- **A null needs the right STAGE, not just the right route** (§63). `K_DAR3`
  read a clean flat null with the route live and the control flat, because it
  was tested on a stage its own description does not name. Read the field's
  description before believing a null.
- **`K_DAR2` measured** (§61) and predicted from `K_DAR1` to 4% before the run.
  Both envelopes scale with the key by the same law, coefficients 4.5% apart.
  Its note-64 pivot is **taken on trust**, not measured -- see below.
- **The corner tracker's resolution is the harmonic spacing** -- 3.5% of the
  corner at note 24, 14% at 48, 56% at 72 -- so anything read through the
  filter must SOUND in the bottom two octaves. That is a limit on the sounding
  pitch and **not** on the note number: key scaling reads the MIDI note (§62),
  so `KGTUNO` holds the sound low while the note sweeps anywhere. The pivot
  measurement §61 called unreachable was done this way the same day.
  The proper fix remains a **white-noise sample**, which has no comb and a
  bin-width resolution at every pitch. Blocked on sample transmission: this
  editor speaks the header protocol and loading audio needs the MIDI Sample
  Dump Standard. Only `SINE`, `SQUARE`, `SAWTOOTH` and `PULSE` are in memory.
- **Envelope 3 measured** (§57): `ENV3R1` and `ENV3R3` are RATES and higher is
  SLOWER, despite the table naming `ENV3R1` "Attack rate". `ENV3L1` is linear
  in octaves and the excursion is level x depth. `ENV3R2` and `ENV3R4` are not
  measured, and the whole envelope reaches nothing unless routed as
  assignable-matrix source 14.
- **A corner tracker exists** (§57) that reads the resonance peak instead of a
  spectral centroid: 0.9% mean accuracy, 0.1-1.0% frame steadiness, over
  527..4525 Hz, unusable below ~500 Hz where the harmonic comb quantum exceeds
  13%. It lives in the scratchpad and **should move into `probes/calibrate.py`**.
  §58 used it to re-measure `ATTAK2`, `DECAY2` and `SUSTN2`; `RELSE2` remains.
  `SUSTN2`'s absolute coefficient moved 22%, which is the size of the centroid's
  error measured directly.
- **`ATTAK2` vs `ENV3R1`: is envelope 2's attack a rate?** §28 called `ATTAK2` a
  duration, but an attack always travels zero-to-full and a fixed distance
  cannot tell a rate from a duration. `ENV3R1` is the same law numerically and
  IS a rate. Envelope 2 has its own attack target in `ENV2L1`; sweeping it
  settles this in one run.

## Panel confirmation — what only the LCD can settle (OPEN, needs a person)

**`HW_PANEL_CHECKS.md`** (machine-local, excluded via `.git/info/exclude`)
lists every question that cannot be answered over SysEx, ordered by value.

Two classes, both structural rather than incidental:

1. **Offset identity.** Every byte offset here is an unverified transcription
   (§2). A write that changes the sound proves *some* parameter moved, not the
   one named. The machine repaints the page it is displaying, so showing a page
   and writing its field is a direct test of the mapping -- and the only one
   available, since this family has no screen-mirror protocol (§1).
2. **Stored versus displayed.** `POLYPH` holds 31 where the panel shows 32.
   Any field whose panel reading is computed can be wrong the same way, and
   `VTUNO1`/`KGTUNO`/`PTUNO` are the obvious candidates -- 1/256ths in the byte,
   semitones and cents on the display.

3. **Pages that hang instead of refusing.** Writing 11 to the page register
   (`byte[91]`) stops the machine answering entirely, needing a power cycle
   (§79). Whether that is because 11 is past the end of the eleven modes, or
   because it is a real page that cannot initialise -- SAVE on an empty
   floppy drive behaves similarly (§78) -- cannot be told apart over the
   wire. Both produce identical silence. **Someone has to watch the display
   while 11 is written.** Until then the bridge refuses 11 and above, and
   `MenuScreen` offers only the three named pages.

   Cheap to settle and worth doing alongside the other panel checks: it also
   names MULTI, SAMPLE, EFFECTS, SAVE and the four EDIT variants, none of
   which has an eyes-free discriminator -- `RMULTIDATA` answers in every
   mode, which was the best candidate (§78).

**Answered 2026-08-13: this machine has NO expansion boards** -- 8 MB of
flash ROM and nothing else, no EB16 effects board and no additional filter
board. Two consequences, both good:

- **§50 needs no caveat.** The owner's manual says envelope 3 at offsets
  179-186 should not work without the IB304F. It works, measured across all
  four stages, on a machine that does not have one. So the manual is wrong
  about that and §50 rests on base-machine functionality.
- **The value-11 hypothesis is live, and §86 then weakened it.** A firmware
  page for a board that is not fitted would crash on initialisation as §85
  observed, and this machine is missing precisely the boards such a page
  would belong to. But value 6 opens the EFFECTS page — for the absent EB16 —
  **without** crashing, so "a page for hardware that is not there" is not by
  itself fatal. Three readings remain and none is favoured.

Remaining highest value: envelope 3 at offsets 179-186, on which §50 rests entirely and
which the owner's manual says should not work at all without the IB304F; the
IB304F's own presence, which one page settles and which mpc2emu also carries;
and whether the ten inert fields (§39, §47) have pages at all or merely have no
effect.

## Housekeeping

- `HW_CHECKLIST.md` and `HW_CALIBRATION.md` are machine-local, excluded via
  `.git/info/exclude` rather than `.gitignore`, matching the sibling eosed
  project.
- **Done 2026-08-14.** The keygroup pane reads each keygroup's
  `LONOTE`/`HINOTE` and shows the range as note names. The offsets are good:
  §81 wrote this pair while measuring whether overlapping keygroups layer,
  and the machine sounded or stayed silent exactly as predicted across six
  settings, including an inverted range and a single-key range. One 2-byte
  read per keygroup, so a 61-keygroup program costs 61 round trips -- under a
  second. An inverted range is labelled `(dead)` rather than printed as a
  range, since it selects nothing.
- No screenshots in the README yet — worth adding once the TUI has been seen
  against something real.

---

## Client dying mid-exchange wedges the sampler (RESOLVED 2026-08-14)

**Status: cause found and fixed twice over.** Launching the TUI repeatedly
under `timeout` while diagnosing a blank window killed it mid-handshake, and
the machine stopped answering `RSTAT` on every port until it was power cycled.
Nothing held the MIDI port. §71 and §78 both blame a wedge on what was *sent*;
this one happened because the client stopped listening.

- `install_clean_exit()` turns SIGTERM into `SystemExit`, so the `finally`
  that closes the bridge runs. Safe for frame integrity because Python
  delivers signals between bytecodes: a `send_message` already in the C call
  completes first.
- `_receive` matches replies to requests — every response is its request's
  opcode plus one — and skips anything else into `stale_replies`. This is the
  fix that survives SIGKILL, a crash or a pulled plug, none of which can run
  a `finally`.

**Operational note, not a code change:** do not run a live SysEx client under
`timeout`. It is right for a probe that talks and stops, wrong for anything a
person would otherwise quit. RESOLUTION_NOTES §95.


---

## The volume IS remotely selectable (RESOLVED 2026-08-14, was recorded as impossible)

**Status: settled.** §72 said the volume could not be selected remotely and
this project repeated it for six days, in a docstring, in the CHANGELOG, in
the TUI's status line, and in a test asserting `select_volume` must not exist.

The register is **`byte[4]`** — already found, already written to, and named a
hold flag (§70). It is the volume, 0-based, displayed 1-based. Writing it
moves the panel and the directory both.

Two things fell out of it:

- `_force_reread` writes 0 to that byte, and `select_drive`, `select_device`
  and `select_partition` all call it. **Every source change silently jumped
  the volume to the first one.** Now documented behaviour of those methods.
- The sweep that found it covered the **word and dword banks** for the first
  time. Every previous sweep, including the one that concluded §72, had only
  ever looked at the byte bank. The answer was in the byte bank after all,
  but only widening the search got anyone to look again.

RESOLUTION_NOTES §96.


---

## Sample-header tail: two §83 claims retracted by corpus (2026-08-14)

**Status:** mpc2emu ran §83's questions over 36 645 real sample headers from
21 discs. Two of three readings do not survive.

- **"Only ever all-zero or all-ones, therefore sign extension"** — dominant
  for S3000 (91.6 %) but 940 counterexamples, and S1000 samples are 86 %
  *mixed*. Generation-specific, not a format property. The inference rested
  on the word "only".
- **"17 412 at 188-191 looks like a default"** — zero occurrences outside the
  single disc measured.
- **Untested and now the only live thread:** whether the sign tracks
  multisample position, over the ~9 % of S3000 headers where those bytes vary
  at all. Needs samples grouped into multisamples.

Nothing in this project reads these bytes, so this blocks nothing.
RESOLUTION_NOTES §83b.

---

## Exceeding the object pool HALF-LOADS (ANSWERED 2026-08-15)

**Status: settled, on a second machine.** Both projects warned that a volume
over the 1006-object pool "will not load", and neither had checked that it
refuses. It does not.

The RAM ceiling is known to degrade differently: §73 measured a volume that
overran memory loading 10 programs and 60 of 88 samples, reporting
"insufficient waveform memory!" **once** and then behaving normally, leaving
keygroups pointing at absent samples and playing silence. If the object pool
does the same, the warning should read "will load incompletely" — a refusal
is loud, a half-load is a bank that appears to work with silent keygroups in
it.

**The test does not need an authored volume.** Load type 2, `programs only`,
brings programs and their keygroups and no samples at all (§93), so
successive loads exhaust the object pool on essentially zero audio — which is
the separation the test needs, or it just measures the RAM failure again. A
partial run reached **free_blocks 2 of 1006 with 31.75 MB of audio RAM still
free**: 26 programs, 978 keygroups, no samples. One more load crosses it.

**Wants a person at the LCD**, because §73's RAM overrun announced itself with
a one-time message no register records.

**Corpus context** (mpc2emu, VinSamLib): across 1843 real volumes on 21 discs
none exceeds 1006, and the largest needs 910. Authored volumes crowd up to the
ceiling and never cross it, so a user reaches this case only with a conversion
tool — which is exactly why the wording matters.

**ANSWERED 2026-08-15 by mpc2emu, on Jan's machine: it HALF-LOADS.** A volume
of 967 objects loaded whole and left `free P/K/S: 39` — 967 + 39 = 1006, to
the unit, which also confirms `max_blocks` on a second machine. A further
88-object volume then consumed the remaining 39 and stopped. A refusal would
have left 39 untouched.

So the object pool degrades exactly like §73's RAM ceiling: programs stay
resident and selectable with keygroups or samples missing underneath them,
and nothing says why. **"Will not load" is wrong; "will load incompletely" is
right.** Free memory stayed at 100 % throughout, so the pool and sample RAM
are independent budgets rather than two views of one — which matches what
`programs only` showed from the other side here, 978 keygroups consumed with
free memory never moving off 31.75 MB.

**Why this project could not answer it:** Its 38
volumes hold one program each -- a sample library -- totalling ~418 objects
with keygroups, or ~960 with every sample, against a pool of 1006; and the
sample route exhausts 32 MB of RAM first. Reloading does not help: a load
**replaces** a resident program of the same name rather than duplicating it
(§100), so the filling phase is bounded by the number of *distinct* programs
on the medium.

Needs an authored volume or a keygroup-heavy disc. mpc2emu offered to
generate images for this and the offer was declined as unnecessary; it was
not.

Nothing is blocked on it: both projects split before the limit.


---

## The active program is selectable (RESOLVED 2026-08-14)

`byte[55]` holds the selected MIDI program number, 0-based, shown 1-based on
SINGLE's `SLCT` page. Reading tracked a person stepping the field across four
values; writing it moved the panel and its "now active" count. It was the only
register that moved in a 224-entry sweep across three banks.

`select_program_number()`, and Enter on a Programs-pane row.

Narrowed in passing: §70 describes `byte[49]` as "the value of whichever field
the cursor is on", and during this measurement the cursor was on
`PROGRAM NUMBER` while `byte[49]` read 0 and moved for nothing. Whatever it
follows, it is not the focused field on any page — every observation behind
§70 was made on the LOAD page. RESOLUTION_NOTES §101.


---

## `renumber_programs()` verified on hardware (CLOSED 2026-08-14)

Never fired against a real machine until now, only synthetically. Six programs
loaded from six volumes all arrived carrying `PRGNUM` 0 — the panel read
`6 now active`, and one program change would have fired all six.

The remote renumber took **0.5 s** for six writes and the panel followed
without being touched: numbers `1 2 3 4 5 6`, count down to `1 now active`.

That also confirms §92's automatic reflagging at a **six-deep** collision,
where it had only been measured two deep, and confirms that the missing
BTSORT sort is a no-op for this write — numbers assigned in list order leave
the list already in number order.

RESOLUTION_NOTES §92.


---

## The SLCT list goes stale after a remote load (recorded 2026-08-14)

Not a bug in this project and nothing to fix here, but it will be reported as
one. After a load fired over SysEx, the panel's `PROGRAMS IN MEMORY` list
shows only the last-loaded program until the page is left and re-entered. The
counts beside it (`N program(s)`, `N now active`) are correct throughout;
only the listing lags.

A header write repaints it immediately, which is why a remote renumber leaves
a correct full list and a load does not. Consistent with the family
repainting its own LCD by default, item-index bit 13 being an opt-*out*
"postpone screen update" — a load is not a header write and never sets it.

RESOLUTION_NOTES §92.


---

## The undo path is hardware-verified (CLOSED 2026-08-15)

Tested only against `DemoBridge` until 2026-08-15, and a fake cannot fail the
way it failed: three bugs, all silent wrong-target writes (§102).

`probes/undo_roundtrip.py` drives the **application** over a real machine and
reads every value back off the sampler. Verified against a 22-keygroup
program, which matters because the first run could only reach program 0 with
its single keygroup — so the non-zero keygroup cases, the ones the bugs were
about, were skipped, and a skipped case is not a passing one (§103).

```
program PRIORT / PLAYLO       x3 each   exact
keygroup LONOTE kg0, kg3      x3 each   exact
keygroup HINOTE kg10          x3        exact
undo-all kg4                            exact
nudge run + cursor + collapse           exact
```

**Run it after touching the edit or undo path.** The synthetic suite cannot
replace it.

Still not covered: **renaming, and undoing a rename** — see below.

---

## `DELK` fired on hardware (CLOSED 2026-08-15)

The one destructive operation never sent to a real machine, because
`clear_memory` uses only `delete_sample` and `delete_program`. Fired against a
22-keygroup program: `GROUPS` 22 → 21, the named keygroup removed, the rest
**shifting down and keeping their order**, and exactly one object returned to
the P/K/S pool — which independently confirms §98's figure from the opposite
direction.

A keygroup index is a position in a list, not a slot that empties. Anything
holding a keygroup number across a delete is holding a stale reference.

RESOLUTION_NOTES §103.


---

## Rename round trip, on hardware (OPEN — next)

The one part of the edit/undo path never run against a real machine.
`probes/undo_roundtrip.py` covers numeric fields only: `PRNAME` is text, so
nudge refuses it by design and the round trip has never been driven on one.

**Why it is not simply "another parameter".** A rename is the only edit that
changes what the *catalog* says, so it is the only one whose write is followed
by a re-read that matters — and this project has already had three bugs in
exactly that window (§102): a pane context reset, a cursor jump, and a log
entry pointing at the wrong place. A rename also has to survive the Akai
character set, which refuses anything it cannot store rather than substituting
(`encode_name`), and it is 12 bytes rather than one.

**To close this:** extend `probes/undo_roundtrip.py` with a text case —
read the name, write a new one, read it back off the machine, `z`, read back
again — and check the **program list** as well as the header, since that is
what a rename is for and what the re-read exists to refresh. Invented names
only; the resident programs are from a commercial library (CLAUDE.md).

**Blocked on:** nothing. RAM only, needs nobody at the panel.

---

## Is CLR reachable after all? (REOPENED 2026-08-16)

**Status:** §75 said the panel's CLR cannot be fired remotely. That rests on
§74's sweep, which §94 proved could detect nothing — it ran against an
already-resident volume, where a CLR value would clear and reload the same
volume and net **zero words**, exactly like an inert one.

§74's own words: *"Values above 7 are untested."* The eight load types occupy
0-7, so a CLR trigger would live exactly where nobody looked.

And the method that found every other register cannot find this one: it works
by watching a **setting** the panel writes, and CLR is an **action**, which
leaves nothing behind to watch.

**To close this:** a large volume resident, a much smaller one selected, then
one candidate value at a time with `free_words` read after each — inert leaves
memory alone, a load grows it, a CLR-then-load drops it to the small volume.

**Partly answered 2026-08-16:** `byte[6]` values **8-47 and 128-135 are all
inert**, tested with a 28.83 MB volume resident and a 0.05 MB volume selected
so a clear would have returned 16 MB and been unmissable. 128-135 tested the
`0x80 | type` modifier hypothesis specifically. Unlike §74's sweep, this null
is valid.

Forty-eight unknown writes to that register caused no crash — worth recording
beside §85, where one unknown value in the *mode* register froze the machine.

**Still open:** `byte[6]` 48-127 and 136-255, and every other register. CLR is
a separate softkey from GO, so it may not live in the load register at all.

**Blocked on:** somebody at the machine for anything beyond this register.
Write-sweeping arbitrary misc-data indices is a different order of risk.

**Worth it because:** it would remove the marker dance from clear-then-load.
Ours leaves a program behind because a delete cannot remove the last one; the
panel's CLR does not.

## `ALL PROGS+SAMPLES` can silently under-load a volume (CLOSED — §111)

Measured 2026-08-16. Load-time reference resolution uses the **directory**
name; the resident name is the **header** name. A sample whose two names
disagree is silently not loaded by `ALL PROGS+SAMPLES`, with no error and
nothing on the panel. `ENTIRE VOLUME` consults no references and is immune.

Found because `analysis.collect()` reported 34 dangling references on a real
bank — the first fault it has caught in the wild rather than one planted to
test it.

**Consequence still worth acting on:** s3ked's load dialog could warn when a
volume's directory and header names disagree, but reading a header means
loading the file, so the check is not free. Left as a note rather than a
task until someone wants it.

## Does `collect()` see a partial load the way it sees a deletion? (OPEN)

`analysis.collect()` exists for one case above all: a load that exceeds free
memory reports *"insufficient waveform memory"* once and then behaves as
though all is well, leaving programs resident whose samples never arrived
(§69 — a volume at 183% of the largest machine of this type, 10 programs
loaded and 60 of 88 samples). Those programs play silence and nothing on the
panel distinguishes them from a program that is merely quiet.

**What is actually verified is the deletion case.** §80 loaded a bank,
audited it clean, deleted two samples the audit said were used, predicted 24
dangling references and got exactly those 24. That is a real failure-case
test — but it makes its dangling references with `DELS`, not by running out
of memory.

Both *should* leave the same RAM state: a zone naming a sample that is not in
RSLIST. "Should" is what §74 said as well, and that one cost an unintended
load.

**Status:** open, and cheap. Load the oversized volume, run `collect()`,
check it names the zones pointing at the samples that did not arrive.

**Blocked on:** Jan, one load. No build and no card swap — it is a disc he
already has. Read-only after the load.

**Worth it because:** it is the only claim in `analysis.py`'s docstring
resting on inference rather than measurement, and it is the claim the module
was written for.

**Not to be confused with** the verbatim-vs-rewritten question in §106 —
what the loader does with a *malformed* disc reference. That one bears on
validating a written volume, which is mpc2emu's problem, not s3ked's. §106
originally claimed otherwise and was corrected 2026-08-16.

## Renumbering gives a second volume 2,4,6 rather than 4,5,6 (CLOSED 2026-08-16)

**Fixed and verified on hardware.** `renumber_after_load(before)` snapshots
`(name, PRGNUM)` before the load and identifies the arrivals by subsequence
rather than by position. Measured: 6 incumbents keep 1–6, 21 arrivals hold
7–27 in volume order. The demo was changed first to INSERT in
program-number order rather than append, so the tests could fail on the
defect before they passed on the fix.

Known cost, recorded in §107: the numbers no longer ascend with list
position until the panel sorts, because §92 established the machine does
not re-sort after a SysEx `PRGNUM` write.

<details><summary>original report</summary>

Reported from live use: loading two three-program volumes and renumbering
should leave volume 2 holding #4 #5 #6, in volume 2's own order. It does
not.

`renumber_programs()` assigns position *i* the number *i* in `RPLIST` order.
A load appears to insert new programs in **program-number** order rather
than appending, so two volumes both numbering from 1 comb together and
list position is no longer a proxy for "which volume". Evidence and the
remaining ambiguity are in §107.

**Status:** the defect is confirmed from the user's report and from a
30-program mapping taken on the same machine. The **mechanism** is not
settled: the mapping was read after a renumber, and a sort by the load or
by the panel produces the same picture as a loader that inserts in number
order. §92 rules out a re-sort triggered by a SysEx `PRGNUM` write — that
discriminator was built to be visible and holds — but not the other two
doors.

**Blocked on:** one hardware run, `probes/renumber_order.py`. Clear, load
two multi-program volumes, read `RPLIST` before any `PRGNUM` is written.
The panel must not be touched in between.

**Then:** the fix identifies which programs are NEW rather than inferring
it from position — snapshot before the load, partition after, number the
incumbents first and the arrivals after them. §107 has the shape and the
ambiguity that duplicate names introduce.

**Do not build it against the demo alone.** A demo that appends passes
every test and hides this exact defect; that has now happened four times.

</details>

## Does `K_FREQ` do anything above 12? (CLOSED 2026-08-17)

`params.py` transcribes the range as 0..12; mpc2emu wrote 22 and the
machine accepted it. Accepted is not effective — this machine clamps some
fields and not others, and §11 shows it returning plausible wrong data
rather than erroring.

**Status:** open. §108 has the discriminator: measure the corner shift at
`K_FREQ` 12 and 22 against §43's fitted law. Extrapolates → the table's
bound is too narrow. Flattens → the bound is right and the field does not
bounds-check, which means the UI must clamp on write.

**Predicted:** flattens. Recorded before the run so it can be wrong.

**ANSWERED 2026-08-16 — the prediction was wrong.** 22 is effective
(centroid 1284 / 2237 / 3995 Hz at K_FREQ 0 / 12 / 22). The field is not
validated by the machine. `params.py` keeps 0..12 because the measurement
does not show what the real ceiling IS; the app now handles out-of-range
values instead of refusing to move them. See §108.

**DIRECTION ANSWERED 2026-08-17.** Below the reference the corner closes
hard (29 dB between K_FREQ 0 and 22), which is what §43's law predicts, so
the sign is key-relative. The earlier "centroid rose" was a noise-floor
artefact: a closing filter drives the signal into the floor and the
centroid then reports the floor. Level is the reliable channel.

**THE CEILING: THERE ISN'T ONE, measured 2026-08-17.** Swept to the top of
the byte at a note eight semitones above the pivot, the corner rises
linearly all the way to `K_FREQ` **99** — 0.508 to 0.602 `FILFRQ` units per
step against §43's predicted 0.511 — with a control at `K_FREQ` 0 reading
−0.006 octaves per octave. The field does not saturate anywhere in its byte
range. So 12 is where tracking reaches 1:1, a musically meaningful point
rather than a limit.

The earlier null above the reference was the operating-point artefact §108
predicted: it needed a source with energy in the reference band at both
notes, which a sawtooth cannot provide because its fundamental moves with
the note. With noise the control passes and the effect is plainly there.

**Decision left for a human:** `params.py` still declares 0..12, so s3ked
refuses to write a value the machine demonstrably acts on. Widening it is a
judgement about transcribed source data rather than about the measurement,
so it has not been changed unilaterally.

## The machine caches the directory across a card swap (CLOSED — §112)

`select_volume` does not invalidate the machine's directory cache;
`select_drive` does. After a card change the sampler keeps serving the
previous card's listing, and the stale reading is indistinguishable from a
fresh one — right counts, plausible names, no error. It propagates into
loads too, since `ALL PROGS+SAMPLES` resolves references against the
directory (§111).

**Fixed:** `S3kBridge.refresh_media()`, called by the disk browser before
listing. Restores the selected volume, clamped to the new medium's count.

**Still worth knowing:** any tool of ours that reads a directory after a
media change without a re-read is describing the wrong disc. §112 has the
near-miss this caused.

## The drum-inputs page: readable, unlabelled (OPEN)

`RDDATA` returns the whole page — 162 bytes, sixteen inputs in two banks of
eight, structure settled in §115. What is missing is **names**: `params.py`
has no drum region, and the page was never transcribed from the Akai
documents.

**Partly answered 2026-08-17** (§115): record byte 0 is the note, byte 3 the
V-curve (0-based), `chan` is a **global** field at header `0x0f` rather than
per record, and `input` is a scope selector that is not stored at all. Seven
of nine record bytes named; bytes 7 and 8 remain open.

**Bytes 7 and 8 are no longer zero everywhere** (2026-08-18, §132). The aux
specimen wrote input 1's full record, including those two positions, which
now read 7 and 9 and are carried in `AUXKEY 1` on HD4. `DDATA` proved
writable and the machine accepted both without snapping.

That removes the obstacle — a field that is zero in every record cannot be
located — but it does **not** name them. Naming needs somebody looking at
the drum-inputs page for input 1 and reading off which two displayed
parameters moved. A panel job, not a probe.

**Located on disk 2026-08-18** (§133): mpc2emu's diff puts them at `+0x07`
and `+0x08` of the record, stored verbatim like the other seven. So the
positions are settled and only the names are missing — the panel reading is
now the *only* thing between this item and closed.

**Byte 7 is a divisor, isolated on a single variable.** Writing 42 into
byte 7 alone — byte 8 left at zero, one byte differing from a verified
snapshot, on a route an identity write had just proven clean — crashed the
firmware with *"Internal Error - divide overflow"*. F8 would not recover
it; a power cycle was needed (§115).

**Byte 8 is UNKNOWN AND UNTESTED, not padding.** Nobody has written to it
alone. Recording it as spare would be the same inference that has now cost
two power cycles. They were about to be filed as spare
on two independent readings — seven visible parameters against nine bytes,
and a write that changed nothing on that page — and both readings were
wrong in the same direction.

**Do not write to this structure again without the machine's owner
agreeing.** It threw an internal error at him. There is no byte-addressable
route — the extended layer has no drum header — so any write is a
whole-structure `DDATA` of all 162 bytes.

**Mitigating:** the page is rebuilt from defaults at boot, so a bad write is
self-clearing and cannot permanently damage anything.

**Blocked on:** the panel's own labels for the remaining two. Not inferable
from uniform data, and no longer safely probable by writing. Also
unverified: that record *n* sits at `0x10 + 9(n-1)` — only record 1 has ever
been demonstrated.

**Worth it because:** it is a whole page of the machine that s3ked can read
and cannot present. Adding a `drum` region would make it editable the same
way everything else is.

## `MODVFILT1` has no measured DEPTH (CLOSED 2026-08-17 — §116)

**Measured.** `probes/calibrate.py mod-filter`, two velocities differenced:

```
pivot = 64.56          <- solved for, not assumed; §43 predicted 64
k     = 0.00252329 ln-Hz per (depth unit x velocity unit)
        = 0.03554 FILFRQ units, or 4.368 cents, per depth x velocity unit
```

A K2000 `VelTrk` of ±10800 cents needs depth ≈ 39.6, which would demand 88
`FILFRQ` units of a 0..99 range — so the full source depth is not
representable from any base, and the honest model is a documented lossy
clamp rather than a multiplier.

**Still open:** the fit at velocity 120 rests on depths 5–20; everything
above ran off the measurable range. Beyond ~55 `FILFRQ` units of swing is
extrapolation.

<details><summary>original entry</summary>

## (was) `MODVFILT1` has no measured DEPTH

§109 established that it responds, that it is per-keygroup, and that the
machine clamps it to ±50. **A clamp is a range limit, not a scale.** Nothing
here converts a `MODVFILT1` value into octaves or cents, and it is absent
from `scales.py` entirely — so the one modulation depth most likely to be
set by a converter is the one with no law behind it.

**Why it matters beyond this project.** The sibling mpc2emu maps a K2000
`VelTrk` of ±10800 cents onto `MODVFILT1` with a multiplier of 25 that
carries no rationale in its code. Asked whether 50 would be more faithful,
the answer from `FILFRQ`'s own measured law is that **neither is**:

```
FILFRQ law   Hz = 6.4597 * exp(0.071 * FILFRQ)   r2 0.99984
one octave   = 9.76 FILFRQ units
25 units     = 2.56 octaves = 3073 cents
50 units     = 5.12 octaves = 6146 cents
K2000 full   = 10800 cents  = 9 octaves = 87.9 FILFRQ units
FILFRQ 0..99 = 10.14 octaves end to end
```

±9 octaves cannot be expressed from any starting cutoff — the whole field is
10.14 octaves wide. **That rests on a `MODVFILT1` unit being a `FILFRQ`
unit, which is exactly what is unmeasured.** `K_FREQ` produces its shift in
`FILFRQ` units (§43), so it is a reasonable guess and it is still a guess.

**The measurement:** fix `FILFRQ` mid-range with headroom both ways, set
`MODVFILT1` to several values, sweep velocity, track the corner. Use
resonance-peak tracking, **not** a spectral centroid — §108 measured the
centroid misleading in both directions, reading noise as brightness near the
floor and arriving low-frequency content as darkening when the filter opens.

**Caution from this project's own history:** `scales.py` records that
`ATTAK2` showed a threefold disagreement between `MODVFILT1` 18 and 25 that
looked like depth-dependence and was **the corner saturating at the top of
the filter's range**. Measure away from both ends of `FILFRQ`, or the
ceiling gets measured instead of the field.

**Blocked on:** hardware plus a reference. Belongs with the cutoff
calibration, not on its own.

</details>

## Velocity zone bounds are INCLUSIVE at both ends (CLOSED 2026-08-17)

`LOVEL1`/`HIVEL1` (keygroup `0x2e`/`0x2f`) were never confirmed, and every
converted velocity split in the sibling projects passes them straight
through. Measured on a sine with `V_LOUD` zeroed, so level could not be
confused with silence:

```
LOVEL1 = 64:   velocity 63 silent (-87 dB)   64 SOUNDS (-27.78 dB)
HIVEL1 = 64:   velocity 64 SOUNDS            65 silent (-87 dB)
```

Both ends inclusive, tested separately rather than assuming symmetry — a
format inclusive at one end and exclusive at the other is not unusual.

**Consequence:** adjacent zones written `hi = 63` and `lo = 64` tile exactly,
with no gap and no overlap, and a writer passing the source's bounds through
unchanged is correct. No off-by-one anywhere in that path.

## An unnamed 16-bit field in every loop record (RETRACTED — §120)

**The corpus evidence was one local tool's output.** All 74 blocks carrying
the pattern came from `vinsamlib-tests/`; the provenance filter missed them.
Only 14 genuinely third-party headers exist here and one carries anything in
that region. The document conflict below is still real — it needs no corpus
— but there is no evidence that those bytes carry information on a machine.

`probes/unmodelled_bytes.py` found two four-member families on the loop
record's own stride of 12, at **+6 and +7** into each record — sample header
`0x5c`, `0x68`, `0x74`, `0x80` and the byte after each. The low byte spans a
wide range and the high byte holds 0/1/2/9, which reads as **one 16-bit
little-endian field at `0x5c`**, repeated per loop record, reaching ~2300.

Nothing in `params.py` names it, and it varies across third-party files.

**Worth it because** the same pass proved the program block's 56 unmodelled
bytes are all constant and the keygroup block is fully modelled — so this is
the *only* place undocumented per-sample structure is known to live.

**AND THE TWO PRIMARY DOCUMENTS DISAGREE ABOUT IT.** The S1000 source
declares `LBYTES EQU $-LOOPAT` (12) and `LOOP2 DW LBYTES*7 DUP(0)` — eight
loops, putting loops 5–8 at exactly `0x56`/`0x62`/`0x6e`/`0x7a`. The
S2800/S3000XL source names those same offsets `SLXY1`–`SLXY4`, four bytes
each, leaving eight of every twelve unnamed. `params.py` follows the S2800.

§8 found twelve offsets where the two documents *agree*; this is the first
place they are known to conflict.

**The corpus rejects the eight-loop reading:** `SLOOPS` is 1 in all 177
third-party headers, and reading `0x56` as a loop point gives values around
134 million against samples 15–80k frames long. So the more complete-looking
document is the wrong one here.

**Blocked on:** hardware, and cheaply. Set a loop from the panel, save, and
read which bytes move. A precise target rather than a survey — and it
settles a documented conflict rather than only naming a field.

**Not** the 0x8d–0x95 bytes: 72 of 182 blocks carry a single fixed value
there and 108 carry zero, which is two populations of files rather than a
parameter.

## Is there a remote volume DELETE? (OPEN — READ THE WARNING FIRST)

> ### ⚠ DO NOT SWEEP A DELETE REGISTER'S VALUES
>
> The front panel's DELETE page carries this type enum (photographed
> 2026-08-18, §134):
>
> ```
> 0  cursor item only     3  ENTIRE VOLUME
> 1  all programs only    4  OPERATING SYSTEM
> 2  all samples
> ```
>
> **What type 4 actually does — corrected.** The first version of this
> warning said it erases the machine's firmware and called the instrument
> unreplaceable. **That was an overclaim and it was mine**, taken from a
> plausible reading and written down without checking. Jan queried it.
>
> It deletes the **OS file on the selected medium**, not the sampler's own
> boot code. Our own table is the evidence: `LOAD_TYPES[6]` is
> `Operating System`, so an OS can be *loaded from* a medium — which makes
> it a file on that medium, and the symmetric delete removes that file.
> `bridge.py` already guards load type 6 for the same reason. The
> `BOOT SYSTEM#` volume's nine directory entries include no OS-typed file
> either, so the OS lives outside the volume directory rather than in it.
>
> **The warning stands anyway, at its true size.** Sweeping values on a
> register carrying this enum walks through `3 ENTIRE VOLUME`, which
> destroys a volume outright, and `4`, which can strip the OS from a boot
> medium — recoverable only if an OS file exists elsewhere to reload. Jan's
> machine boots to the flash device. Neither is a power-cycle recovery.
>
> §105's rule applies with more teeth than when it was written: a reachable
> page does not imply a reachable softkey — **and here a value that looks
> inert may not be.** Any future work on this item must name the value it is
> about to write and why, one at a time. No sweeps.

**Status:** open. Remote save, volume creation and volume rename all work
(§127). Removing a volume does not, by any route tried.

**The delete itself is real and is NOT absent** (§134). `ENTIRE VOLUME` is on
the panel's DELETE page, so the earlier "perhaps this family has no
per-volume delete" possibility is closed. What is missing is the remote
route, not the capability.

Swept without an event: the byte bank 10–127 and the **word bank** 0–31,
both written own-value; `byte[7]` across its types, which deletes from
memory and never from the disk (§128, §129).

**Blocked on:** hardware time, and **mpc2emu holds the lead on the sampler**
(2026-08-18).

Untried, in order of promise — **and note the first entry is no longer a
value sweep**:

- **selectors 3, 4, 5 and 7** of the miscellaneous data, never addressed by
  this project at all. The name bank turned up at selector 6 and volume
  naming had been called impossible until somebody read the §5 table. This
  is now the *first* candidate rather than the second, because it explores
  addresses rather than values and cannot stumble onto a type enum.
- the panel's own page structure. Softkeys read
  `SAVE VOLS REN DEL SCSI FORM` with **`GO` on F8** — the trigger is a
  *separate key* from the type selection, unlike the LOAD and SAVE
  registers where the write IS the operation. If DELETE works that way
  remotely too, there is an arm-then-fire pair to find, and the arming half
  is safe to look for while the firing half is not.
- `byte[8]` / `byte[9]` at other values — **deprioritised and fenced.**
  Those are save registers whose type table is `LOAD_TYPES`, not the delete
  enum, so they are probably not the risk. "Probably" is not the standard
  that applies when value 4 of some enum on this page erases the firmware.

**Caveat that applies to every negative above:** own-value sweeps fire a
register with whatever type it is already set to, so they detect "this
register acts" and not "this register can do X" (§128).

## Seven disposable volumes left on the HD4 test image (OPEN)

`VOLUME 005`, `VOLUME 006`, `VOLUME 009`, `MIXED KIT 1`, `SKED VOL A`,
`SKED VOL B`, `MIXED DEMO` — created while establishing §127. Harmless, and
they stay until either the delete above is found or the image is reformatted.
mpc2emu holds a byte-identical pre-session backup.

## Re-examine §116 against its source (CLOSED — premise withdrawn, §135)

**CLOSED 2026-08-18.** This item rested on `NSWHITE` producing a pitched
tone. It does not — that was a program stack, three programs sharing PRGNUM
0 with an OMNI among them, and the "~46 dB down by 2-8 kHz" figure was the
tone-over-floor step rather than a property of the source. Isolated, the
sample measures as noise. **The premise is withdrawn and so is the concern.**

The original text follows for the record.

~~`NSWHITE` produces a pitched tone that tracks the keyboard, not white noise
(§135).~~ `corner_from_difference` is source-agnostic by construction, so most
of what was measured with it is unaffected — but two of its own stated limits
sit close to this source's properties:

- it gates on bins within **45 dB** of the reference's peak, and this source
  is ~46 dB down by 2–8 kHz;
- it is **unusable below ~500 Hz**, and §116's fitted bases are 533.54 and
  583.76 Hz.

**What to do:** re-run one §116 point with a genuinely broadband source and
compare. If the corner agrees, §116 stands as written. If it does not, the
velocity/depth law needs re-fitting — the *method* is sound either way.

**Not blocked on hardware** beyond a source. Making one is the real task:
this project has no way to send sample data (no MIDI Sample Dump Standard),
so a broadband sample has to arrive on the disk, which mpc2emu's image
writer can now do.

**Do not delete `NSWHITE`.** It is the specimen this was found with, and
§133's disk decode references the same volume.

## A calibration disc exists and needs a card crossing (OPEN)

`/home/lentferj/temp/HD6.img` — 64 MB, volume `CALNOISE`, built by mpc2emu
2026-08-18 after §135. Manifest at `HD6.manifest.json`.

```
NOISE W 1SHOT   PRGNUM 120  ch 0   white, one-shot, 20 s
NOISE P 1SHOT   PRGNUM 121  ch 1   pink,  one-shot, 20 s
NOISE W LOOP    PRGNUM 122  ch 2   white, looped,   20 s
NOISE P LOOP    PRGNUM 123  ch 3   pink,  looped,   20 s
```

**It carries the three properties tonight's failure paid for**: program
numbers well out of the way, explicit channels, **no `PMCHAN 255` anywhere**
— verified by reading the written bytes back rather than by intending it —
and names that announce themselves in a program list.

**And its flatness travels in the file.** The manifest records spectrum and
lag-1 measured on the frames actually written, in the run that wrote them:
white `lag-1 = -0.0022`, flat within ~11 dB from 62 Hz to 8 kHz. Pink is
`+0.8713`, which is **correct and not a fault** — pink noise is correlated by
construction, that being what −3 dB/octave means.

**That white lag-1 is the number that would have caught §135.** If a capture
of this source ever reads `+0.999`, the manifest says at once that the
machine is not playing what was written.

**Blocked on:** the card being in the PC to write the image, then in the
sampler. Not urgent — nothing else waits on it.

**First use when it lands:** re-run the 20–200 Hz rumble check. It measured
149.5 / 148.5 / 149.2 dB/Hz across three notes on `NSWHITE` — identical, so
it is the capture chain rather than the sampler. Confirming that on a source
whose content both projects have verified settles the attribution.

## `LOOPAT1` is the loop END — settled (CLOSED)

`LOOPAT1` is where playback returns; `LLNGTH1` measures backwards from it.
Confirmed on hardware by content discrimination, 0.7998 against 0.0412 with
orthogonal references (§136), and stated plainly in the S3000XL manual, in
`ConvertWithMoss`, and by 82.9% of 16493 factory sample headers.

**For the sibling's writer:** it wrote the intended loop *start* into `LOOPAT`,
so every looped sample it produced asked for `[start - length, start]` —
negative whenever `LOOPAT` is 0, which was all of them. Cause of the silent and
degraded playback; fix is to write the loop end.

**Open:** `LOOPAT` = `SLNGTH` exactly does not loop, one past the last valid
frame index. And the confirming capture plays the right region with no
measurable periodicity — region right, repetition unproven.

## The two playback rates are not written up (CLOSED 2026-08-20 — §142)

**Status:** established on hardware, recorded only inside §137.

`byte 0x01` of the sample header selects the playback rate and does it with
**bit 0 alone** — 0 = 22050, 1 = 44100. `SSRATE` (0x8a) is descriptive and does
not drive it: writing 255 into the bit-0 field is accepted, which is the usual
lesson that acceptance is not validation on this machine. §137 confirms the
consequence by ear (a 24000 Hz source resampled to 44100 plays at pitch), but
the finding itself has no section, so it exists only as a sentence inside
another one's preamble.

**Closed by §142**, written from the original 2026-08-18 captures rather than
from a re-run — the data was still on disk, which is the cheaper half of "ask
whether the answer is already held". The load path remains untested and is
carried by the converter's discriminator disc, not by this item.

## Our `FILFRQ` law disagrees with ConvertWithMoss's firmware table (MEASURED 2026-08-20)

**Status:** measured on hardware; the disagreement is real, constant, and
unexplained. Neither number is withdrawn.

ConvertWithMoss added a 100-entry `FILTER_CUTOFF` table to its Akai S-1000
converter on 2026-08-20, read out of the sampler's operating system (v4.40) and
documented as the −3 dB points of the three one-pole stages which form the
18 dB/octave low-pass. That is an independent, firmware-derived ground truth for
a quantity we had only ever fitted, so it is worth reconciling.

Measured here on the **S3000XL** with the plain cascade (`FILQ` 0), using a
sawtooth harmonic comb divided by its own spectrum at `FILFRQ` 99 so the source,
the speaker path and the interface response cancel:

    FILFRQ    measured    CWM table    §54 law
        40       139.3          243      110.6
        56       444.5          788      344.3
        72      1403.8         2517     1072.3
        84      3363.1         5640     2513.9

    over FILFRQ 40..84:  measured / CWM  = 0.5668  (sd 0.0116)
                         measured / §54  = 1.2886  (sd 0.0311)
    slope: measured 1.045 octaves per 10 steps, CWM 0.984, §54 1.024

Two keys an octave and a half apart (MIDI 28 and 46) agree within ~1% at every
setting, so the measurement is not the loose part. **The slopes agree; only the
absolute placement does not.** CWM's table sits 1.76x (0.82 octaves) above what
this machine does, and our own §54 law sits 1.29x (0.37 octaves) below it.

Above `FILFRQ` 84 the measured ratio drifts upward (0.65 at 88, 0.80 at 92) as
the corner approaches the source's own bandwidth — that is the measurement
running out of headroom, not the law bending, and those two points should not be
used.

**Candidate explanations, none yet tested:**

1. **A single stage versus the cascade.** For N one-pole stages in series the
   cascade's −3 dB point is `sqrt(2^(1/N) - 1)` of one stage's corner: 0.5098
   for N = 3. We measure 0.5668. If CWM's table is really one stage's corner
   rather than the cascade's, that accounts for most of the gap but leaves 11%.
2. **Different machine.** They read S1000 firmware; this is an S3000XL. The
   family shares a protocol, not necessarily a filter implementation.
3. **§54 measured a different quantity again** — it was fitted to the resonance
   peak, not the −3 dB corner, so it was never the same number as either of
   these and should not be quoted as if it were.

**Blocked on:** nothing here. Distinguishing (1) from (2) needs either an S1000
to measure or the firmware coefficients themselves, and (3) needs §54 re-derived
against this run.

### Update 2026-08-20: the filter ORDER differs, so it is a machine difference

The roll-off slope was measured from the same captures: **−12.2 dB/octave, two
poles** (r² 0.996–1.000, both source keys, `FILFRQ` 40..56), against the three
one-pole stages at 18 dB/octave that CWM read out of S1000 firmware. That
settles explanation (1) against (2) above: **the two machines have different
filters**, not one filter under two labels.

Consequence for anything converting between formats: `FILFRQ` is not portable
between S1000 and S3000XL, in either direction, and an Akai→Akai passthrough of
the value is not sound-preserving.

Still open: the S1000's own corner law, which needs an S1000 to measure.

### Update 2026-08-20 (second): the corpus route is closed

The generation split was tested against 101 131 keygroups from 43 factory discs
and **the corpus cannot answer it**: no program appears in both generations, no
disc is mixed, and the S1000 and S3000 discs hold different material
(orchestral against synth dumps), which moves `FILFRQ` far more than the 0.82
octaves under test. The distributions differ, in the opposite direction to the
prediction, and are uninterpretable — see §140.

So the S1000 corner law needs **an S1000**. There is no corpus-side substitute
and no further analysis of the discs we have will produce one.

### Update 2026-08-20 (third): what exists in the literature, and what does not

Searched for a published S1000 filter law. **No acoustic measurement of the
parameter-to-hertz mapping appears to exist anywhere public.** What exists is
three things, none of them a measurement:

1. **The specification.** "18 dB/octave, non-resonant" is Akai's own published
   figure for the S1000 and is repeated everywhere. It is a *slope*, not a law —
   it says nothing about which setting produces which frequency.

2. **MAME's `sound/l6009.cpp`** (Robin Whittle, Devin Acker), emulating the
   S1000's sound LSI. It applies a first-order IIR three times, and the comment
   is an explicit hedge: *"-18dB filter with no resonance and only one settable
   coefficient (**most likely** just three -6dB first order filters in
   series)"*, with a TODO conceding that `ENV_SHIFT` is "basically a complete
   guess". **There is no coefficient table** — the register value is used
   directly. So it publishes an implementation, not a parameter-to-hertz law.

3. **ConvertWithMoss's `FILTER_CUTOFF`, published 2026-08-20** — the first
   parameter-to-hertz table for this family that we can find anywhere. Its
   scaling table and coefficients come from the OS v4.40 disassembly, but the
   conversion of a coefficient into hertz assumes **three one-pole stages at
   44.1 kHz**, and they take the envelope accumulator width explicitly "from the
   emulation of the sound hardware", flagged as such at the constant.

**So CWM's hertz values inherit MAME's "most likely three".** If the pole count
is wrong, every entry is wrong by the corresponding factor — and a wrong pole
count is exactly the shape of the constant 1.76x offset in §139. That does not
make them wrong: §139 measured an **S3000XL** at 12 dB/octave, and the S1000 is
specified at 18, so the two are very possibly both right about different
machines. It does mean the S1000 number has never been checked against an S1000.

Which leaves §139's measurement plausibly the **first measured** filter law in
this family, and the S1000 side resting on a chain of two derivations and one
hedge.

**Blocked on:** an S1000. Unchanged, but the reason to want one is stronger:
nobody has measured this, so the measurement is not a duplicate of anything.

## Measure the S1000 filter law with a borrowed machine (PLANNED 2026-08-20)

**Status:** designed, not built. Procedure in
`docs/re_procedures/s1000_filter_disc.md`.

Build a disc of test presets — one program per `FILFRQ` setting over a sawtooth,
wide-open references bracketing each — hand it to somebody who owns an S1000,
have them record one note per program, and analyse the recordings here. Played
on our S3000XL first, the same disc is a **paired test** and removes the
material confound that made the corpus comparison uninterpretable (§140).

Built by **patching a genuine factory `.P1`/`.S1` pair**, not by generating S1000
files: the sibling converter emits 192-byte S3000 blocks only and has no S1000
path, whereas a `.P1` is 300 bytes with `FILFRQ` at keygroup+`0x07`, so N
programs is N copies with one byte different.

**Blocked on:** a volunteer with an S1000 **and** a BlueSCSI-class adapter or a
Gotek. Floppy is not the universal option it appears to be — the AKAI format is
not DOS and cannot be written on a PC drive.

## `FILFRQ`: §54 and §139 disagree by a constant 1.29x (CLOSED 2026-08-21 — §145)

**Status:** both measured here, on the same machine, by different methods.

    §54   6.4597  * exp(0.07100 v)   from the RESONANCE PEAK
    §139  7.60732 * exp(0.07245 v)   from the -3 dB CORNER at FILQ 0

§139 is 1.29x above §54, constant to sd 0.031 across `FILFRQ` 40..84 — a fixed
factor, not drift, so one of them is measuring something other than what it
says. It is not obviously definitional: §54 argues the resonance peak sits **at**
the corner and does not move with `FILQ` (919 Hz at sixteen settings, §53), and
a Q-independent peak should not sit 1.29x below the −3 dB point of the same
filter.

Meanwhile, recorded in `scales.py`: a converter mapping a source format's
*cutoff* wants **§139**, which measures the −3 dB point directly and is what
every format means by the word; anything asking where the resonance sits wants
§54.

**Closed by §145.** Both laws reproduce on one sweep — §139 to 1.7%, §54 to
3.7% — so the 1.29x is definitional. The resonance peak sits at **0.790 of the
−3 dB corner** (sd 0.039), so §54's claim that the peak locates the corner is
what was wrong, not either measurement. `scales.py` now labels §54's law as the
resonance peak.

### Sweep 85..98 first, because that is where the material actually lives (DONE 2026-08-21 — §146)

Measured by the converter across four **S3000** factory discs, 1555 keygroups:

    FILFRQ 85..98    685 keygroups
    FILFRQ 99        868 keygroups
    FILFRQ 40..84      2 keygroups

**Two.** Both of this project's laws are fitted on 40..84, and on this material
that range holds 0.1% of voices; 44% sit in 85..98, where the law is an
interpolation between two measured endpoints and nothing else.

**Qualified 2026-08-21: this is generation-specific and the first version of
this entry overstated it.** The full corpus, both generations:

    generation      keygroups   40..84   85..98   99 (open)
    .P3 (S3000)          1555        2      685         868
    .P1 (S1000)         76086     6869     6541       61425

The fit is **badly placed for S3000 material and well placed for S1000
material**. So "fitted where the material is not" is true of the S3000 discs and
false of the S1000 ones, and the unqualified claim was the same error this entry
is about: a correct measurement generalised past the population it was taken
from. The 85..98 sweep stays the priority because that is where S3000 material
lives, but the fitted range is not misplaced everywhere.

The fit range was never chosen — it was **imposed by the instrument**. §54's
own bounds note says it: below 44 the corner drops under the lowest note's
fundamental and above 92 the source runs out of harmonics above the corner, so
the sawtooth, not the machine, set the limits. The material then turned out to
live almost entirely outside them.

**Calibrating over the range you can measure is not the same as calibrating over
the range that is used, and nothing warns you when they do not overlap.** The
fix is a source with harmonics far enough above the corner to bracket it at
`FILFRQ` 98 — a lower root, or a brighter waveform than a sawtooth, or a noise
sample if the Sample Dump Standard is ever implemented.

Worth more than any refinement inside 40..84.


## `ATTAK2` below byte 40 is still unmeasured (OPEN 2026-08-22)

**Status:** nine attempts, no measurement. §150 records each failure and the
control that was missing from all of them.

The law is fitted 40..85, so a converter clamping to the fit cannot ask for a
filter attack faster than about 66 ms. Whether the machine floors there or
attacks faster is unknown.

What survives: `ATTAK2` 70 rises monotonically over 540 ms, so the field is live
and slow values are slow. "Byte 0 is faster than byte 40" was reported and then
withdrawn — it was read off oscillating data on a source that cannot resolve
time (§149).

**What a fresh attempt needs**, learned the expensive way:

* a **stationary** source. `TC10 NOISE` is on the card and verified good through
  the sampler's own D/A at 0.92–0.99 dB HF stability;
* a detector that cannot read a floor as a plateau, and whose gate threshold is
  derived through the same path as the measurement (§150);
* **a positive control in the same session, immediately before the sweep.** Nine
  attempts assumed a configuration that worked an hour earlier still worked. It
  did not.

**Blocked on:** nothing but a fresh start. The hardware, the source and the
question are all in place.

## What actually selects the answering MIDI channel (OPEN 2026-08-22)

**Status:** two sessions, identical `PMCHAN 0` in every program header,
opposite results — §147 found channel 1 silent and 2..16 live, §151 found
channel 1 live and 2..4 silent. A field that reads the same while the
behaviour reverses is not the field that decides.

**Blocked on:** nothing external. The suspect is the global/multi assignment,
which `analysis.collect` does not read — the S3000XL/S2000 multi opcodes
(`41`/`42`) are implemented in the bridge but no audit path reads a multi
part's channel mapping. Reading it in both states would settle it.

**Why it is worth closing rather than living with:** the workaround (§151)
is a four-take lift check at session start, which is cheap but is a
measurement standing in for a read. Every session that plays notes pays it,
and a session that forgets pays §147's thirty captures instead.

**Do not** attempt this by writing `PMCHAN` and watching what happens until
the multi state is *read* first — with two unknowns live, a write that
appears to work proves nothing.

## `K_FREQ`'s key-follow disagrees between two rigs on one machine (OPEN 2026-08-28)

**Status:** open and unexplained. Both measurements stand; neither is
withdrawn. Four candidate mechanisms have been proposed and refuted — see
RESOLUTION_NOTES §169 for each and for what killed it.

§167 measured octaves of filter corner per octave of key at 96–103% of
`K_FREQ/12` across ten points from −30 to +40. A sibling session's independent
sweep **on the same physical S3000XL** fits **0.622×** that law, flat across a
4.5× range of `|K_FREQ|`, with per-point r² 0.95–1.00.

Same instrument, same field, same quantity — so unit-to-unit tolerance is not
available as an explanation, and one of the two instruments is measuring
something other than what it reports.

**Blocked on:** nothing external, but not worth bench time on its own. The two
rigs locate "the corner" by different means — a `FILQ`-difference peak here, a
−3 dB threshold against a fixed 50–150 Hz band there — and until they agree on
what the corner *is*, a re-run compares two definitions rather than two
measurements. Settle that on paper first; §169 has the argument.

**Do not** adopt `K_FREQ × 0.622/12` as a conversion constant on the strength
of it working. It is an unexplained empirical factor from one of two
disagreeing instruments, and if the cause turns out to be note-dependent rather
than a constant scale, a converter built on it is wrong by an amount that grows
with distance from wherever the sweep was taken.

## A stale volume directory cannot be distinguished from a live one, except by probe (OPEN 2026-08-30)

**Status:** the behaviour is measured and a working gate exists
(RESOLUTION_NOTES §170). What is open is *where* the staleness lives.

With the medium absent the machine returns a complete, plausible, previous
volume directory — no error, no empty list — and `refresh_media` does not
clear it. The tell is that two SCSI IDs holding different media return
identical contents; they disagree the moment the medium is back.

**Blocked on:** nothing external, but it needs a bench session with
deliberate card removal. This one was discovered around a card swap someone
else was doing, so the two states were an hour apart and not under control.

**Worth closing because** the load sequence is CLR and *then* load, so a
directory that lies costs the contents of RAM before it reports anything, and
§94's quiet out-of-memory failure leaves programs resident, selectable and
silent rather than raising. The gate prevents the loss; it does not explain it.

**Do not** assume the sampler is the culprit. The disc arrives through an
SD-card device, so the cache could be the sampler's, the emulator's or the
reader's, and the probe in §170 is behavioural and cannot tell them apart.

## Every load costs a fixed 70 s wait that may be 60 s of nothing (OPEN 2026-08-31)

**Status:** open, and the cost is measured. The 18-volume rate sweep on
2026-08-30 spent **21 minutes** (18 x 70 s) purely waiting for loads to finish.
Every probe script that loads a volume pays it.

The wait exists because there is no known safe way to ask "is the load done".
`trigger_load`'s docstring is explicit that the machine stops acknowledging
while it works, so silence is indistinguishable from a slow load.

**What the evidence actually says, and it is weaker than the practice:**

- §71's wedge was a **train** of `RSTAT` probes every 8 seconds throughout a
  **58.7 MB** load, which ran in 30-50 s bursts and finally sat at BUSY until
  power-cycled.
- The same docstring records that the identical trigger **"on a quiet bus
  finished in seconds"**.
- And it says outright: *"Whether the probing caused it or merely coincided
  with it is NOT established."*

So a 70 s blanket wait for volumes of half a megaword rests on an attribution
the project itself marks unproven, taken from a load 60x larger.

**Blocked on:** a decision about risk, not on information. The test is one
load followed by a **single** cheap read after a short wait — qualitatively
different from a probe train during a multi-minute load — walking the wait
down until the read no longer succeeds. That bounds the real load time.

**The risk is a wedge needing a power cycle**, which on this bench cuts power
to three devices, so it should be run deliberately and not as a side effect of
something else. Use the smallest volume (`TC11 ATKCAL`, one sample) so any load
is as short as it can be.

**Do not** poll during a load to find this out. The question is what the
minimum *post-load* wait is, not whether polling is survivable.

## §141's attack law: t90 is sound, the shape is not, and the shape may not be measurable here

**Status:** open, and narrowed. Swept, not blocked on more of the same.

`t90 = 0.9 * 0.000201173 * exp(0.10844 * ATTAK1)` was fitted on `t90` read off
the machine's display. Swept against audio on a single-keygroup program at
`ATTAK1` 75/85/94/99, **it holds to within 12%** (§186). Keep it.

What does not hold is using it to predict captured *level* at a hold shorter
than the attack: measured gains of +11.00 and +14.90 dB against a predicted
+4.21 and +7.29. **No fixed correction exists**, because the observed shape is
not one curve — the origin-free statistic `R` runs 1.6–1.8 at `ATTAK1` 75,
3.0–3.1 at 85 with the same sample, and 0.18–0.42 on another program,
straddling both the linear (1.000) and exponential (2.738) references.

**Blocked on a flat-contour sample, not on hardware time.** A capture shows the
envelope multiplied by the sample's own amplitude contour, and nothing in the
sweep can separate them. Measuring the envelope generator's shape needs a
sample of known contour, which needs the MIDI Sample Dump Standard — the same
missing capability that blocks the white-noise tracker work. **Until then the
envelope's true shape is not measurable on this bench**, and further sweeps
will produce more numbers of the same kind.

Two measurement limits worth keeping, both found by hitting them:

- Below `ATTAK1` 75 the 10–90% span is ~0.2 s and `R` is quantisation noise.
- Above `ATTAK1` 85 the attack outlasts a ~7.3 s one-shot sample: the peak
  arrives with 1.33 s left at 94 and 0.53 s at 99, so the envelope never
  completes and the measured "peak" is the sample ending. **Any future sweep
  needs a sample that outlasts the slowest attack under test.**

Before trusting any onset-derived figure from such a sweep, see **§189** for
what the shared onset check can and cannot see — in particular that a merge
margin is a property of the individual capture and must be measured there.

## §185's re-articulation: read the keygroup map

**Status:** open. One capture, one program, cause untested.

A re-articulation 0.95 s after note-off appears on MIDI 48 and 55 and is
absent on 36 and 43, which decay to the floor and stay there. The split sits
between 43 and 48, where a keygroup boundary would be.

**Narrowed 2026-09-08: the phenomenon is BUILD-dependent.** Re-captured on the
MX10 build of the same source program — same `PRGNUM` 9, same 8 keygroups —
under §185's original conditions, it is **absent on all four notes**, with the
route live at 4/4 sounded and −34.9 dB, confirmed both by a grid-independent
event count and by the harness classifier against real marks.

**Blocked on:** reloading the build that produced the original capture, which
may need a card swap. Comparing per-keygroup envelope fields between the two
builds is then a read-only diff, and the field that differs on kg1/kg2 but not
kg0 is the candidate. Until that build is resident, this cannot progress —
reading the MX10 build's fields says nothing, since it is the build that does
*not* show the effect.

Related: a sibling session filed a comparable event on other hardware at
~1.15 s as a property of a superseded bank, four mechanisms refuted. That
framing no longer covers a similar event on a different machine.

## PLAYLO/PLAYHI did not gate playback, and the pan combination is unmeasured

**Status:** two open questions left by §191, neither needing a card.

**PLAYLO/PLAYHI.** Narrowed to 60..62 on a one-keygroup program and read back
as stored, notes 48 and 72 sounded at full level. Either they are a second pair
of stored-but-unused fields on this machine — like `OSHIFT`, which §191 shows
is accepted and ignored — or they need something that run did not do
(a different recalculation, or the keygroup's own span governing instead).

**Blocked on:** nothing but bench time. Read the keygroup's own key span and
narrow *that* as the control, so the two candidate gates are distinguished
rather than conflated.

**The pan combination rule.** Program `PANPOS` is live (+48.23 dB at +50).
There is **no keygroup-scope `PANPOS`** in the parameter table, so what a
converter calls "zone pan" is some other field, and the combination question
cannot be posed until that field is identified.

**Blocked on:** finding which field a converter means by zone pan. If it is a
keygroup or zone output assignment rather than a pan, the question changes
shape entirely.

## The eight ENV3 stages declared the IB304F — FIXED 2026-09-09

**Status:** done. `requires` removed from `ENV3R1`..`ENV3L4`; the seven fields
§87 names as genuinely gated keep it. Two tests pin the distinction between
the note (documentation, still on all fifteen) and `requires` (enforcement,
now on seven), so the flag cannot return quietly.

Original report below.

`ENV3R1`..`ENV3L4` at keygroup offsets 179–186 carry `requires="IB304F"`, so
`s3ked` **refuses to read or write them unless the board is declared**. §87 is
marked "Status: corrected" and names the seven fields that genuinely are gated
— `FLT2GAIN`, `FLT2MODE`, `FLT2Q`, `FIL2FR`, `K_FRQ2`, `TONEFREQ`, `TONESLOP`.
Envelope 3 is not among them; §50, §63 and §64 measured it on a machine that
never had the board.

**The fix is small**: drop `requires` from those eight declarations, and add a
test asserting envelope 3 needs no board so the flag cannot come back.

**It has already propagated once.** A sibling project transcribed the flag into
its own format document from this table and built an explanation on top of it.
A machine-readable field is what downstream tools consume; the note beside it
is not.
## Filter 2's mode-3 pivot — RESOLVED 2026-09-10

**Status:** closed by §206. All 32 `FLT2Q` values measured at `FIL2FR` 64. The
pivot is between **23 (−2.01 dB) and 24 (+0.77 dB)**, and 24 is the only value
in the range passing an inert test at |depth| < 1 dB.

## Filter 2 mode 3: the centre moves with `FLT2Q` — RESOLVED 2026-09-10

**Status:** closed by §203, and the mechanism turned out to be the whole
filter-2 story rather than a mode-3 quirk. Every mode's feature sits at its own
`FLT2Q`-dependent offset from `f0`; the offsets collapse at high resonance,
where all topologies agree to under 1 %. There is one tuning law, not five.

## Filter 2: three more corner ladders, one per mode — RESOLVED 2026-09-10

**Status:** closed by §201, §202 and §203. All four modes measured. The premise
— that each mode has its own corner law — was itself wrong: §203 showed the
per-mode "laws" were one law read through four different features at four
different `FLT2Q` values.

The prevalence figures quoted in the original entry (7 % / 50 % / 39 %) came
from a 6-of-64-disc sample and are wrong; the full corpus gives **LP 4.7 %,
EQ 34.2 %, HP 57.6 %, BP 3.5 %** (§212). HP is the *most* common mode.

**The two sub-items both closed as well:**

- **The HP shape.** Resolved by §208, and the entry's own suspicion — "either
  the section is a different order in HP" — was correct. **The highpass tap is
  one pole**, not two: +6.1 dB/oct asymptotically at every `FLT2Q`. mpc2emu was
  decoding it as XPM `High 2`; it is `High 1`, on 57.6 % of board use.
- **Where the top departs.** Resolved by §205. There is no breakpoint: rung 89
  is already +1.6 % off, so it begins at the first byte above 88, and the steps
  are uneven (four large at ~1.089–1.092, two small at ~1.057–1.059). §204
  confirmed it is not filter 1's departure seen twice — filter 2 is still
  exponential four bytes and 0.4 octaves past filter 1's.

## Multi: three things an RE session has to settle (OPEN 2026-09-10)

**Status:** open. Multi edit is wired into the TUI behind the write gate
(§213); these are what it does not know. No card write is involved in any of
them, and the first two need the rig.

1. ~~**The part count.**~~ **CLOSED 2026-09-10 by §214: 16 parts, 0-15,
   measured.** Reads could not answer it — parts 16+ return part 15's buffer
   byte-for-byte with no error — but a write to part 16, 20 or 31 returns
   REPLY error code 1 while parts 14 and 15 accept. The write path also does
   **not** alias onto part 15 while refusing, so a TUI edit of a non-existent
   part cannot silently corrupt a real one. All 16 parts verified byte-identical
   to the pre-probe snapshot.

2. **What an `FX1`–`FX4` value indexes.** Partly answered by §215: the
   **domain is 0-204**, measured by writing all 256 values and reading `REPLY`.
   205-238 are refused; 239-255 are accepted but appear to fold into the
   refused band, and **writing 239 panicked the machine** to "Internal Error -
   divide overflow" (recovered with F8, nothing lost). The declarations now say
   `0..204` as a safety fence.

   **CLOSED 2026-09-10 by §216, read off the panel by camera.** They are
   **intrinsic built-in setups with names** — byte 37 displays "38 CLEAR
   DETUNE", byte 0 displays "1 REVERB EQ 1". The panel is 1-based and the byte
   0-based (`display_offset=1` now declared), which also means §215's figures
   are RAW BYTES: crash at byte 239 is **panel 240**.

   And the four fields are **not** four fx setups: the page is headed
   EFFECTS/REVERB SELECT with rows FX1, FX2, RV3, RV4, and only FX1/FX2 have an
   Effects column. **Bytes 16-17 index the effects list, bytes 18-19 index the
   reverb list.** An effects setup carries its own reverb — setting FX1 to
   byte 0 changed both its columns to match FX2's exactly, as predicted before
   the test — which is why no byte in the 32-byte header stores a per-channel
   reverb, and why `FXFILENAME` is all-zero on all 170 known multis.

   **Two things still open here.** That 239 is specifically the crash trigger is
   unconfirmed (one run, one read-back mismatch; each confirmation costs a
   physical F8 press). And the 0-204 domain was measured on FX1, which indexes
   EFFECTS — RV3/RV4 index REVERBS, a different list, and their bound is
   inherited rather than measured. Both flagged in the field notes; ask before
   re-running either.

3. **The `.M3` record offset and stride.** s3ked has the *wire* layout, verified
   on hardware (§11 Finding F), which is not the same claim as the *file*
   layout. mpc2emu has 169 multi files it cannot read a field in. **Derive the
   stride from the files, not the arithmetic:** `PRNAME`/`MULTINAME` are
   AKAI-charset text, their spacing is the stride, and it self-checks — every
   part name should be blank or name a program on the same volume.

**Why this outranks its 4.0 % file prevalence:** Akai documents offset 114
twice — `PFXSLEV` is "Not used" in the program header and "Effects send level"
in the part. The send level is inert where both corpus scans counted it and
live in a region neither had looked at (§213).

## Session state at 2026-09-11 16:15, written before a host reboot

**AKAI hardware state: NOTHING OUTSTANDING. Every edit restored and verified.**

Three RAM edit sessions today, all snapshot-then-restore with read-back
confirmation. None left anything modified:

- **Multi `FX1`** (§214, §215, §217). Probed to 205/239 and the crash band;
  restored to **33** and verified after each of two panics. All 16 multi parts
  byte-identical to the pre-probe snapshot except part 15 offset 109, which
  §200 established is the last-played-note byte and tracks playback.
- **`LSI2_ON`** on PRG 43/44/45, all 23 keygroups set to 0 for the
  single-variable board test (§222). Restored to `1` on every keygroup,
  read-back verified.
- **`ENV3` + `MODVFLT2_3`** on PRG 44, all 5 keygroups, for the filter-2 depth
  probe. Restored to the snapshot `(0,0,0,0,0,0,0,0)` and depth `0`; the script
  asserted `matches snapshot: True` before exiting.

Snapshots on disk if any doubt arises: `~/temp/s3ked-logs/multi_snapshot.json`,
`truectl_snap.json`, `fl2depth_snap.json`.

**Resident on the AKAI:** `FXPATHS` from HD4 partition B — 15 programs
(PRGNUM 0, 40-47, 50-55), 21 samples. This is the **corrected** build; the
sample-rate rebuild is confirmed on hardware (§220, gate check: PRG 45 note 65
gives 130.37 / 175.05 Hz against the E4XT's 130.83 / 174.73).

**Captures held**, all in `~/temp/matrix/captures/`: `GRIDv{16,48,80,104,127}_
0{40..45}` (the original 150-cell grid), `GRIDr80_*` (repeat pair for the noise
floor), `R{43,44,45,46,47}v*` (post-rebuild re-captures), `OFF{43,44,45}`
(board-off control), `D2_{0,12,25,50}_044` (filter-2 depth ladder), `LONG45`
(HOLD 20). Analyses in `akai_P000.json` … `akai_P005.json`.

**Where I was — all three staged volumes are captured and reported (§223,
§224, §225).** Each needed the flat/steady subject that the live `FXPATHS`
material could not provide: the original filter-2 depth attempt failed because
the subject's own late-minus-early change was 9.52 dB rms against the
modulation's 3.59, so the material moved more than the thing being measured.

```
  ~/temp/HD_atkcal.img    109-116   ATTAK1 ladder, steady sine        -> §223
  ~/temp/HD_poles.img     117-120   filter skirt, referenced saw      -> §224
  ~/temp/HD_f2depth.img   121-126   MODVFLT2_3 ladder, flat subject   -> §225
```

Resident on the AKAI at the end of the session: **F2DEPTH v2**, partition A.

## `MODVFLT2_3`: the negative half and the cents-versus-bytes law

**Status:** CLOSED by §226, figures revised by §227. F2DEPTH v3 was loaded from partition A on
2026-09-11 and captured; both open questions are answered and a third thing
was found. **~230 cents per unit, ±8** — §226's ~225 was the value at one
baseline-window position; sweeping that position moves it 220–236, the field
is **symmetric** (±4 excursions agree to within the method's resolution), and
the measurement is **linear within the ~15-20 cent scatter**. The history
below is kept because §226's main finding is about the instrument, not the
field.

§225 settled the positive side at **~220 cents per unit** from two rungs
agreeing to 1.1 %, and confirmed the deflattening method by landing depth 0 at
470.9 Hz against the generator's 476. Two things it could not settle:

- **The negative half below about −10.** −25 and −50 came back 76 cents apart,
  which is the measurement saturating rather than the machine: at `FIL2FR` 66 a
  −1900-cent excursion lands near 158 Hz with barely three harmonics of the
  55.1 Hz saw beneath it, so there is no passband left to reference.
- **Cents versus bytes.** One resolved rung at the second corner compares
  nothing.

mpc2emu has built **F2DEPTH v3** to that spec — 13 programs, every rung inside
the subject: `FIL2FR` 66 at depths 0/2/4/6/8/10 for linearity, `FIL2FR` 72 at
0/4/8 as the second corner, `FIL2FR` 80 at 0/−4/−8 for the negative half (a
high corner so the downward excursion still lands above the fundamental), and
one both-filters-open program as the deflattening reference. It costs one of
the five free PRGNUM slots.

**Two coincident pairs fall out of that ladder, 40 cents apart on paper** —
`FIL2FR` 72 depth 0 (720 Hz) against 80 depth −4 (737 Hz), and 72 depth 4
(1197 Hz) against 80 depth 0 (1225 Hz). The first crosses the sign of the
field and the second does not, so comparing the two isolates an asymmetry
between the halves.

**But neither pair separates the depth law from the byte→Hz table, and the
analysis must not be written as if it does.** Both pairs agree on exactly one
condition: that 4 depth units equal the `FIL2FR` 72→80 interval. At 220
cents/unit that is **880 cents**, while the two available tunings of that
interval are **920 cents** (mpc2emu's measured table) and **974 cents**
(§204's law) — a 54-cent spread between the sources, larger than the 40-cent
offset the pairs were designed around. A pair disagreeing tells you the
product is wrong, not which factor.

**The ladder already carries its own un-confounding, at no extra cost:** 72
depth 0 and 80 depth 0 are both in it, so the 72→80 interval is measurable
directly in the same session, against the same reference, before either pair
is looked at. Measure the interval first, then the pairs test the depth law
alone. Taking them in the other order buys a number that cannot be assigned to
anything.

A residual tilt in the deflattening is **not** what the pairs catch: both
members of a pair sit at the same frequency to within 40 cents, so a smooth
tilt corrects them almost equally and cancels within the pair. That is a
virtue — it is why the pairs test the field and not the reference — but it
means a tilt has to be caught somewhere else.

The size of that tilt is better taken from a measurement than from a model.
§225's deflattening put depth 0 at 470.9 Hz against the generator's 476 —
**19 cents absolute**, and the within-pair residual is a fraction of that.
It is an indication rather than a bound for v3, which deflattens against a
different reference (PRG 56, not 37); but v3 carries three depth-0 rungs at
`FIL2FR` 66 / 72 / 80, so it measures its own method's absolute residual at
three frequencies in the same session it needs it.

**And that residual measurement is uncontaminated by the gap it sits beside:**
none of the three depth-0 corners falls in the 45–64 hole. 66 and 72 sit in
dense parts of the table and 80 is itself a measured point, so a disagreement
there indicts the method rather than §FIL2FRGAP. (mpc2emu's check.)

## `FIL2FR` 45–64: a 19-byte interpolation gap, and one measurement inside it

**Status:** open, blocked on a card crossing.

`AKAI_FIL2FR_MEASURED` (mpc2emu's table) has points at 45 (106.9 Hz) and 64
(414.4 Hz) and **nothing between** — every other gap in that table is 6–10
bytes. §225's reference captures put `FIL2FR` 55 at **264.7 Hz** against the
table's interpolated 218.1, an 18 % disagreement, while the same method agrees
to 1.0 % at `FIL2FR` 66 where the table is dense. The provenance is the same
measurer, mode, `FLT2Q` and feature, so the two numbers are directly
comparable.

~~One point cannot separate "the table is wrong at 55" from "the region has
structure the table averages over".~~ **264.7 is withdrawn by §227**, not as
wrong but as *unmeasurable*: `FIL2FR` 55 has three harmonics below its corner
and a relative baseline window holds at most one. Reprocessed it gives 215.1
or 243.1 depending on the window — bracketing the table's 218.1, supporting
mpc2emu's window-proximity explanation in direction, and determining nothing.

~~The four-program run must be redesigned: put all four at a common
`MODVFLT2_3` offset of +8, lifting a 131 Hz corner into the denser part of the
comb.~~ **Both the original run and that redesign are withdrawn by §228.**

Doubling the harmonic density — note 24 instead of 36, `K_FREQ` and `K_FRQ2`
both 0 so the corners stay put — moves the window sensitivity **not at all**:
269 → 284 cents at `FIL2FR` 66 and 347 → 347 at `FIL2FR` 80. Sampling density
was never the constraint. The deflattened response has no flat passband to
reference, so the −3 dB corner is not a well-defined observable by this method
at any note or any corner frequency.

**What that costs this item:** the `FIL2FR` 66→80 interval moves ~70 cents
across the window sweep and §FIL2FRGAP needs 40. **This table cannot be
settled by the −3 dB-below-passband method**, so no card crossing should be
spent on it until a different observable exists — a fixed-slope crossing, a
fit to the whole transition, or an A/B against a program *known* flat rather
than assumed flat. That is the open work here now. Added as points,
never as a refitted curve: the first run of that table fitted one exponential
and byte 20 falsified it 46 % high.

~~If it holds, §204's filter-2 law wants labelling with the feature it
actually measures — §139 is the precedent for a peak-versus-corner factor.~~
**Withdrawn by §226.** §204 is a *single exponential* over bytes 25–88, and
this table already records that one exponential does not fit `FIL2FR` — the
first attempt was falsified 46 % high at byte 20. A known-wrong model
misfitting locally is that model behaving as recorded, not evidence of a
different feature. No peak-versus-corner factor is claimed.

§226 measured `FIL2FR` 72→80 at **113.1 cents/byte** against the table's 115.0
and §204's 121.8, so the table is within 15.4 cents over eight bytes. That is
agreement, and it is also why the 45–64 gap below stays a gap rather than
becoming a general doubt about the table.

**Unpushed commits: run `git rev-list --count @{u}..HEAD`.** §201 onward. Jan
has not given the word and I have not pushed.

## The attack table's ROM image: a named candidate, not confirmed (OPEN 2026-09-14 — §247)

**Status:** §245 left the boot-time writer of the RAM attack table at
`0x3A60:0x0892` unfound. A contents-first scan of the whole image — every
monotone geometric `uint16` run, no address assumptions — found eight tables
and gives §247 a named candidate at file `0x03AE92`: exactly 100 entries, the
same length as the decay table, and `0x03AE92 − 0x0892` is the paragraph
address of segment `0x3A60`.

**Not confirmed, for three reasons, all recorded in §247:**

1. that alignment is a **coin flip** — 0.40 that at least one of eight tables
   aligns, and two actually do;
2. the string-offset test built to confirm the base **scores a different base
   higher**, with no peak;
3. the contents are a **factor of 1.96 out** in exponent, and a bare factor of
   two with no mechanism is the shape of an indexing error.

**Blocked on:** a contents test rather than an address test — reading live RAM
at `0x3A60:0x0892`, which this protocol cannot do, or finding the copy itself.

**Not on anyone's critical path.** `ATTAK1`'s law is §234's, refitted from
hardware with ±2 % residuals; what a converter needs is §245's "stop using the
ROM decay table for attack", which does not depend on this.

The eight-table inventory in §247 is the durable part and is reusable for any
future "where does this curve live" question.

**§250 (2026-09-14) closed one of the eight exactly** and failed on the rest.
`0x024562` is 128 entries of `trunc(22050 * 2^((i-127)/12))` — one per MIDI
note, verified 128/128 with `22050` taken a-priori from §142 rather than
fitted. §247's scan had under-reported it as 109 entries from `0x024588`,
the detector's tolerance clipping nineteen entries off the head where values
of 14 and 15 make a log-slope meaningless — **the same clipping it did to the
decay table, so it is a property of the scanner, not the image.**

The other six resisted the identical treatment; three sit within 0.5 % of
`ln(2)/6` but the exact-form test gives 4/74, 5/58 and 4/52, so the
whole-tone reading is not supported. **An exponent is a lead; contents
matching a constant measured elsewhere is an identification.**

**Updated 2026-09-15 — §254 closes two more, by solving for the anchor instead
of guessing it.** The "4/74, 5/58, 4/52" above was the *search*, not the data:
`A` was taken from a short list. Solved as an interval intersection, two tables
fit exactly, and §247's extents turn out to have been the obstacle:

```
   §247 said                     actually
   0x03B2B0  214    ->   0x03B25C  256 entries  round, A = 31.999173, 25.49997 st/oct
   0x03B58E   74    ->   0x03B55C   99 entries  round, A =  1.615276,  6.02061 st/oct
```

**The tidy constants fail** — `A = 32` at exactly 25.5 gives 231/256, and
`A = 2` at exactly 6.0 gives 4/93. Both are so nearly round that the tidy
version writes itself, and this section's first draft did write it.

**Still open:** four tables with no form at all (`0x03ABEA`, `0x03AE92`,
`0x03D050`, `0x03D0D4`), and the *meaning* of the two just solved — a form is
not an identification, since neither carries a constant measured elsewhere the
way §250's `22050` did.

## Is the modwheel pivoted at 64 like velocity, or unipolar? (CLOSED 2026-09-14 — §249: UNIPOLAR)

**Status:** blocks the shape, not the numbers, of mpc2emu's modwheel → LFO
depth feature on the AKAI path. The offsets are answered (`MODVLVOL`, program
95, signed −50..+50; §248 has all sixteen `MODV*`).

§43 and §116 established that this machine references modulation to the
**middle** of the source's range — pivot solved at 64.56 across `V_LOUD`,
`V_ATT1`, `K_FREQ` and `MODVFILT1`. All four were driven by **velocity or
key**, sources with no neutral position. If the rule extends to the modwheel,
"fully wheel-gated" is not expressible: at wheel down the modulation swings
negative rather than to zero.

**The source enumeration argues it does not extend.** Exactly the three
external continuous controllers have inverted twins — `!modwheel` (11),
`!bend` (12), `!external` (13) — and velocity, key, the LFOs and the envelopes
do not. With a signed amount an inverted source is redundant for a bipolar
source and *not* redundant for a unipolar one. That is an inference from a
table's shape, not a measurement.

**To close this:** `MODSFILT1 = 1`, `MODVFILT1 = −50`, `FILFRQ` mid-range;
capture the corner at wheel 0, 64 and 127. **The discriminating rung is wheel 0
with a NEGATIVE amount**, where unipolar leaves the corner at `FILFRQ` and
bipolar puts it above. A positive amount does not discriminate — a negative
excursion may clamp at zero and look like no response under either model.

**Closed by §249, measured.** `corner(wheel 0) / baseline = 1.001x` where
unipolar predicts 1.000 and bipolar 3.61 — and at wheel 64, bipolar's required
neutral point, the corner is already down 7.70 `FILFRQ` units. There is no
neutral position anywhere in the wheel's travel.

The response is linear in `FILFRQ` units (max residual 0.027, intercept
+0.008), at **1.914 units per amount unit over full travel**. So `FILFRQ` is
the **wheel-down** value, not the centre of a swing, and a source asking for
"wheel opens the filter" writes the closed value with a positive amount.

**§43/§116 are untouched** — velocity and key still pivot at 64. The rule was
never wrong; it was being applied past the source types it was established on.

**One thing stays open and is flagged in §249:** this measured filter
frequency, not LFO depth. Source polarity ought to belong to the source rather
than the destination, but that is an inference of exactly the kind this item
existed to test.

**Also measured, and a converter hazard:** velocity is **2.32× stronger per
depth unit** than the wheel (0.002523 vs 0.001088 ln-Hz per depth×source unit).
A wheel amount computed from a velocity calibration is wrong by that factor.

## `MWLDEP`: a dedicated modwheel→LFO-depth path, and what else it hides (OPEN 2026-09-14 — §251)

**Status:** found by a **failed null check** while measuring something else.
`MWLDEP` (program 36, unsigned 0..99) and `PRSDEP` (37, aftertouch) reach LFO1
depth **outside the assignable matrix**, and the resident test program carried
`MWLDEP = 30` — so the first run's every number was the sum of two paths.

**What is settled (§251):** `MODVLVOL` is unipolar, matching §249's
`MODVFILT1`, so wheel polarity is a property of the **source**, not the
destination. Sensitivities agree to 2.2 % across the two destinations (1.914
vs 1.956 units per amount unit over full travel).

**What this opens:**

1. **A corpus reading is wrong wherever it was done.** `MODSLFOL = 0` does not
   mean "no wheel vibrato" — `MWLDEP` must be read too. Any count of modwheel
   routings taken from the assignable matrix alone under-reports.
2. **Are there other dedicated paths?** `MWLDEP`/`PRSDEP` were in our own
   parameter table the whole time and nobody had connected them to the
   modulation question. A sweep of the table for *any* field whose description
   names a controller and a destination would say whether these two are the
   only ones. Free, no hardware.
3. **`MWLDEP`'s own scale is unmeasured.** Leg B was compressive and the
   compression is probably the corner clipping at `FILFRQ` 99, not the field —
   so no units-per-`MWLDEP`-unit figure is claimed. A run with a smaller
   `MODVFILT1` would settle it.

**Updated 2026-09-15.** (1) is **answered by mpc2emu's corpus scan**: `MWLDEP`
is non-zero in **213 of 213** programs, 202 of them at exactly 30, while
`PRSDEP` is non-zero in 2. The dedicated route is used 213 times and the
assignable one once, so their earlier 24-assignment count was counting the
wrong mechanism — and this project's confounded first run was not bad luck,
since 94.8 % of programs carry the value that broke it.

**Scale-versus-add is also answered, from captures already taken** — §251's
leg B *was* that experiment. `B(wheel 0)/A(wheel 0) = 0.9998` where adding
predicts 1.000 and scaling 0.495: **`MWLDEP` adds.**

**(3) CLOSED 2026-09-15 by §255: `MWLDEP` is 1:1.**
`depth = min(99, LFODEP + MWLDEP · wheel/127)`. Leg A gives 0.9802 units per
`MWLDEP` unit with headroom; **the 1:1 is carried by leg B's plateau**, where
wheel 96 and 127 read identically at 95.3 because the sum clamps at 99. A 0.5
rule predicts a final 95 — the *values* nearly agree and only the *shape*
separates them. §251's own explanation of its compression was refuted by its
own numbers and the cause was the instrument, not the corner clipping.

~~**(3) is the only open part, and it is the one a converter needs**: the
magnitude.~~ The clipped ladder gives a **lower bound** of 0.476 `LFODEP` units
per `MWLDEP` unit; the least-clipped rung extrapolates to ~0.86, which is
consistent with a 1:1 rule and is **not** grounds for adopting one. Three
minutes on the rig with a smaller `MODVFILT1`.

**Blocked on:** nothing for (1) and (2). (3) needs the rig, RAM only.

## The ±50 rail is path-dependent, and §109 needs its path named (OPEN 2026-09-15 — §256)

**Status:** raw byte writes of 90 and 166 to keygroup offsets 151–155 all store
**verbatim**, and 90 at `+155` survives two seconds, a program change and
sounding the voice. The machine does not enforce ±50 on the byte-offset path.

§109 records the opposite at the same offset — `MODVFILT1` *"clamped to ±50 by
the machine: a write of 90 read back as 50"*. Both can stand: the clamp is a
property of the **write path**, exactly as §13a found for the
delete-on-duplicate-name rule.

**CLOSED 2026-09-15: there was no opcode.** mpc2emu has no AKAI
parameter-SysEx path at all — every AKAI measurement of theirs goes through a
generated disk image, loaded by Jan, played and captured. So §109 was
`disk image → load → panel read` and §256 is `byte-offset SysEx write →
read back`. **The clamp lives on the load/panel path and is absent from the
byte path**, which is §13a's shape for the third time on this family. Both
sections were always right about different things, and the path is now recorded
in §109 beside the measurement.

**CLOSED 2026-09-15: nothing convicts the offset.** Both arguments against
`+155` fell. mpc2emu's shape comparison died to a caveat this project wrote and
neither side tested — `MODVAMP2` is a loudness mod amount of unquestioned
identity with 392 non-zero values and **0.00 %** on the rail, so a missing pile
means nothing. This project's counter-hypothesis (that `+155`'s rail sits at
127, making it a `HIVEL`-like limit field) died to the high-bit test: a
`0..127` field cannot set bit 7 and the four `HIVEL` fields never do across
209,592 slots, while `+155` does on 0.49 %. It is signed, as `MODVAMP3` should
be, and the 24-byte zone stride would put a fifth `HIVEL` at 143 anyway.

`+155` is consistent with `MODVAMP3`, transcribed from Akai's own document —
mpc2emu had mis-stated it as unsourced, which is how it came to be attacked
twice. The SysEx keygroup diff was offered and is **not needed**.

**Consequence for this project:** `params.py` ranges are **client-side
validation only**. The encoder refuses out-of-range values — which is why §109's
experiment cannot be reproduced through `set_parameter` — but the machine
accepts whatever `set_header_bytes` sends. The range in the table is s3ked's
promise, not the machine's, and any doc wording implying otherwise is wrong.

## LFO2's rate law, and `MODVPAN1`'s depth law (CLOSED 2026-09-17 — §257)

**Status: measured, both.** mpc2emu asked for a pan-depth calibration after
their E4XT conversion swung 39.21 dB where the MPC original had 5.11.

**Rate:** `rate = 0.11840 · PANRAT + 0.0108 Hz`, fitted over six rungs, which
is **LFO1's law to 0.1 %** — the two LFOs share one rate law, cross-checked
against §255's LFO1 figure to 1.3 %.

**Corrected the same evening:** this entry first said mpc2emu's writer emitted
double-rate programs. **It does not.** Their live constant is `0.11913`, in use
since 2026-09-06 and 0.6 % from this fit; `0.23708` survives only in comments.
The direction was inverted as well — the conversion divides, so the old law
would have *halved* the rate. **A stale comment was read as the code, with the
constant one grep away** (§188's failure). This measurement's worth is as a
**third independent arrival** at the same law, through pan rather than the
filter, where the refuted figure came from a magnitude detector counting
half-cycles of a bipolar sweep.

**Depth:** ~**1.0 dB peak-to-peak per `MODVPAN1` unit** at the low end, rising
to 1.26 dB/unit by byte 20. Not linear, not interpolatable. Null taken twice
(first and last) at exactly 0.00 dB with a 0.000 off-LFO floor, on a mono
source with L−R correlation +1.0000.

**Saturation shows in the residual, not the swing:** off-LFO rms 0.167 → 0.482
→ 2.522 → 24.317 dB at bytes 2, 6, 20, 50. Byte 50's `166.68 dB` is a near-zero
denominator, not a measurement.

**Open, and mpc2emu's:** whether their writer's fraction-of-rail convention
(`round(depth × 50)`) is replaced by something anchored on these numbers. A
source wanting 5.11 dB needs `MODVPAN1 ≈ 5`; the writer emits 32.
---

## `Scale` records why a range stops but not what it was measured on (OPEN 2026-09-20 — §259)

**Status:** found by generalising a gap mpc2emu found in their own
`docs/calibration.json`, where `_AK_DECAY1_RATE` carried
`provenance_gap: true` with `method`, `subject`, `date` and `detector` all
null. s3ked has the same hole, structurally rather than by oversight.

`Scale` has `region, param, unit, kind, a, b, fitted, r2, provisional, note,
bounds, endpoints`. **There is no field for the instrument the law was
measured on.** Surveying all 36 measured laws:

    name a section (§n)      23 / 36   (64%)
    name what was measured   17 / 36   (47%)
    have no bounds text       0 / 36

**The contrast is the finding.** `bounds` is 100% complete because the
dataclass documents it as required, with mpc2emu's own `DECAY1 0..99` as the
cautionary tale attached. Source is 47% complete because nothing ever asked
for it. A requirement written into the class got obeyed 36 times out of 36;
the one that was never written got skipped on half.

**What it cost, concretely:** §30 does not name its source. Its only remark
about it — "the sample's own slow decay competes with the envelope" —
describes something that is *not* flat noise, and two sibling sessions spent
this week relying on a second-hand claim that it was noise. §259 no longer
depends on that, but it depended on noticing.

The 19 with no source: `program.PRLOUD`, `program.LFORAT`, `keygroup.RELSE1`,
`keygroup.ATTAK2`, `keygroup.DECAY2`, `keygroup.K_DAR3`, `keygroup.V_REL3`,
`keygroup.O_REL3`, `keygroup.V_ATT3`, `keygroup.VLOUD1`, `keygroup.FILQ`,
`keygroup.ENV3R2`, `keygroup.ENV3R4`, `keygroup.ENV3R3`, `program.V_LOUD`,
`program.MWLDEP`, `program.PRSDEP`, `program.LFODEL`, `program.PANPOS`.

**Blocked on:** nothing for the mechanism — add a `measured_on` field and a
test that requires it, exactly as `bounds` is required. **Backfilling is the
part that needs judgement:** some are recoverable from their sections, some
are not, and an entry whose source cannot be recovered must say so explicitly
rather than be left blank — a blank reads as "nobody needed one", which is
how this happened. mpc2emu's `provenance_gap: true` is the right shape.

**A second field of the same shape: residual spread.** Every `Scale` carries
an `r2` and none carries the worst per-point residual. eosed found their own
law quoted at r2 0.998042 concealing a -16.1% point; the same challenge
applied to §259's fresh 25..99 fit found **r2 0.99989 concealing +7.45%** at
byte 96 (p5..p95 -2.36%..+3.48%, rms 2.36%). An aggregate r2 over a dozen
points in log space cannot show a bad one, which is the same reason §30
insists on per-curve fit quality rather than an average. A converter reading
`r2=0.99989` and sizing a tolerance from it would be wrong by an order of
magnitude.

**Not started — this is a change to a shipped dataclass and 36 entries, and
it wants Jan's word before it is made.** Two fields now, `measured_on` and a
residual summary; both are the same edit and should land together.

## §118's other two re-measures are unconfirmed, and two peers were quoting it (OPEN 2026-09-20 — §259)

**Status:** §259 refuted §118's `DECAY1` sine re-measure (0.08781 against a
standing 0.09776; a fresh 20..99 sweep returned 0.09728, and the residual has
no slope against the byte, which an exponent error must have). §118's
conclusion was **"all three reproduce"**, so the other two rows inherit the
same doubt and neither has been retested:

    ATTAK1   0.10844 -> 0.10862   40..99   r2 0.99901   0.17%   plausible
    RELSE1   0.09683 -> 0.09226   20..70   r2 0.99941   4.7%    UNCONFIRMED

`ATTAK1` at 0.17% is a genuine reproduction and needs nothing. **`RELSE1` at
4.7% is the one to check** — too large to be scatter, too small to have been
caught by the same eye that let 10% through.

**Why it matters beyond s3ked:** mpc2emu and eosed have both been reading
§118 at second hand this week. Both have been told to treat its numbers as
unconfirmed, and that instruction needs discharging rather than standing
forever.

**Blocked on:** the rig, and it is the same sweep — `RELSE1` 20..99, one
source, one dB-slope estimator, per-curve r2 reported. §259's rig time was
about seven minutes of capture; this is the same shape. Note `RELSE1`'s span
is not a property of its value (§30): a release starts wherever the note had
got to, so the probe must set a known level before note-off.

**Also open, cheap, no rig:** §30 does not name the source it measured on, and
its one remark about it ("the sample's own slow decay competes with the
envelope") describes something that is not flat noise. Two sessions were
relying on a second-hand claim that §30 used noise. §259 no longer depends on
it, but the section should say what it measured.

## Two resident-object ceilings, 255 samples and 254 programs (OPEN 2026-09-20 — §258)

**Status:** mechanism solved from the firmware, and Jan's observed failure now
attributes to it. What remains is a live confirmation and one static question.

`0x72F6` counts resident **samples** and `0x72F4` resident **programs**, in a
1006 x 192-byte directory at linear `0x90000`. Six sites cap samples at
**255** (`cmp byte [0x72F6],0xFF`), four cap programs at **254**
(`cmp byte [0x72F4],0xFE`), and two are the ordinary `GROUPS`+1 pool check.
Every other comparison on either counter is against 0. Keygroups (type 2)
have no counter and no ceiling of their own.

The volume that failed needs 13 + 205 + 271 = **489** entries against **533**
free — the pool had 44 spare and its check passes. 13 programs is far under
254. **Only the 255-sample guard fires**, so the refusal attributes to it.
(The first write-up used 395 keygroups and concluded the opposite; that number
was mpc2emu's parser counting velocity zones, corrected to 205 the same day.)

**Blocked on:** Jan, and the rig — a live confirmation, one variable. Either a
built volume of 1 program / 1 keygroup / **260 samples** (262 entries against
533 free, crossing cleanly), or mpc2emu's zero-build version using the card
already in the machine: cumulative distinct samples cross 255 between the 11th
and 12th program (252 -> 263), so load eleven then add the twelfth. The
zero-build form crosses *inside* the twelfth program's sample set and so
leaves it partially loaded on refusal; the built volume does not.

**Mostly closed 2026-09-20, static:** exactly one instruction in the image
stamps type 3 (`0x17E32`), and the guard at `0x17DFF` dominates it — no
`rel8`, `rel16` or far transfer enters the span, verified by disassembling
every candidate (six were the byte `0x7C` inside `movw $imm,0x7Cxx` operands,
not branches).

**Narrowed from fourteen sites to ONE, 2026-09-20.** The structural question
was not where `ES` points but which copies **allocate** a slot; the allocator
has two entry points, `0x24d1:0x0e96` (file `0x25BA6`) and `0x24d1:0x0ec5`
(`0x25BD5`). Seven of the fourteen call no allocator and create nothing. Six
more are guarded correctly — `0x12EC7`/`0x12EE0` and `0x13555`/`0x1356E` are
program duplication that walks the source's keygroup chain and so propagates
types 1 and 2 only, and `0x12FDD`/`0x135AA` sit directly behind the sample
guards at `0x12FBB`/`0x13582`.

**`0x177B6` is not guarded.** It allocates a slot behind only a flag test
(`testb $0x20,0x7ca0`), then copies the shared `0xA1B0` staging buffer into it
with no restamp of byte 0, inheriting whatever type that buffer holds. The
three stamping sites all overwrite byte 0 after copying, which implies the
buffer's own type byte is not trusted; this path does not.

**`0x177B6`'s type is UNDETERMINED — two attributions attempted, both
withdrawn.** First type-3, then type-1 on the grounds that it calls `0x12873`,
"the program recount". **`0x12873` is not a program recount**: it counts type 1
into `0x72F4` and then, at `0x128A9`, calls `0x3520:0x0000` — the sample
counter — and reads `0x72F6`. A combined catalogue refresh tells you nothing
about which type was created, and `0x135AA` proves it by sitting behind the
*sample* guard and calling `0x12873` too. Checked by reading the first twenty
bytes of the routine and not the next twenty.

What survives both corrections: **`0x177B6` is unguarded against BOTH
ceilings** — no program guard (`0x12E77`, `0x17C51`, `0x1AFE1`, `0x1B46D`) and
no sample guard (`0x12FBB`, `0x13582`, `0x1752F`, `0x17DFF`, `0x1A7C4`,
`0x1AA55`) is anywhere in `0x177xx`.

**A separate, SOUND finding from the same pass:** the program-duplication
routine at **`0x13533`** checks the pool (`GROUPS`+1 against free capacity,
refusing at `0x1353D`) and **never checks the 254-program ceiling** — nearest
`0xFE` guard is 1758 bytes away in an unrelated routine. This rests on
structure, not on the withdrawn discriminator: it reads `GROUPS` to size the
allocation and walks the `[si+1]` keygroup chain, which is what a program
duplicator does whatever it calls afterwards.

Note `0x12873` **recounts from scratch** rather than incrementing, so `0x72F4`
cannot drift — a low-byte wrap needs 256 genuinely resident programs, which
the 1006-entry pool could hold (256 programs at one keygroup each is 512
entries). Unusual, not impossible.

**Still open:** dominance for `0x12EC7` (guard `0x12E77`, 80 bytes, 2 candidate
entries), `0x17C7E` (`0x17C51`, 45 bytes, 6) and `0x1B0C7` (`0x1AFE1`, 230
bytes, 9). Those counts are raw scan output and are expected to be mostly
operand bytes, as the `0x7C` candidates were.

Also corrected: `0x177AA` has **three** entries, not one — a call from
`0x177A1` plus jumps from `0x1D88F` and `0x1DA16`. The first scan omitted
`jmp rel16`.

**No longer a pure disassembly question.** The buffer is genuinely shared —
`0x17C5B` reads `GROUPS` from it, `0x17E13` reads sample fields from it, eight
sites write it and thirteen read it. Settling it needs the template at
`DS:0x1531` versus whatever fills the buffer on the `0x17786` path, and `DS`
is not resolvable statically here. **Blocked on** either resolving the data
segment or a live read.

Still unscanned and unscannable by byte pattern: indirect `jmp`/`call`
(`ff /4`, `ff /5`) and dispatch tables. Same question unexamined for the four
program sites at `0xFE`.

**Neither project models either ceiling.** s3ked does not build volumes, but
mpc2emu's writer should refuse >255 samples or >254 programs rather than let
the machine discover it.

## Split `bridge.py` — 3,053 lines, and `S3kBridge` is 2,218 of them (OPEN)

> **The reviewer was shown these corrections and agreed, in its own words:**
> *"bridge.py is genuinely large and could be split. The observation is real;
> the numbers and class names around it were fabricated."* — and the same for
> the other two, structure sound, framing wrong.
>
> That concession is worth more than the items. It means the three below are
> **independently arrived at twice**, which is why they survive at all; and it
> names the failure exactly — **a sound structural intuition with fabricated
> specifics wrapped around it.** The shape was worth reading. Every number,
> class name and line reference in it was not, and none announced itself:
> `MidiTransport` and `_write_struct` read exactly like `ThrottledOut` and
> `_misc_word`, which are real.
>
> **So: keep the shape, check every particular.** A review that is right about
> what to look at and wrong about what is there costs more than one that is
> simply wrong, because the correct part lends the invented part its
> credibility.

**From an external code review (MiMo V2.5 Free), corrected against the file.**
The review called this "the single largest file in the project"; it is the
**third** — `tests/test_app.py` is 4,906 and `s3k/params.py` is 3,355. It also
proposed extracting a class called `MidiTransport`, which does not exist here.

The underlying observation survives both corrections. `S3kBridge` alone runs
from line 835 to the end — **2,218 lines and 68 methods** — carrying transport,
config, disk browsing, load/save, renumber, undo and multi.

**The boundary that actually exists**, and it is already clean:

```
   ThrottledOut      408    the paced output port
   MultiIn           464    the reassembling input
   _enum_in/_enum_out/list_ports/bidirectional_ports   359..380
```

Those three touch nothing above them. A `s3k/transport.py` holding them is a
move, not a redesign.

> The config readers and writers (`_read_config` … `save_exclusive_channel`,
> 205..326) look like they belong with the transport because they sit beside
> it, and do not — they persist *choices about* ports, not the ports. Splitting
> on the file's layout rather than its dependencies would take them along.

**Blocked on:** nothing. All 979 tests should pass unchanged, and a split that
needs a test edited is a split that moved a behaviour.

---

## Three `MISCDATA` writes bypass the helpers that exist for them (OPEN)

**From the same review, and it is right for none of the reasons it gave.** It
asked for a `_write_misc` helper "because the pattern repeats"; three such
helpers already exist — `_misc_word` (1454), `_misc_byte` (1563) and
`_misc_write_verify` (2043) — and the `_write_struct` it says they dispatch
through is not in the codebase.

**The duplication is real, in the callers that skip all three:**

```
   1771   trigger_load
   1946   rename_volume
   1967   _fire
```

each building the identical shape by hand:

```python
   frame = m.HeaderData(
       command=m.Command.MISCDATA, index=..., selector=..., offset=0,
       data=..., exclusive_channel=self.exclusive_channel,
   ).encode()
   self._drain()
   self._send(frame, write=True)
```

**The `_drain()` is the part that matters.** It is not decoration — it clears
the input before a write so a stale reply cannot be read as this write's
acknowledgement. A fourth caller written by copying one of these three will
work; one written from scratch may omit it and fail only under timing.

**Blocked on:** nothing. Internal, no public API change.

---

## Replace the lambda signal handler with a plain function (OPEN)

**From the same review; the conclusion is right and the reason it gives is
wrong.** It cites the `signal` documentation on lambdas not being picklable
under `multiprocessing`. **Signal handlers are never pickled** — `fork` inherits
them and `spawn` re-imports the module and installs its own. That warning is
about named lambdas generally, and does not apply here.

The readability argument stands on its own. `bridge.py:557` installs:

```python
   lambda signum, _frame: (_ for _ in ()).throw(SystemExit(128 + signum))
```

`(_ for _ in ()).throw(...)` is a generator-expression trick for raising inside
an expression, because a lambda cannot contain a `raise`. A four-line `def`
says the same thing plainly, and the `128 + signum` convention deserves a
comment rather than being buried in an idiom.

**Blocked on:** nothing. No behaviour change — same exception, same code.

**Not to be lost in the rewrite:** the surrounding function is careful in a way
that is easy to undo. It installs only where `getsignal` returns `SIG_DFL`,
leaving any handler the host application already owns; and it tolerates
`ValueError`/`OSError`, because `signal()` only works on the main thread of the
main interpreter. Both are deliberate.

---

## Cross-project: eosed and mpc2emu contradict each other on EOS envelopes (OPEN 2026-09-20)

**Status:** surfaced from here because both sent their findings to this
session within a few hours. Not s3ked's to adjudicate, but s3ked should not
cite either until it resolves.

- **mpc2emu**, from 1161 matched voices: the implied EOS decay span is
  **29.99 dB**, stable while their own span varied a hundredfold across
  sustain buckets. Written up mechanistically — *"EOS computes them over a
  fixed reference of about 29-30 dB"* — and flagged as suggestively close to
  E4B's `_ENV_SHAPE_KNEE_DB = 29.0`.
- **eosed**, from decompiling EOS 4.70's importer: it does **no time
  arithmetic** on envelopes. Attack is a 100-entry table at `0x303d0`, decay a
  table at `0x30434`, release the *same* table as decay, sustain a plain
  `round(clamp(v,0,99)*127/99)`, with `Dcy1 rate = 0` and `Dcy1 level = 127`
  hardcoded.

**Both can describe the same table; only one describes the importer.** If the
conversion is a byte-to-byte lookup, ~30 dB is an emergent property of the
table's *contents* — plausibly because its generator assumed 30 dB, which
would make mpc2emu's figure a correct inference about the table's **origin**
and a wrong one about the importer's **behaviour**. The sustain sweep itself
is unaffected: a denominator moving 100x with a stable quotient still says the
effective span is ~30 dB whatever produces it. Only the mechanism clause is in
question, and mechanism clauses are what other projects build on.

**The discriminator, no rig, belongs to whoever holds the files:** index the
decay table at `0x30434` with the AKAI decay bytes from a sample of the 1161
voices and compare against the EOS values those voices actually carry. Exact
match means the table is the whole mechanism; a mismatch means something
computes after it.

**Relevance to s3ked:** none of our constants depend on this. It matters only
if §259's `DECAY1` law is ever cited alongside an EOS comparison — and it is a
live instance of the rule that a fit to a non-existent quantity still returns
a number (eosed's phrasing).

**Blocked on:** eosed and mpc2emu. Both notified 2026-09-20 21:30.

## Three loudness fields are grouped under `program.pan` (OPEN 2026-09-20)

**Status:** found sideways, answering eosed's request to name fourteen AKAI
program-common offsets for their EOS importer. Cosmetic, real, and it does not
touch offsets, ranges or encoding.

`program.pan` holds `V_LOUD` (0x1A, "Note-on velocity dependence of
loudness"), `K_LOUD` (0x1B) and `P_LOUD` (0x1C) alongside the genuine pan
fields. `PRLOUD` (0x19), the loudness those three modify, is in
`program.output`. `group` drives editor page selection (`group_params`,
`params.py:3152`) and the param label (`app.py:836`), so **in the TUI the
velocity-to-loudness control appears on the Pan page, away from the loudness
it acts on.**

Likely cause: the boundary was drawn by offset rather than by meaning —
`PRLOUD` at 0x19 is output, `PANRAT` at 0x1D is pan, and 0x1A-0x1C fell to the
pan side. `PANRAT`/`PANDEP`/`PANDEL` genuinely are pan (LFO2 is the pan LFO),
so only 0x1A-0x1C are misplaced.

**Blocked on:** nothing. Two of the three are documented "Not used" (range
0..0), so only `V_LOUD` is reachable in practice. A test pins entry counts per
region but nothing pins group membership, so this changes no test.

**Checked 2026-09-20, and it does NOT generalise — `V_LOUD` stands alone.**
Every parameter in every region was scanned for a description naming a concept
its group contradicts. Restricted to the groups that are **kind**-based, three
candidates came back and two are not defects:

- **`SPFILT` 0x40 in `program.output`** — not an error. `SPLOUD` (0x3E),
  `SPATT` (0x3F) and `SPFILT` (0x40) are the three soft-pedal parameters and
  are grouped together as one functional set. Keeping a loudness, an attack
  and a filter control together is the right call for a feature; only the
  group's *name* is imperfect. Deliberate, not a boundary drawn by offset.
- **`multipart.PANPOS` 0x18 in `multipart.output`**, against
  `program.PANPOS` 0x18 in `program.pan` — same name, offset and range, two
  different groups. But the multipart region has **no `pan` group at all**
  (only general/midi/output/pitch across 13 parameters), so this is a coarser
  scheme rather than a misplacement; a `multipart.pan` group would be a page
  with one control on it. Worth knowing only because someone who finds
  `PANPOS` on the Pan page for a program will look for a Pan page for a
  multipart and not find one.

**The check itself needed fixing before its output meant anything**, which is
the reusable part: the first pass returned **69 rows**, almost all noise,
because it took the group's last dot-component as the kind — making
`keygroup.env.1` read as kind `1` — and because `zone.N` and `env.N` are
indexed by *which*, not by *what kind*, so they legitimately hold tuning,
loudness and filter offsets side by side. Excluding the index-based groups and
matching on the full path took it from 69 to 3. A check that emits rows it
cannot interpret is worse than no check; this one emitted 69.

## External code review — GLM-5.3-Flash, 2026-09-20 (OPEN — triaged against the files)

**Status:** every finding below was re-checked against the code on
2026-09-20, not taken from the reviewer's line numbers. **The line numbers
were accurate throughout** — unlike the MiMo review, nothing here was
fabricated. Four findings are sharpened, one is demoted, one has its severity
lowered, and one was understated by the reviewer. Nothing had to be dropped.

Convention compliance passed independently: arm-then-fire gating present,
`--demo` never constructs a bridge, GPL headers, no commercial-library names.

### Sample delete deletes a different sample (CRITICAL — CONFIRMED, fix ready)

`_confirm_destructive` (3346–3350) takes `#samples`.`cursor_row` and hands it
to `bridge.delete_sample(target)` as a **resident index**. In
`_fill_program_samples` the pane rows are `missing + present` — the selected
program's *used* names in usage order — which has no relation to
`self._samples` order.

Sharpened against the file, three ways the reviewer did not state:

1. **The diverging scope is the default.** `self._samples_scope = "program"`
   at 1230. The pane agrees with the resident list only in `"all"` scope,
   which the user must press `a` to reach.
2. **The file already documents the trap.** `action_usage`'s docstring (1822)
   says in as many words: *"That list and this pane were the same thing until
   the pane became program-centric; indexing one by the other's cursor now
   picks a different sample entirely, and would do it silently."* The delete
   path is the case that was missed when that was written.
3. **The guard is the wrong guard.** `if not self._samples` tests the
   *resident* list, not the pane. A program that references nothing leaves the
   pane empty while the test passes, and `cursor_row` then names a resident
   sample nobody selected.

`self._samples[target]` in the dialog text also raises `IndexError` on the UI
thread whenever the used-list is longer than the resident list — which is the
MISSING-heavy case, i.e. exactly when it matters.

**Fix, and it exists twice in the file already:** resolve by name off the row,
as 1822 and 3544 do. One addition both of those can afford and this cannot —
3544 says *"showing the first"* when a name matches several resident samples.
**A delete must refuse instead**, per CLAUDE.md's rule that the machine
enforces no name uniqueness and objects are addressed by index.

**Blocked on:** nothing. Synthetic — `DemoBridge` plus a scoped pane
reproduces it. Regression test must assert the *resident index*, not the row.

### `bridge.status()` on the UI thread, outside the lock (MAJOR — CONFIRMED)

`_show_volumes` (2779) calls `self.bridge.status()`. Its **only** caller is
`call_from_thread` at 1737, so it always runs on the event loop. Every other
bridge call in the file is in a worker under `_bridge_lock` — this is the sole
exception, and 1732 shows the calling worker taking and releasing the lock
immediately before, so the unlocked call can interleave with the next worker.

**Blocked on:** nothing.

### Destructive ops reachable against hidden panes (MAJOR — CONFIRMED)

`Binding("m", "master", ...)` (1125) is global. `_show_multi_pane` (3163) sets
`display = False` on `programs`, `keygroups` and `samples`.
`_confirm_destructive` then reads `cursor_row` from hidden tables. The dialog
names a target, but the target is off-screen. Compounds the critical finding.

**Blocked on:** nothing.

### `DemoBridge.clear_memory` leaves header stores untrimmed (MAJOR — CONFIRMED)

`s3ked/demo.py` (333–339) empties `_samples` and truncates `_programs`, but
`_program_headers` (128), `_keygroup_counts` (126) and `_keygroup_headers`
(129) are untouched. A header read for a cleared program returns stale data
where the machine errors — and the demo's whole contract is that its answers
match the machine's.

**Blocked on:** nothing.

### Sharpened beyond what the reviewer claimed

- **`SHIDENT` is a three-way inconsistency, not a two-way one, and §258
  raises the stakes.** `PRIDENT` is `1..1` readonly; `KGIDENT` is `2..2` but
  **writable**; `SHIDENT` is `0..255` and writable — pinned, half-pinned, not
  pinned. And §258 (2026-09-20) established that byte 0 of each header **is
  the firmware's directory type code**: the counting loops at `0x12873` and
  `0x35200` dispatch on it, and the ceilings at `0x72F4`/`0x72F6` count what
  it says. A write to offset 0 of a resident header would mis-type the entry
  in the machine's own directory. Whether the machine accepts such a write is
  untested and needs the rig; pinning all three costs nothing either way.
- **The `params.py` module docstring is staler than reported.** Counts are
  84/132/35 against an actual 85/130/35 (`tests/test_params.py` pins 85 and
  130, with the 85 explained as `PRIDENT` from hardware, §14). It also says
  **"Three regions exist"** — there are five; `multi` (6) and `multipart` (13)
  are missing from the table entirely.
- **`Postpone.RECALC` deserves more than minor.** `header_data` (2547) and
  the second write API (2653) pass `postpone` straight into the frame (2578,
  2669) with no guard, warning or clearing follow-up. Its own docstring (2561)
  says `RECALC` "must never be left set", and **CLAUDE.md names it as one of
  the two protocol facts that shape this code** — bit 12 leaves the machine in
  an undetermined state until a later write clears it. Nothing in-repo sets it
  today, so this is a latent API hazard, not a live bug.
- **The `demo.py` rtmidi reach-through is eight sites, not two.**
  `s3k.bridge` does `import rtmidi` at module scope (bridge.py:71).
  `s3ked/demo.py` imports it inside functions at **236, 270, 507, 595, 596,
  689, 716 and 736**, against its own comment at 315 ("not import s3k.bridge,
  which pulls in rtmidi"). Three of those reach private API — `_Volume`,
  `_DirectoryEntry`, `_selector_for`.

### Confirmed as written

- `s3k/analysis.py` 519–521: the `continue` on an unread program skips
  `progress(index + 1, len(names))` at 545. Confirmed.
- `s3k/analysis.py` 532–535: per-zone `except Exception: continue` with no
  `audit.unread` counterpart, where the program-level path at 520 does record
  one. A transient timeout undercounts references and can report a sample as
  an **orphan**, which is this module's one job.
- `s3k/params.py` `STUNO` (sample, offset 20): `0..65535` where every sibling
  2-byte tuning field (`PTUNO`, `KGTUNO`, `VTUNO1-4`) is `-12800..12800`.
- `s3k/params.py` `models`: no reader anywhere outside `params.py` — grep over
  `s3k/`, `s3ked/` and `tests/` returns nothing, while `requires` is
  bridge-enforced. Advisory in fact; the docstring should say so.
- `s3k/bridge.py` 1225: the *wait* correctly uses
  `self.timeout if timeout is None else timeout`, so `timeout=0` means 0 — but
  the message says `{timeout or self.timeout}` and reports the default. The
  code is right and only the error lies.
- `s3k/bridge.py` `refresh_media`: docstring promises the volume is "clamped
  to what the new medium actually has"; the code does `if wanted < available`
  and otherwise leaves it at 0. Falling back to 0 is arguably the safer
  behaviour — **fix the docstring, not the code**.
- `s3ked/app.py` `_remove_leftover_worker`: `if not marked: break` means
  *already gone*, and falls through to the branch reporting
  `"<marker> is still resident … delete it from the Master screen"`. The
  comment above it says "Never claim it went when it did not"; the code
  claims it did not go when it did.
- `s3ked/app.py` `_nudging`: cleared only in `_after_write` (3014), and
  `_write_param_worker`'s `except Exception: notify; return` (2945) never
  reaches it. A failed write leaves the nudge set for the next edit.
- `s3ked/app.py` undo: `action_undo` pops at 3062 **before** calling
  `_write_param_worker` at 3064, so a failed write loses the entry silently;
  `_undo_all_worker` reads `[-1]` at 3089 and pops at 3100 **after**. The two
  disagree, and the all-path is the correct one.
- `s3ked/app.py` `_select_source_worker` (2021): defined, never called. Live
  path is `_source_change_worker` (1956, called at 1953).
- `int(x, 0)` at `s3ked/cli.py` 260 and `s3ked/app.py` 2987: confirmed by
  language semantics — `int("010", 0)` raises, `int("10", 0)` is 10. A
  zero-padded decimal is refused as "not a number".
- `s3ked/cli.py` `_build_bridge` (406): `--demo` silently ignores
  `--port`/`--exclusive-channel`/`--timeout`. Not re-read line by line; the
  reviewer's other numbers all held.
- `s3k/scales.py` `stacked()` (288–297): greedy join may over-merge through an
  OMNI bridge. Not re-checked; conservative direction for §135's purpose.

### Severity lowered

- `s3k/scales.py` `from_physical` (1043): the docstring promises "rounded
  **and clamped** to the parameter's own limits" and the code only rounds —
  `from_physical("keygroup", "KGTUNO", 1e9)` returns **2560000000**.
  Confirmed. **But `encode_field` refuses out-of-range** (`KGTUNO: 2560000000
  is outside -12800..12800`), so the cost is a confusing error, never a bad
  write, and the returned `within` flag is already `False`. Doc bug, not a
  hazard. Clamp or fix the docstring.

### Demoted to a nit — the one finding that did not hold up

- `s3k/params.py` `lookup()`: the reviewer claimed dead code at 3118 shadowed
  at 3121, and a multi-region fallback "contradicting its own docstring".
  **Both halves are wrong.** Nothing at 3118 is shadowed — `matches` is read
  at 3119 and 3123. And the docstring says a bare name resolves "only when it
  is unambiguous", which a multi-only name *is*; it is the inline comment's
  word "opt-in" that overstates `candidates = primary or matches`. The seven
  multi-only bare names are `FX1`–`FX4`, `FXFILENAME`, `MULTINAME` and
  `PTUNOCM` — none collides with a primary-region name, so the "silent
  wrong-structure read/write path" has **no instance**. Reword the comment.

### Nits (unchecked, recorded as given)

`s3k/params.py`: unused `Iterable` (57), vacuous `param.key[0:2]` guard
(3331), `Parameter.key` docstring describes the retired flat-id scheme.
`s3k/scales.py`: `exact` from the unrounded float (1055), endpoint
early-return bypasses the provisional `!` (1113, dormant), dead `"u/s"` branch
(1142). `s3ked/app.py`: wrong return annotation on `LoadOptionsScreen` (113),
redundant local `import time` (1993, 2520), `b` possibly unbound (3684).
`s3ked/demo.py`: class-level mutable `boards: set = set()` (320), dead
`keygroup_count`/`_loaded`/`SourceScreen.changed`.

### Verified sound by the reviewer (recorded, not re-checked)

`s3k/messages.py` codec round-trips and fail-loud/fail-soft rules;
`s3k/params.py` encode/decode core; `s3k/bridge.py` reply-pairing arithmetic,
autodetect sweep teardown, stale-reply accept-set, write-gap labelling;
`s3k/scales.py` all 23 hardware anchors reproducing from table coefficients.

**Blocked on:** nothing — all code work, no hardware. The critical finding
first; it is the only one that can destroy a user's sample.
