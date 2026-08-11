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

The practical cost is `BTSORT`: the spec says twice that it should be
triggered after writing `PRGNUM`, and never says with what index. So s3ked
cannot offer `trigger_btsort()`; `PRGNUM`'s description records that the user
must do it from the panel.

**To close this:** find a fuller copy of the Akai document, or capture what
MESA sends when it renumbers a program.

**Blocked on:** a better source, or a MIDI capture. RESOLUTION_NOTES §5.

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

## Filter calibration: FILFRQ mapped to hertz (CLOSED 2026-08-11)

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

**Done (§20):** `Hz = 6.998 * exp(0.07384 * FILFRQ)`, valid 50-90, max error
3.6 %, about one octave per 9.4 units. Below 50 the corner enters the
reference band and `ref_flat_db` correctly returns NaN; above 90 it saturates
toward Nyquist.

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

## The sweep harness fits an exponential to every sweep (OPEN)

**Status:** `summarise` assumes `a*exp(b*x)` for all eight sweeps. That is
right for filter frequency and wrong for tuning, which is linear -- the tool
reported `cents = 1.05*exp(0.067*KGTUNO)` for a relationship measured at
r2 0.9998 as a straight line (§21).

**To close this:** give `Sweep` a fit model (`"exp"` / `"linear"`) and let
`summarise` dispatch on it. Amp envelope times are probably exponential;
loudness and pan are probably linear in dB. Each should say which it is rather
than inheriting a default that happens to suit the filter.

**Blocked on:** nothing. RESOLUTION_NOTES §21.

---

## Sample header bytes 171-191 are undescribed (OPEN, hardware finding)

**Status:** our table stops at offset 141 of the sample header. On real
library samples, bytes 171-191 carry consistent non-zero structure; on
machine-authored samples the whole tail is zero. Not text — the values fall
outside the 41-character set. 21 bytes of a structure s3ked both reads and
writes have no description at all.

By contrast **program bytes 115-191 are zero across all eleven resident
programs**, so that tail really does look like padding.

**To close this:** ask mpc2emu, whose sample header is derived from real media
rather than from the document. Raised in the handoff.

**Blocked on:** a better source. RESOLUTION_NOTES §14.

---

## The extended layer does not bounds-check reads, and fails silently (OPEN, hardware finding)

**Status:** found 2026-08-10 by `probes/conformance.py`. An out-of-range
`RPHEADER`/`RKHEADER`/`RSHEADER` — bad program number, bad keygroup, offset
past the header, count up to 1024 — returns **the previous valid read's
buffer** instead of the documented REPLY/error. The S1000 whole-block
operations do bounds-check correctly.

Consequences: a wrong offset cannot be detected by reading; read-back
verification of a write can confirm something that never happened; callers
must bounds-check locally against PLIST/SLIST/GROUPS.

**To close this:** decide whether the bridge should refuse out-of-range
requests client-side rather than pass them to a device that will answer them.

**Blocked on:** nothing. RESOLUTION_NOTES §11.

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
