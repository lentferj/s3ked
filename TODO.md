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
