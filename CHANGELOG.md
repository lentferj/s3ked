<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors -->

# Changelog

Notable changes to s3ked. Dates are ISO. Versions follow
[semantic versioning](https://semver.org/), with the caveat that 0.x means the
interfaces may still move.

The reasoning behind almost every entry here lives in
`docs/RESOLUTION_NOTES.md`, which is numbered by section (§n) and is the
long-form record; this file is the short one.

## [0.1.0] — unreleased, first public beta

Version `0.1.0`, `Development Status :: 4 - Beta`, on a 0.x line whose
interfaces may still move. Development happens on `main`; there is no
maintenance branch, and there was briefly one — see the note at the end of
this entry.

First release. A terminal editor for the Akai S1000/S3000 family over MIDI
SysEx, developed against an S3000XL.

**Not a librarian.** It edits the machine in place and navigates the
sampler's own disk; it does not keep a patch library on your computer. There
is no export, no import, no file format, and sample audio is not transferred
at all — see *Known limitations*. The protocol Akai provides is of the
editor/librarian class, which is where the word appears elsewhere in these
docs; this tool implements the editor half.

### What it does

- **A TUI (`s3ked`) and a CLI (`s3kcli`).** Programs, keygroups and samples in
  one view; the parameter table decoded beside them.
- **269 parameters** across the program, keygroup, sample, multi and multi-part
  structures, read and written by name.
- **35 parameters carry measured physical units** — `FILFRQ 80 (~2.57 kHz)`
  rather than `80`, and `set FILFRQ 500Hz` accepted. Every law was measured on
  hardware, and none is provisional (§20–§26, §43–§66).
- **Disk control.** Volume and directory listing, and remote selection of SCSI
  drive, device and partition. Loading a volume is triggerable, with a fit
  check against free memory beforehand (§70–§74). The load asks how: onto what
  is resident or onto an emptied machine, and whether to renumber afterwards.
  *What* to load is a real choice: all eight of the sampler's own load types
  — `ENTIRE VOLUME`, `ALL PROGS+SAMPLES`, `programs only`, `all samples` and
  the cursor and multi variants — are performable over MIDI, and the screen
  opens on whatever the panel is set to (§93, §94). `Operating System` is
  guarded behind an explicit flag and is not offered in the TUI.
- **Renumbering**, the remote equivalent of the panel's `RNUM` → `SEQU`. It
  matters because loads append and `PRGNUM` is stored in the program and
  reloaded verbatim: load four volumes and four programs claim number 1, and
  they stack — one program change fires all four (§91).
- **An integrity audit** (`s3kcli audit`, `i` in the TUI): which zones name a
  sample the machine does not hold, who uses a given sample, and which samples
  nothing references. It exists because a load that overruns memory reports
  "insufficient waveform memory" once and then behaves normally, leaving
  programs that play silence with nothing on the machine to say so (§73, §80).
- **All eleven main-menu pages** are reachable over MIDI (§84).
- **A multi mode implementation.**
- **Effects and reverb read/write.** No document describes the structure, so
  it was measured: a 12-character name at offset 3, entries 128 bytes apart,
  two lists of 51 presets. It works on a machine with **no EB16 fitted**,
  which is the point — an editor can author effects for a program destined
  for a machine that has the board (§86, §88).

### Safety

This talks to a sampler that has no undo.

- **The write gate starts closed.** Nothing is written until you arm it.
- **Destructive operations are arm-then-fire**, never a single keypress.
  `DELP`/`DELK`/`DELS` have no device-side confirmation and no undo.
- **Expansion-board fields are fenced.** Fifteen keygroup fields need the
  IB304F and the effects pages need the EB16; without them the panel refuses
  those pages outright, and this project crashed an S3000XL twice in one
  session while that area was exercised. They are refused unless you declare
  the board fitted, because the machine cannot be asked which boards it has
  (§85, §86, §90).
- **Out-of-range header reads are refused locally.** The device answers them
  with the previous read's buffer instead of an error, so a wrong index comes
  back as plausible data belonging to something else (§11, §82).
- **Replies are matched to requests.** A client killed mid-exchange leaves the
  sampler's answer in flight, and the next process to open the port is handed
  it. Frames that cannot answer what was just sent are skipped and counted
  rather than decoded as the wrong message (§95).
- **SIGTERM closes the port.** It used to end the process where it stood,
  leaving the machine composing an answer nobody would read — which wedged an
  S3000XL until it was power cycled (§95).
- **The page register is range-checked.** Writing a value past the end crashes
  the firmware outright — no reply, frozen front panel, power cycle — and
  nothing on the device refuses it (§79, §85).

### Known limitations

- **Sample data transfer is not implemented.** Sample *headers* are read and
  written; the audio is not. `ASPACK` hands off to the MIDI Sample Dump
  Standard, which this does not speak.
- **Whole-header `PDATA`/`KDATA` writes are deliberately not exposed.** The
  specification says writing a program whose name matches an existing one
  deletes that program first.
- **Choosing a volume is a front-panel job.** There is no volume register; the
  drive, device and partition are settable and the volume is not (§72).
- **A load cannot be set up and fired separately.** The type register has no
  `GO`: writing it *is* starting the load. So any code touching bytes 6–9 has
  begun one, and there is no way to stage a load in advance (§94).
- **Every byte offset is a transcription** from Akai's own documents and is
  unverified except where a panel check confirmed it. A write that changes the
  sound proves *some* parameter moved (§2).
- **The SysEx send gap is a conservative guess**, not a reverse-engineered
  figure. k2kremote's 120 ms is a K2000 finding and does not transfer.
- **Tuning writes snap to a one-cent grid.** The field holds 1/256 of a
  semitone but only multiples of 2.56 are kept, silently (§89).

### A note on the branch that briefly existed

A `release/0.1.x` branch was cut and then abandoned within a day. Everything
on it is here; nothing was lost.

It was cut on the assumption that 0.1 was close to releasable. It was not.
The evening that followed found a bug in the bounds cache, a config file that
could silently empty itself, `s3kcli ports` crashing on any host without a
MIDI backend, a key legend that hid half its bindings at the smallest
supported size, a parameter table claiming envelope 3 needed a board it does
not, and a pane that had never shown anything but a placeholder. A freeze is
for a line that has stopped moving, and this one had not started.

Two things it did earn, kept here because they outlived it: a policy that a
false claim in the documentation is a bug rather than a nice-to-have, and the
discipline of asking whether a change *repairs* something or *adds* something
before writing it. Both are worth keeping without the branch.

### Not included

`s3kcli` and `s3ked` never open a MIDI port unless asked, and `--demo` never
constructs a bridge at all. No telemetry, no network access, no automatic
updates.
