<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# Calibration: turning parameter numbers into physical quantities

**Status: tooling built and tested synthetically, never run against a
machine.** Nothing in this document is a measurement. It is the procedure for
making measurements, the order to make them in, and the list of traps that
have already cost real bench time on the sibling projects.

## What this is for

`s3kcli set FILFRQ 63` writes a 63. The specification says "basic filter
frequency, 0 to 99" and stops, so nothing in this project — or in any
converter that writes Akai programs — knows what 63 *is*. The same is true of
every rate, level, depth and tuning field on the machine: the tables carry the
range and not the meaning.

Two things want the answer:

- **s3ked itself**, so a parameter pane can show `FILFRQ 63 (≈4.2 kHz)`
  instead of `63`. `describe_value()` already exists for enumerations; a
  calibration is the same idea for continuous fields.
- **A converter writing Akai programs** — the sibling mpc2emu already builds
  S1000/S3000 programs and disk images, and currently has to guess how a
  cutoff in hertz or an attack in seconds becomes a 0–99 integer. That guess
  is the difference between a converted program that sounds like the original
  and one that does not.

The sibling projects have done this twice, for the E-MU E4XT and the Kurzweil
K2000R. The numbers do not transfer — they are different machines — but the
method and every trap do.

## What is different here, and it is a big difference

On the E4XT, changing one parameter meant rebuilding a bank, writing an SD
card, carrying it over and loading it. Bench sessions had to be planned around
the card swaps, and the published curves were fitted from **four to six**
stopwatch points because that was what the day allowed.

This family takes a parameter over SysEx, acknowledged, between one note and
the next. A fifty-point sweep is one unattended run of a few minutes.

So: **sweep the whole range.** Do not fit a curve to five points and hope. The
expensive step that shaped the sibling procedures does not exist here, and a
procedure copied across without noticing that would leave most of the value on
the table.

## Prerequisite: the machine must be able to make the sound

`probes/calibrate.py` sets parameters and plays notes. It cannot put a
**sample** in memory — sample transfer is deliberately not implemented (see
TODO) — and this family has no oscillator, so with an empty machine every
sweep records silence.

The measurements need two purpose-made sources:

| source | needed by | why |
|--------|-----------|-----|
| **white noise**, looped, several seconds | `filter` | flat and continuous, so the filter is the only shape in the spectrum. A saw works (every harmonic, no gaps); a square is worse (the corner search can land in an even-harmonic gap and report a corner that is not there); a sine is useless. |
| **a steady tone**, looped, one clear fundamental | everything else | envelope, level, pan, LFO and tuning all want a source whose own shape contributes nothing. |

Get them onto the machine as a **disk image**, not over MIDI. The sibling
mpc2emu writes S1000/S3000 floppy, hard-disk and CD-ROM images already, and
`tests/re_banks/gen_akai_cal_disc.py` there builds exactly this disc: both
samples, plus one program per sweep with its keygroup already pointed at the
right one. Build it, load it, and the machine is ready for every sweep in this
document.

If that is not available, any commercial disc with a sustained pad will do for
everything except `filter`, which genuinely needs broadband material.

## Order

Each step assumes the ones above it. The order is by dependency, not by
interest.

1. **`HW_CHECKLIST.md`, all of it.** If step 4 there has not confirmed the
   program header against the front panel, then a sweep is writing to an
   offset nobody has verified, and what it measures is *some* parameter. Not
   optional, and not something a good-looking curve can substitute for: a
   wrong offset that happens to land on another continuous parameter produces
   a beautiful curve for the wrong field.
2. **Level (`loudness`).** Cheapest, and it validates the rig end to end: if
   `PRLOUD` does not move the measured RMS, nothing downstream will work
   either. Also settles whether 0–99 is decibels, linear amplitude, or a
   table.
3. **Pan (`pan`).** Needs the stereo capture path, so run it early — a mono
   recording chain is much better discovered here than at the end.
4. **Filter (`filter`).** The headline curve, and the one with the most ways
   to go wrong. See the traps below.
5. **Amplitude envelope (`amp-attack`, `amp-release`, `amp-decay`).** Long
   runs; start them when you want to leave the room.
6. **Tuning (`tuning`).** Checks the "cent:semi" claim about the two-byte
   tuning fields — cheap, and it is a structural claim, not just a scale.
7. **LFO rate (`lfo-rate`).** Left last because it is blocked: see below.

## Traps

Every one of these has already produced a wrong number on a sibling project.

**Record from the hardware capture ports, never from a downstream client.**
If the same inputs also feed an EQ and room correction on the way to the
monitors, tapping downstream convolves every measurement with a correction
curve — tilting spectra, moving every −3 dB corner, changing resonance peaks.
A verification pass then reproduces the same error and looks like a
confirmation.

**Neutralise before you sweep.** Each sweep carries a `prepare` list, and it
is the most valuable part of the definition. Key-follow left on measures a
corner that moves with the test note. Velocity dependence left on measures the
velocity curve as well. An individual output left routed measures silence.
None of these fail loudly; they all produce a plausible curve.

**A reference band must lie in the passband.** The −3 dB search takes its 0 dB
reference from a band (default 100–500 Hz). If the corner falls *below* that
band, the reference is itself attenuated, the first bin above the band is
already 3 dB down, and every point returns the same value just above the band
edge. The first synthetic sweep run here reported **"500.6 Hz" for seventeen
consecutive filter settings** — a flat run that reads like a filter that stops
moving. `measure.corner_frequency` now checks the band is flat and returns NaN
instead, and the `filter` sweep references at 50–100 Hz. Points below that
still come back NaN, and NaN is the correct answer: it means "not measurable
with this source and this band", not "zero".

**Segment on the MIDI clock, not on silence.** A slow attack starts too
quietly for a level gate, so the gate is late by exactly the attack time being
measured.

**Sustain must be below peak to measure a decay.** With `SUSTN1` at 99 there
is no decay segment at all and every point returns NaN. The `amp-decay` sweep
sets it to 50 for this reason.

**Rates are rates.** `ATTAK1` 99 is the *fastest* attack, so the curve almost
certainly runs the opposite way from the parameter number. Fit against
`99 − value` if the raw fit is poor.

**Believe r2.** A sampler scale is usually exponential, and the fit reports
r2. Below about 0.99, look at the residuals before writing anything down: a
poor fit usually means a lookup table with a kink, which is itself a finding
worth recording — but only if it is not mistaken for noise.

**Run the negative control.** Before believing that `FILFRQ` moves the corner,
sweep it with the keygroup muted, or with a parameter that should do nothing,
and confirm the measurement goes flat. A rig that reports a curve for a
parameter that cannot possibly produce one is a rig measuring itself.

## Known blocker: the LFO destination

`lfo-rate` is written but marked blocked. `LFORAT`/`LFODEP` are "speed" and
"depth" of LFO1, but the assignable-modulation fields in the table
(`MODSLFOT`, `MODSLFOL`) are sources of modulation **of** the LFO, not the
LFO's own destination, and the specification's routing prose is not
conclusive. Before trusting a rate curve, confirm on the front panel which
destination LFO1 actually drives on this machine, and set the sweep's
`prepare` list accordingly. Measuring a rate through a route that is not
connected produces a clean, flat, entirely fictitious result.

## Recording results

Write the fitted curves into `docs/RESOLUTION_NOTES.md` §10, with the raw CSV
kept alongside. State the source sample, the test note, the reference band and
the r2 for every curve — a calibration whose conditions are not recorded
cannot be checked later, and the first thing anyone will want to know is
whether the number holds at a different pitch.

Then re-run one point from each curve at a different note and a different
velocity. If the number moves, the curve is not a calibration of the
parameter; it is a calibration of that note.
