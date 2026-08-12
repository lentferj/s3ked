<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# Disclaimer

## AI Assistance & Human Authorship

In the interest of transparency: s3ked was created by its **human author,
Jan Lentfer (<jan.lentfer@web.de>)**, working together with Anthropic's
**Claude**, an AI coding assistant, and closely follows the pattern
established by the author's sibling **eosed**, **k2kremote** and
**mpc2emu** projects.

**The ideas and the direction are human.** The decision to target the
documented editor/librarian protocol — the only one this sampler family
has — the project structure, and every safety behaviour (destructive
operations are never key-bound, only reachable through a modal
arm-then-fire screen; synthetic-first testing via `--demo`; the
one-session-at-a-time hardware rule) came from the human author and from
the conventions he established in the sibling projects.

**Claude assisted with the execution:** the protocol survey that
established what this family can and cannot do; transcribing the
operation codes and header parameter tables from Akai's own SysEx
specifications into `s3k/messages.py` / `s3k/params.py`; writing the
codec, transport, CLI and Textual TUI; and drafting the documentation
and test suite.

## Some of this is verified against hardware. Much of it is not.

**Read which is which before trusting a byte offset.**

This page said "nothing here has been verified against hardware" until
2026-08-12, and that was true when it was written. It is no longer. The
protocol, the write path and a set of physical-unit laws have since been
exercised at length against a real S3000XL, and `docs/RESOLUTION_NOTES.md`
records every measurement.

What that does **not** mean:

- **Verified fields are a minority of the table.** Calibration touched the
  filter, the three envelopes, the LFOs, tuning, loudness, pan and the
  assignable modulation matrix. Most of the ~300 parameters have still never
  been written to a machine, and their offsets rest on the transcription
  alone.
- **A green test run still proves only self-consistency.** The suite is
  synthetic by design — fake bridges and a `--demo` mode that never
  constructs a bridge — so it tells you the code agrees with the table, not
  that the table is right.
- **Being measured is not being measured correctly.** Several findings here
  were published and then retracted after better instruments contradicted
  them: a filter-frequency law 20-30 % high because it was read through a
  spectral centroid, five fields called inert that were one dead destination,
  a tuning range transcribed 256x too narrow, and a threefold
  "depth-dependence" that was a saturating measurement. Each retraction sits
  next to what replaced it, deliberately, so the failure modes stay visible.

The sibling eosed project remains the standing warning. Its parameter table
was transcribed from a manufacturer specification too, and live use still
found a twelve-entry range error that only surfaced by writing negative
values and reading them back, and at least three "number of X" fields that
read as plain counts in the spec and are not reliable on real hardware. Both
classes have now been found here as well — see the transcription caveat
below.

## The parameter tables come from a hand transcription

The Program / Keygroup / Sample header offsets in `s3k/params.py` are
transcribed from *S2800/S3000/S3200 MIDI System Exclusive Extensions*.
The text used is **itself a hand-typed transcription** of a printed Akai
document, published by a third party who marked the points he believed
were wrong with "FN" and explicitly disclaimed any warranty of
correctness.

So there are two transcription steps between the hardware and this code,
and the first one is known to contain errors its own author could not
resolve. Those "FN" marks turn out to fall in the message-format sections
rather than in the parameter tables themselves — `docs/RESOLUTION_NOTES.md`
§2 records exactly where, and what is done about each — but that is a
narrowing of the risk, not a removal of it: an offset can be wrong without
anyone having flagged it.

Two specific decisions are already known to be judgement calls rather than
transcription: the keygroup header documents offsets 161/162 twice with
incompatible enumerations (§3), and the note-name octave numbering is
self-contradictory in the source (§4).

**Do not treat a byte offset in this project as fact until it has been
diffed against a real header dump.**

## No Warranty

This program is distributed in the hope that it will be useful, but
**WITHOUT ANY WARRANTY**; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License (`COPYING`) for details.

## Hardware Risk

This software sends MIDI System Exclusive messages to vintage hardware.
You use it entirely at your own risk. Specific hazards, in rough order of
how easy they are to trigger by accident:

- **`DELP` / `DELK` / `DELS` delete without any device-side
  confirmation.** There is no "are you sure" on the wire and no undo.
  s3ked never binds them to a single keypress — they are reachable only
  through an explicit arm-then-fire modal — but that is a guard against
  slips, not a safety net.
- **A whole-header write can destroy a program you did not name.** The
  specification states that sending program data whose *name* matches an
  existing program **deletes that program first**. Treat any whole-header
  write as destructive.
- **A wrong byte offset writes to the wrong parameter.** Given the
  transcription caveat above, this is a realistic failure mode, not a
  theoretical one. Anything in RAM that you have not saved to disk is at
  risk.
- **Item-index bit 12 ("postpone recalculation") leaves the machine in a
  documented "undetermined state"** until a later write clears it.
- **Flooding a vintage sampler with SysEx can hang it.** The send gap in
  this project is a conservative guess, *not* a reverse-engineered value —
  the sibling k2kremote's RE'd 120 ms figure is a Kurzweil K2000 finding
  and does not transfer here.

**Back up anything you care about to disk before pointing this at real
hardware**, and keep the write gate closed (`--allow-write` is off by
default) until you have reason to trust the offsets.

## Trademarks

Akai, S1000, S1100, S2000, S2800, S3000, S3000XL, S3200, S3200XL are
trademarks of their respective owners. This project is not affiliated
with, endorsed by, or sponsored by Akai. All other trademarks are the
property of their respective owners.
