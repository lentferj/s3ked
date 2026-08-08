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

In rough order of value, the open items are now: the live offset diff
(everything else is downstream of it), the throttle floor (§6), and the missing
miscellaneous index table (§5).

Next time there is hardware, the two cheapest items are the `RSTAT`→`STAT`
round trip and reading one program header — perhaps ten minutes, and between
them they either validate or demolish the foundation everything else rests on.

---

## Live hardware verification — nothing has ever been run (OPEN)

**Status:** blocking, in the sense that every other item's answer depends on
it. No part of this project has exchanged a byte with a real sampler.

To close this, in order, cheapest first:

1. `s3kcli status` — does the machine answer `RSTAT` on exclusive channel 0,
   and does `STAT` decode to a sane version and memory figures?
2. `s3kcli programs` / `s3kcli samples` — do the name lists come back
   readable? This alone validates the 41-entry non-ASCII character set, which
   is one of the more surprising parts of the protocol.
3. `s3kcli header program 0` — diff against the front panel. If `PRNAME` reads
   correctly the early offsets are right and confidence in the rest rises
   sharply. See RESOLUTION_NOTES §2.
4. Only then, with something unimportant loaded and after a disk save:
   one `s3kcli --allow-write set PRIORT 3 0` followed by a read-back.

**Blocked on:** hardware.

**Known undetectable, do not chase:** two machines left on the same exclusive
channel are indistinguishable on the wire, exactly as one machine heard on two
input ports is. That is a user-side misconfiguration, not something autodetect
can resolve.

---

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

## The SysEx send gap is a guess (OPEN)

**Status:** `SEND_GAP = 0.05` s, labelled unverified at the point of use.

k2kremote's reverse-engineered 120 ms is a Kurzweil K2000 finding and does not
transfer. `ThrottledOut` already separates read and write gaps so they can be
tuned independently once there is evidence.

**To close this:** walk the gap down under a read loop, then under a write
loop, watching for garbled replies or a hung panel. Procedure in
RESOLUTION_NOTES §6.

**Blocked on:** hardware.

---

## The miscellaneous data-index table is missing from the source (OPEN)

**Status:** the generic `RMISCDATA`/`MISCDATA` path is understood and the
message shape is implemented, but no table of index values exists in the
transcription.

The practical cost is `BTSORT`: the spec says twice that it should be
triggered after writing `PRGNUM`, and never says with what index. So s3ked
cannot offer `trigger_btsort()`; `PRGNUM`'s description records that the user
must do it from the panel.

**To close this:** find a fuller copy of the Akai document, or capture what
MESA sends when it renumbers a program.

**Blocked on:** a better source, or a MIDI capture. RESOLUTION_NOTES §5.

---

## Autodetect: no broadcast address exists in this protocol (BUILT, never run live)

**Status:** designed and unit-tested against fakes, including the MIDI-Thru
rejection and the two-machine refusal. Never seen hardware.

Because there is no broadcast address, discovery sweeps ports at one exclusive
channel (0 by default). A machine set to another channel will not be found
without widening `channels=`, and the error message says so.

**To close this:** confirm a real machine answers on channel 0 out of the box;
time one full sweep on a host with several interfaces.

**Blocked on:** hardware. RESOLUTION_NOTES §7.

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

## Parameter scales are unknown -- what a value MEANS (OPEN, tooling built)

**Status: measurement tooling built and tested synthetically; no number has
been measured.** Blocked on hardware, and behind the offset confirmation:
every sweep writes offsets that `HW_CHECKLIST.md` step 4 has not yet
validated.

The tables carry each parameter's *range* and none of its *meaning*.
`FILFRQ` is "basic filter frequency, 0 to 99" -- not one word about which
hertz. Same for every rate, level, depth and tuning field. Two consumers want
the answer: this editor, so a pane can show `FILFRQ 63 (~4.2 kHz)` the way
`describe_value()` already renders enumerations; and any converter writing
Akai programs -- the sibling mpc2emu builds S1000/S3000 programs and disk
images today and has to guess how a cutoff in hertz becomes a 0-99 integer.

What exists now:

- `s3k/measure.py` -- the analysis half. Pure functions, no I/O, so each is
  tested against a synthesised signal whose answer is known in advance
  (`tests/test_measure.py`): a 200 ms attack must measure 200 ms, a filter at
  a known corner must report that corner.
- `probes/calibrate.py` -- the driving half. Eight sweeps, each carrying the
  list of parameters it must neutralise first, which is where the real
  knowledge sits. `--dry-run` drives a synthetic machine end to end and
  recovers the curve that machine was built with.
- `docs/re_procedures/calibration.md` -- order, traps, and what each sweep
  settles. `HW_CALIBRATION.md` (machine-local) is the bench checklist.

**Blocked on more than hardware, in one case:** `lfo-rate` needs the panel
consulted first. `MODSLFOT`/`MODSLFOL` are sources of modulation *of* LFO1,
not its destination, and measuring a rate through an unconnected route yields
a clean and entirely fictitious curve.

**Also blocked on source material.** These sweeps set parameters and play
notes; they cannot put a sample in memory, and this family has no oscillator.
Filter calibration needs broadband noise resident on the machine, which is a
disk-image job -- see the procedure doc.

See RESOLUTION_NOTES §10.

## Housekeeping

- `HW_CHECKLIST.md` and `HW_CALIBRATION.md` are machine-local, excluded via
  `.git/info/exclude` rather than `.gitignore`, matching the sibling eosed
  project.
- The keygroup pane currently shows a placeholder range per keygroup rather
  than reading each keygroup's `LONOTE`/`HINOTE`; it costs one request per
  keygroup and should wait until the offsets are known good.
- No screenshots in the README yet — worth adding once the TUI has been seen
  against something real.
