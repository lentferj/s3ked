<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors -->

# `release/0.1.x` — feature freeze

The 0.1 line lives on the branch `release/0.1.x`, cut from `main` at
`4b67cc5` with 775 tests green. It takes **bug fixes only**. New work goes to
`main`.

This file is identical on both branches deliberately, so that the rule reads
the same wherever someone finds it and a fix touching it never conflicts.

Named `0.1.x` rather than `0.1.0-beta` because it is a maintenance *line*, not
a snapshot: 0.1.1 and 0.1.2 come from here.

## The test

> Does this change what the software does for someone who has already
> installed it, in a way they would call a **repair**?

If it makes a thing that was wrong right, it belongs here. If it makes the
project able to do something it could not do before, it does not — however
small, however tempting, however green the tests are.

## Yes

- **A crash, a hang, a wrong number.** Including anything that leaves the
  sampler needing a power cycle.
- **A guard that does not guard.** The bounds check (§82), the mode range
  check (§79), the write gate, the arm-then-fire on destructive operations.
  If one of these can be got past, fixing it is a repair.
- **A refusal that should not refuse.** The bounds check caches counts and
  cannot see front-panel changes; a stale count producing a wrong refusal is a
  bug on this branch.
- **Portability fixes.** A path, an encoding, a locale assumption. The sibling
  eosed shipped a config file that silently never persisted on Windows for the
  life of the project; nothing about that is a feature.
- **Packaging.** A missing dependency, a broken entry point, a wheel that does
  not install.

## No

- New parameters, new laws in `scales.py`, new keys, new panes, new CLI
  subcommands.
- New protocol operations, however well understood.
- Refactoring for its own sake. A refactor that is not fixing something is a
  feature with no user-visible benefit and all of the risk.
- Performance work, unless something is unusably slow.

## Documentation is where this gets hard

Most of this project *is* documentation of measurements, so the line has to be
drawn deliberately rather than by feel.

**A false claim in the docs is a bug, and fixing it belongs here.** This
project has treated it that way consistently and should keep doing so: §80's
attribution correction, §77's retraction of the 0.25 MB reasoning, the three
stale `TODO.md` entries that said "provisional" about settled fields, and
eosed's §23 — which was not false, but described a live mechanism in the past
tense and so read as complete. Every one of those would have sent a reader to
a wrong conclusion. That is a defect in the artefact, not a missing feature.

**A new finding is not a bug fix, even when it is small and true.** §83's
sample-header characterisation, the overlapping-keygroup measurement, a law
for a field nobody has swept — these go to `main`. They are additions.

The boundary between them:

| change | branch | why |
|---|---|---|
| a shipped law is wrong; re-measure and correct it | `0.1.x` | the number in the user's editor is wrong |
| a field has no law; measure one | `main` | the editor gains something |
| a note says "provisional" about a settled field | `0.1.x` | it will send someone to re-measure |
| a note records a new measurement | `main` | addition |
| a note describes a live mechanism as history | `0.1.x` | it reads as complete and is not |

## Hardware findings against a frozen branch

A measurement that **refutes something already shipped** is a bug fix: the
law, the range, or the safety claim in the user's hands is wrong. A
measurement of something not yet shipped is not, no matter how interesting.

This distinction has already bitten: `KGTUNO` was declared `0..50` when the
real range is ±12800 (§56), which made a semitone of detune impossible to
express. On a frozen branch that is unambiguously a fix — the editor was
refusing a value the machine accepts.

## `docs/RESOLUTION_NOTES.md` is identical on both branches

**The notes document the sampler, not this codebase.** That the machine
crashes on page value 11, that its mode register walks past the panel's own
hardware gate, that tuning writes snap to a one-cent grid — none of that
changes with a branch, and a reader on either one needs all of it.

So the file is kept byte-identical, and a finding lands on both branches even
when only one implements it. The freeze branch carrying a note about a
capability it does not ship is harmless; a reader missing a hazard because
they were on the wrong branch is not.

It is also the practical answer. The file grows by append, so every
cherry-pick that touches it conflicts — three did in one evening before this
rule existed, each resolved by hand the same way.

Sync it whenever it drifts:

```sh
git checkout release/0.1.x
git checkout main -- docs/RESOLUTION_NOTES.md
```

## Practical

```sh
git checkout release/0.1.x
# fix, with a test that fails without it
.venv/bin/python -m pytest -q          # must be green before and after
```

Fixes that also apply to `main` are cherry-picked forward. `main` is ahead;
this branch is never merged into it wholesale, because that would carry the
freeze's own history into the development line for no benefit.

**If a fix is tempting but does not pass the test at the top, it is a feature
and it goes to `main`.** The freeze exists precisely for the changes that feel
too small to bother arguing about.
