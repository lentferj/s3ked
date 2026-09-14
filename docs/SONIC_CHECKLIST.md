<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors -->

# The sonic verification stage

What a human must hear before a converted build is called good, written for
VinSamLib's matrix by the session that has been measuring these machines.

**Nothing here is automated and nothing here touches hardware from a test.**
This is the stage after the suite ends: a person, headphones, a sampler with
the build loaded.

Programs and samples are identified by **slot number** throughout. Never write
a commercial library's program, sample or bank name into a checklist, a log or
a screenshot caption.

## Why the stage exists

Two machines' readers agreed that 110 samples were at 44100 Hz. Both were
wrong, and what caught it was pitch on a real S3000XL. Agreement between two
instruments that share an assumption is not corroboration — it is the
assumption, twice.

## What a listener catches that a measurement did not

**Intervals.** A wrong sample rate or a wrong root note lands the sound a
*musical interval* away, and a musician names an interval instantly and
categorically. My own detectors disagreed by exactly an octave on the same
audio today, each with a self-consistent story, and neither could break the
tie. Nobody has ever confused an octave with a fifth by ear.

Intervals this project has actually produced, so they are worth recognising:

| heard | means |
|---|---|
| octave | sample rate doubled or halved |
| **8 semitones** (a minor sixth) | `SSRATE` 27777 against 44100 — §231 |
| fifth or fourth | a 3:2 or 4:3 rate ratio |
| ~4 semitones | a detector locking onto the 5th harmonic (5:4 = 386 ¢) |

**Two notes where there should be one.** A pad containing a fifth made
autocorrelation report the dyad's common period — a pitch belonging to neither
voice (§231). A listener hears a chord.

**A sound stopping dead.** Dropped loops on trimmed samples: the file is
present, every header field is correct, and the note ends at 2.3 s. No
metadata check sees it.

**An envelope in the wrong decade.** Two attack laws each ~1.8× wrong and
*cancelling* passed every file-level check on one conversion path (§223). Half
a second against four is not subtle by ear.

**Artefacts** — aliasing, clicks at loop points, a truncated release. These
have no field to be wrong in.

## What the measurement does better, and must be trusted over the ear

- **Small detunings.** 15–20 cents on a pad is at the edge of the method's own
  residual and well inside what a listener will call "fine". Anything under
  about 30 cents is the instrument's to judge, not the ear's.
- **A few dB of level**, and any level comparison across programs.
- **Anything stated as a number** — a corner frequency, an attack time, a byte.
  "Sounds about right" has no units.
- **Coverage.** Nobody auditions 200 programs attentively. The suite does.
- **Anything where the listener has been told what to expect.** Expectation is
  an instrument with a bias and no calibration.

> The honest summary: **the ear is categorical and the meter is continuous.**
> Use the ear for "which kind of wrong", the meter for "how wrong". Asking
> either to do the other's job is where both of today's retractions came from.

## Three traps in the measuring half

These belong here because a listener never has to think about them and a meter
always does. All three were paid for on 2026-09-14, across three projects.

### 1. A detector's window must span a fixed number of **carrier cycles**

Not a fixed number of milliseconds. A note ladder changes the carrier period at
every rung, so a fixed window measures a different quantity at each — and the
bias varies with note, which is indistinguishable from a note-effect.

eosed's measurement, on a **synthetic pure tone with nothing modulating**:

```
    f0 Hz    cycles per 5 ms window    envelope swing
     10.3            0.052                 20.57 dB
     41.2            0.206                  8.32
    164.8            0.824                  1.51
   1046.5            5.232                  0.26
```

Below about one cycle the carrier leaks into the envelope and its peaks cross a
−3 dB threshold early. At 10 Hz, **67 % of windows sit above the −3 dB line
with no attack in progress at all.** This cost the E4XT a 1.655× note-gradient
that did not exist, and cost this project a whole `ATTAK1` ladder measured 17–33
% short through a 5 ms window on a 33 Hz tone — **0.165 cycles**.

> **"Use a clean subject" is not the lesson.** A pure tone at 41 Hz has 8.3 dB
> of swing. What protected this project's note sweep was that its carrier was
> 1046 Hz, not that it was a tone. Record **cycles per window** for every row;
> it is the column that says whether the row is trustworthy, and it cannot be
> recovered afterwards.

**And the threshold is not "more than half a cycle".** Reproduced here on a
synthesised constant-amplitude sine, independently of eosed's run:

```
  cycles/window   0.05  0.10  0.17  0.25  0.33  0.41  0.50  0.66  0.75  1.00  2.00
  swing (dB)     14.80  9.03 10.32  0.04  3.91  1.83  0.00  1.79  0.04  0.00  0.00
```

The swing appears to collapse at 0.25, 0.50 and 0.75 alike — **and only two of
those are real.** The mean of `sin²` over a window `[t0, t0+T]` is

```
  1/2  -  cos(2*pi*f*(2*t0 + T)) * sin(2*pi*f*T) / (4*pi*f*T)
```

which vanishes **for every start `t0`** only when `sin(2*pi*f*T) = 0`, i.e. at
**half-integer** cycles per window. At quarter ratios that factor is at its
maximum and the term survives; it reads zero above only because tiled windows
start at `t0 = m*T`, where the cosine happens to vanish for every integer `m`.

**That cancellation is knife-edge and the half-integer one is not:**

```
  ratio    swing        ratio    swing
  0.2500    0.04 dB     0.5000    0.00 dB
  0.2510    6.50        0.5010    0.02
  0.2550    6.35        0.5050    0.09
  0.2600    6.12        0.5100    0.17
  0.3000    4.07        0.5500    0.74
```

**Four parts in a thousand off 0.25 and the full 6.5 dB is back.** Half an
octave either side of 0.50 and it is still under a dB. And with a start offset
that is not tiled from zero, 0.25 ranges 0.04 to 6.54 dB over start phase while
0.50 stays at 0.00 throughout.

> So a measurement can land on a ratio that looks immaculate and is an artefact
> of exact tiling, and **the cycle count alone does not tell you which
> happened.** No real carrier sits on an exact ratio, which is why the only
> safe reading is a wide one.

> The rule that survives is **many whole cycles, with margin** — not "enough"
> and not "more than half". Below about two, whether a reading is clean is an
> accident of the fractional part.

The 0.17 row is this project's own `ATTAK1` ladder: a 5 ms window on a 33 Hz
tone, **10.3 dB of swing against a −3 dB threshold**, which is what produced a
reading 17–33 % short (§234). Two independent syntheses agree on that number to
0.02 dB.

### 2. Every capture already contains a value known before you measured it

The pitch of the note you played. One FFT against equal temperament validates
sample rate, header, analysis scaling and tuning at once, and costs nothing.

**This is what caught a hardcoded 44100 against a JACK server at 48000** — two
bugs that had produced five sections of false findings, all of which compared
one of this project's measurements against another and so were blind to a
common-mode error in its own analysis (§240).

> **Two measurements agreeing is much weaker than one measurement matching a
> value that was never measured** — and only the second kind is free.

### 3. Build a comparison whose answer you already know into the run

Not "state the method's uncertainty first" — that requires knowing to, which is
exactly the discipline an interesting result erodes. Carry a known alongside
the unknown, and the method's error is measured as a by-product:

- something that **must** read the same as the subject, so any disagreement
  *is* the error bar;
- something with **nothing to measure** — a no-attack control, a program that
  cannot be one-sided — so any reading it gives is known to be artefact.

A method too coarse for the question then announces itself instead of waiting
to be asked about. eosed's enquiry needed 1.27 dB of separation and their
method's own spread was 2.61 dB worst case; that was knowable before a note was
recorded, and was discovered afterwards by accident.

**And run the screening before the ladder, not inside it.** A
material-qualifying test built into the run gets read as a measurement.

## The procedure

**1. The listener is not told what to expect.** Ask "play this and describe
what you hear", never "check this is a C2". A listener told the answer will
hear it.

**2. Every pass contains an unlabelled control.** At least one item known good
and one known broken, not identified until afterwards. This is not ceremony: I
reported nine programs as playing hard right, and it was a loose cable. What
would have caught it was the six programs in that same set that *cannot* be
one-sided — a control I already had and did not look at (§230).

**3. Write down what was heard before comparing it to what was expected.** Same
rule as filing a prediction and its falsifier before a run.

**4. A disagreement between ear and meter is a finding, not a tie to break.**
Record both. Today's two retractions were both cases where one instrument was
overruled too early.

## Per device

### Akai S3000XL — measured by this project

1. **Pitch against a reference, same MIDI note.** Listen for an *interval*, not
   a detune. An interval is a rate or root fault; a few cents is tuning and
   belongs to the meter.
2. **A chromatic run across every keygroup boundary.** Three faults that
   measure alike sound different: a **root/tune** fault is constant across the
   whole program, a **stretch** fault grows with distance from each root, and a
   **wrong surviving sample** jumps at a boundary and is right in between.
3. **Hold every sustaining program past 3 s.** Dropped loops stop dead.
4. **The slowest attack in the build**, against what the source asks for.
5. **Open and close the filter by hand** across the full range. Filter 2's
   highpass is *one pole* (§208) — it should sound gentler than the lowpass,
   and a build that renders it as two will sound abruptly thinner.
6. **Headphones, for one-sidedness — with a pan-centre single-zone control in
   the same pass.** If the control is also one-sided, the fault is the
   monitoring path, not the build.
7. **Soft and hard on the same key**, for velocity routing.
8. **Anything that clips, and anything more than ~15 dB below its neighbours.**

### E-mu E4XT, Kurzweil K2000, MPC

**I have not measured these and this checklist does not cover them.** Items
1–4 and 6–8 above are device-independent and can be carried over as they
stand. Item 5 is specific to the Akai's filter board and must be rewritten per
device by whoever has measured that filter — eosed for the E4XT, k2kremote for
the K2000.

Cross-machine figures that exist and can anchor a comparison: attack times
agree between the Akai and the E4XT to 3.7 % (§223), and the two machines'
tunings were compared at 130.37/175.05 Hz against 130.83/174.73 (§220). Those
are meter figures, not listening figures; they tell a listener what "the same"
should sound like.

## What this stage cannot do

It cannot certify a build. It is a **detector for categorical faults**, run by
a human, on a sample of the material. A clean listening pass means no fault of
the kinds listed above was audible in the items auditioned — which is worth
having precisely because those are the faults that survive every automated
check, and worth nothing as a statement about the other 190 programs.
