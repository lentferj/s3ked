<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# Measuring the S1000 filter law with a borrowed machine

**Status: designed, not built.** Nothing here is a measurement. It is the
procedure for getting one from an S1000 this project does not own, by handing a
disc to a volunteer and analysing what they record.

## Why it is worth the trouble

The S1000 filter law has, as far as we can find, **never been measured**.
What exists is a specification, an emulator and a derivation:

* Akai's published **"18 dB/octave, non-resonant"** is a slope, not a law. It
  says nothing about which of the 0–99 settings produces which frequency.
* **MAME's `sound/l6009.cpp`** emulates the sound LSI by applying a first-order
  IIR three times, and hedges it in the comment — *"most likely just three -6dB
  first order filters in series"* — with a TODO conceding `ENV_SHIFT` is a
  guess. It carries **no coefficient table**: the register value is used
  directly.
* **ConvertWithMoss's `FILTER_CUTOFF`** (2026-08-20) is the first published
  parameter-to-hertz table. Its coefficients come from the OS v4.40
  disassembly, but converting one into hertz **assumes three one-pole stages at
  44.1 kHz** — MAME's hedge. If the pole count is wrong, every entry is wrong by
  the corresponding factor, which is the exact shape of §139's constant 1.76x.

§139 measured this S3000XL at **12 dB/octave**, against the S1000's specified
18. So the generation question is live, and nothing in the literature settles
it.

## The design, and why each part is there

### It is a paired test, which is the whole point

The corpus could not answer this (§140): the S1000 and S3000 discs held
different material — orchestral collections against synth dumps — and that
confound moved `FILFRQ` far more than the 0.82 octaves at issue.

**One disc, one sample, identical programs, played on both machines** removes it
entirely. Whatever differs is the machine. It also gives a free pre-flight: build
it, load it here first, and if it misbehaves on the S3000XL it never goes out —
and that run produces the S3000XL half of the pair at no extra cost.

### Build it by patching real files, not by generating a format

The sibling converter's writer emits **192-byte S3000 blocks only**; there is no
S1000 path and nothing has ever read one back. Do not write one for this.

Instead **start from a genuine factory `.P1`/`.S1` pair** — a file an S1000 has
demonstrably loaded — and change only fields whose offsets are verified. A `.P1`
is 300 bytes, exactly two 150-byte blocks (common + one keygroup); `FILFRQ` is
at keygroup+`0x07`. N programs is N copies of a 300-byte file with one byte
different, all sharing one sample.

### The source must be a sawtooth, not a sine

A pure sine leaves nothing above the fundamental, which is what made the
spectrum-scaling check unavailable on the RATEREAD disc (§143). A sawtooth
rooted near 40 Hz gives a harmonic comb every 40 Hz, dense enough to fit **both
the corner and the slope** — and the slope is what actually decides the
question, 18 dB/octave against the 12 measured here.

### Nothing may require editing

One program per setting, `FILFRQ` stepped monotonically (say 30..95 by 5),
`FILQ` 0, instant attack, full sustain so the note is a steady state with no
envelope in it. **The operator changes program and plays one note.** They must
never touch the filter page, because a setting we cannot verify is a specimen we
cannot identify — which is the failure that cost this project most of two days.

### Controls, and there are three uses for them

A wide-open program (`FILFRQ` 99) placed **before and after every measured
note**, not merely at the ends:

1. **cancellation** — dividing each setting by the open reference removes the
   volunteer's microphone, converters, room and any mastering in one step, the
   same division that removed the speaker and interface in §139;
2. **drift detection** — references spread through the take expose a gain change
   mid-recording, the likeliest thing to go wrong when a stranger runs this
   unsupervised;
3. **self-normalisation** — bracketing makes each measurement independent of the
   take being continuous, so an unknown pause between notes costs nothing.

Bracketing doubles the note count. That trades against the rule below, and the
trade is the one real design decision here.

### Ordering is a self-check

Stepped monotonically, the corner must rise through the take. If it does not,
the programs were played out of order and **we see it without trusting anyone's
notes**. Given how many of this project's errors were mislabelled specimens, a
design where misordering is self-detecting is worth the extra programs.

## Media

Two routes, and **neither is universal**:

* **SCSI hard-disk image** — needs a BlueSCSI / ZuluSCSI / SCSI2SD. Common
  among owners who still use these machines, not guaranteed.
* **Floppy** — the obvious "everyone has the drive" answer, and it does not hold.
  The AKAI format is `80 x 2 x 10 x 1024`, **not DOS**, so a volunteer cannot
  write it on a PC drive: it needs a Gotek or flux tooling (Greaseweazle,
  KryoFlux). That swaps "owns a SCSI emulator" for "owns a Gotek" rather than
  widening the audience. The sibling's floppy builder also carries an **S3000**
  docstring and has never been hardware-verified for AKAI at all.

Offer both images, since the two populations only partly overlap, but do not
spend effort hardening the floppy path on a reach argument that is false.

## What we ask the volunteer

Short enough for one card. Anything longer will not survive contact with a
favour:

```
load the volume; direct out, no effects, no EQ, no compression;
play the SAME key, one note, ~3 seconds, for each program in number order;
leave a clear gap between notes; one continuous take; do not touch any page.
```

## Analysis

As §139: take each setting's harmonic magnitudes, divide by the bracketing open
reference, find the −3 dB crossing by interpolating in log frequency, and fit
the asymptotic slope separately in the region above 2.2x the corner and above
the noise floor. Gate every harmonic on an **absolute** floor before use — a
relative threshold scales with the noise and returns a confident reading of
silence (§138).

Do not fit the corner and the pole count together. That fit is ill-conditioned
at low settings and returned a plausible, wrong N = 3.12 here (§139); the
asymptotic slope needs far less of the curve.

## What it settles

The S1000's corner law and its slope, measured — and, through the paired
S3000XL run on the same disc, whether `FILFRQ` means the same thing on both
generations. Both are open, and neither would duplicate anything that exists.
