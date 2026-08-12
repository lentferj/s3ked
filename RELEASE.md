<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors -->

# What a first public beta needs

Ordered by what would embarrass the project most if it were skipped.

## 1. Run the TUI. As a person. Before anything else.

**Nobody has ever done this.** 698 tests pass, five screens are asserted, and
a screenshot script drives the key handling — and none of that is a human
noticing that a pane is unreadable at 80 columns, or that the status line
truncates, or that the thing is simply unpleasant to use.

This session's whole lesson applies to its own software: a green run is not
clearance.

```sh
.venv/bin/python -m pytest -q          # should be green first
s3ked --demo                           # no hardware, no risk
```

Then the same against the machine, gate closed:

```sh
s3kcli status
s3ked                                  # write gate starts locked
```

Things worth deliberately trying, because each is a claim the README makes:

- [ ] arrow around all four panes at 80×24 and at a full-screen terminal
- [ ] `w` arms the gate and the header changes
- [ ] `e` on a normal parameter; `e` on `PRIDENT` (must refuse)
- [ ] `z` undoes a write, and the pending-count in the subtitle is right
- [ ] `m` → arm → fire → confirm, on a program you do not mind losing
- [ ] `r` re-reads after you change something on the front panel
- [ ] resize the terminal while it is running
- [ ] `q` from every screen

Anything that feels wrong here outranks everything below it.

## 2. Decide what the beta claims about hardware

The calibration is real but partial: the filter, three envelopes, the LFOs,
tuning, loudness, pan and the modulation matrix are measured on an S3000XL.
**Most of ~300 parameters have never been written to a machine.**

`DISCLAIMER.md` now says this accurately. Worth re-reading it as the audience
rather than the author before tagging, because it is the page that decides
whether someone points s3ked at an irreplaceable sampler.

- [ ] read DISCLAIMER.md end to end, as a stranger
- [ ] confirm the version number: `0.1.0` is right for a first beta
- [ ] decide whether `Development Status :: 3 - Alpha` should become `4 - Beta`

## 3. Things a stranger will hit in the first ten minutes

- [x] **Install from a clean checkout**, in a fresh venv, following only the
      README. **Done 2026-08-12, and it found a real bug.** The isolated
      install ran 512 tests of 698 and reported success — two modules skipped
      at collection and took 186 tests with them, silently, because
      `s3k/measure.py` shipped in the wheel and imported numpy. measure.py has
      moved to `probes/`, numpy is a `bench` extra, and nothing that ships
      imports it. Re-verified: clean clone, empty venv, no system packages, no
      numpy, editor fully working.
- [ ] `s3kcli ports` with no MIDI device attached — does it fail kindly?
- [x] the built wheel carries both licence files and only the nine modules it
      should (checked 2026-08-12)
- [ ] autodetect against a machine on a non-zero exclusive channel
- [ ] a terminal without truecolour, and one with a light background
- [ ] `s3ked` with no `config.toml` present

## 4. Repository hygiene

- [ ] `git tag -l` — `backup-before-cleanup` and `backup-night-run` are from
      history work that is long since verified. Delete or keep, but decide.
- [ ] `HW_PANEL_CHECKS.md` is machine-local via `.git/info/exclude`; confirm
      that is still wanted, since it is useful to anyone with the hardware.
- [ ] `config.toml` is checked in — confirm it holds nothing machine-specific.
- [ ] CI is green on 3.11, 3.12 and 3.13.

## 5. Nice to have, not blocking

- [ ] a CHANGELOG, even if its first entry is just "first public beta"
- [ ] an asciinema cast of a real edit — the screenshots are static
- [ ] `docs/RESOLUTION_NOTES.md` is 68 sections and ~5000 lines. It is the
      most valuable thing in the repository and the least navigable; an index
      at the top would help.

## What is genuinely open, and should be said out loud in the release notes

Not blockers — but a beta that pretends these are solved will be found out.

- **`HW_PANEL_CHECKS.md`** — a list of fields only a person at the LCD can
  confirm. Nobody has been at the LCD.
- **`external` / `!external`** modulation sources: no document says what the
  stimulus is, so they are untested rather than inert.
- **The second filter** needs the optional IB304F board this machine lacks.
- **Sample data transfer** is not implemented, by choice; loading a sample
  needs the MIDI Sample Dump Standard.
- **Whole-header `PDATA`/`KDATA` writes** are deliberately not exposed.
- Four published measurements were **retracted** after better instruments
  contradicted them. That is the process working, and the retractions are
  visible in the notes — but it means a number in `s3k/scales.py` is a
  measurement, not a specification.
