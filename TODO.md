<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# TODO

*What* is open. `docs/RESOLUTION_NOTES.md` tracks *how* to resolve each item.

## Status, 2026-08-08 (first session)

The project exists and is complete as a piece of software: protocol codec,
parameter tables, transport, CLI and TUI, **204 tests, all passing, all
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

In rough order of value, the open items are: the live offset diff (everything
else is downstream of it), the two known judgement calls (§3, §4), the
throttle floor (§6), and the missing miscellaneous index table (§5).

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

## Keygroup offsets 161/162 are documented twice, incompatibly (OPEN)

**Status:** resolved by decision, unverified. The later definition
(`KFXCHAN`/`KFXSLEV`, with a leading `0 = PRG`) is used; the earlier
(`PFXCHAN`/`PFXSLEV`) is recorded in the entry's notes.

If the machine follows the earlier enumeration, every value read from these
two bytes is off by one.

**To close this:** set a keygroup's effects bus from the front panel to a
known value, read offset 161, see which enumeration matches. One reading.

**Blocked on:** hardware. Full write-up: RESOLUTION_NOTES §3.

---

## Note-name octave numbering is self-contradictory in the source (OPEN)

**Status:** anchored to the specification's low end (note 21 → `A1`).

"21 to 127 represents A1 to G8" cannot be true at both ends. This is a display
concern only — the byte written is the same under either reading.

**To close this:** set a keygroup's low note from the panel and read what the
panel calls it.

**Blocked on:** hardware. Full write-up: RESOLUTION_NOTES §4.

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

## Housekeeping

- `HW_CHECKLIST.md` is machine-local, excluded via `.git/info/exclude` rather
  than `.gitignore`, matching the sibling eosed project.
- The keygroup pane currently shows a placeholder range per keygroup rather
  than reading each keygroup's `LONOTE`/`HINOTE`; it costs one request per
  keygroup and should wait until the offsets are known good.
- No screenshots in the README yet — worth adding once the TUI has been seen
  against something real.
