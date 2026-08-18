<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors -->

# Implementing SAVE

Branch `save-page`. The panel's SAVE button sits immediately left of LOAD,
and `§84` names them: **mode 9 is SAVE, mode 10 is LOAD**.

## The standing constraint, first

**No actual writes to disk.** Everything up to the commit may be built,
driven and measured against the machine; the commit itself must not fire.
That is not a limitation to work around — it shapes the design, because it
forces the trigger to be a separately fenced call rather than the tail of a
convenience method.

Risky RAM writes are permitted. Disk writes are not.

## What the LOAD side already gives us

`§70`, `§93`, `§96` and `§107` mapped the LOAD page into the miscellaneous
byte bank. `s3k/bridge.py` uses:

```
byte[0]     device type (floppy / hard / flash)
byte[2]     partition, 0-based
byte[4]     volume, 0-based -- also the "selection held" flag
byte[6..9]  load type, mirrored; writing one FIRES the load (§71, §93)
byte[11]    SCSI drive ID
byte[12]    local SCSI ID
byte[49]    cursor value (reads the panel; writing does not move it)
byte[91]    mode -- the main-menu page
word[6]     directory entries
word[7]     item cursor
```

The load is reachable **only** because the LOAD page carries a register that
acts when written. `§113` established there is no save opcode anywhere in
the protocol: the whole disk surface is `RVOLLIST`/`VOLLIST` and
`RHDDIR`/`HDDIR`. So SAVE, if it is reachable at all, is reachable the same
way — a page register that acts.

`§105` is the warning: a 48-value sweep hunting the LOAD page's `CLR`
softkey found every value inert. A page being reachable does not make every
softkey on it reachable.

## The open questions, in the order they have to be answered

1. **Does the SAVE page have its own registers, or does it share the LOAD
   page's?** Both address the same disk, so device / partition / drive /
   volume may simply be shared state. Answerable by dumping the whole
   miscellaneous byte bank in mode 9 and again in mode 10 and diffing.
2. **Where does "what to save" live?** The LOAD page's equivalent is
   `byte[6..9]`, mirrored fourfold. A save-type register is the thing most
   likely to exist and most likely to be adjacent.
3. **Is there a destination NAME?** A save to a new volume needs one, and no
   register in the bank is known to carry a name. If there is none, remote
   save can only ever target an existing volume — which would be a finding
   worth having on its own.
4. **Is there a trigger, and can it be identified without firing it?** This
   is the hard one and the constraint bites here.

## PHASE A RESULT, and a hazard it exposes

Dumped all 128 bytes of the miscellaneous bank in mode 10 and again in mode
9. **Exactly three differ:**

```
byte[49]  LOAD  5   SAVE  0    page cursor position
byte[91]  LOAD 10   SAVE  9    the mode itself
byte[97]  LOAD 22   SAVE  1    UNKNOWN -- the page's own state
```

Everything else holds, including `byte[0]` device, `byte[2]` partition,
`byte[4]` volume, `byte[11]`/`byte[12]` the SCSI ids — **and `byte[6..9]`,
the load type**.

**Question 1 is answered: the SAVE page shares the disk selection with
LOAD.** There is no separate destination drive, partition or volume
register to find. Setting the destination is setting the same registers
s3ked already drives.

### THE HAZARD: the load trigger is shared

`byte[6]` is the register whose *writing* fires a load (§71, §93). It is
**not** page-specific — it reads the same value in both modes.

So writing `byte[6]` while the machine is in mode 9 may fire a **save**.
That is the one action this branch must never perform.

**Rule for this branch, and it is absolute:** never write `byte[6..9]` while
`byte[91]` is 9. Every code path that touches the load type must assert the
mode first. This is not a caution to remember — it goes in the bridge as a
guard, because §105's lesson is that the softkey you cannot reach is safer
than the one you fire by accident.

It also reframes question 4. The trigger may not be a *new* register to
find at all: it may be the load trigger, with the **mode** deciding what it
does. That is consistent with the design of everything else on this page,
and it means the candidate is already identified — which is exactly the
outcome the constraint allows, since naming it requires no firing.

### A second finding, incidental

**`byte[91]` will not accept 0.** Writing mode 9 or 10 succeeds; writing 0
(SINGLE) returns device error code 1. The machine declines to leave the
disk pages by that register, so a caller cannot put the panel back where it
found it. Recorded because a probe that assumes it can restore the mode will
fail in its `finally` — this one did, and lost two complete dumps before the
data was written out.

## PHASE C RESULT: there is no remote save, and no remote volume creation

Measured 2026-08-18, written up as `§122`. Four runs:

* `byte[6]` in mode 9 swept its **whole domain** (it is 3 bits — 8–15 all
  read back 7, and 0–7 is exactly `LOAD_TYPES`): inert against an empty
  slot, and inert against an existing volume.
* `byte[97]`, the only page-specific unexplained byte, ignores writes the
  way `byte[49]` the cursor does (§96).
* **Positive control**: the same raw poke in mode 10 loaded an item —
  559.2 kB consumed, sample resident. The route is live, so the negatives
  are the machine's answer and not the probe's.

**Can a new volume be created remotely? Not by any route yet tested — but
see §124, which names what was NOT tested.** Three reasons, and the third
is weaker than it looks:

1. The protocol's four disk opcodes are all readers. **This is weaker than
   it was first written.** It rules out a save *opcode*; it does not rule
   out a save *trigger*, because the load trigger is not an opcode either —
   it is a register write (§124). A document sentence also cited here was
   misread and has been withdrawn (§125).
2. The shared page trigger does nothing in mode 9.
3. **Not** because a name cannot be transmitted — that was my inference and
   it is wrong. The machine offers one slot past the last used volume,
   shows it as `INACTIVE`, and names it `VOLnnn` itself on save. So the
   empty-slot run asked for nothing the protocol lacks, and got nothing.
   Corrected from the operator's front-panel knowledge, not from a probe.

### The one route not yet excluded

Four opcode ranges below `0x45` are undefined: `0x17`–`0x1c`, `0x1e`–`0x26`,
`0x39`–`0x40`, `0x43`–`0x44`. **`0x39`–`0x40` sits immediately after the
disk block**, which is where a disk writer would live if one were left out
of the documents.

Probing it is not free. §85 and §90 crashed this machine with out-of-range
*values*; an undefined *opcode* could be anything, and "format volume" is
the kind of thing that lives next to a directory writer. This is named as
the remaining route, and deliberately **not** taken unilaterally.

## Method

**Reconnaissance is read-mostly and reversible.** Writing `byte[91]` to
change page is a RAM write the project has done many times; `§85` and `§90`
crashed the machine with *out-of-range* values, so only documented modes get
written, and the original is restored.

Phase A — **diff the bank across modes.** Dump every byte of the
miscellaneous bank in mode 10, switch to mode 9, dump again, restore. Bytes
that differ are the pages' own state; bytes that hold are shared. This needs
nobody at the panel and no disk access.

Phase B — **watch the bank while the page is operated from the panel.** The
method that mapped LOAD (`§70`): change one setting by hand, see which byte
moved. Needs a person, so it is scheduled around the operator rather than
assumed.

Phase C — **identify the trigger without firing it.** By analogy the
candidate is a small run of mirrored bytes near the type selector. Mirroring
is itself the tell: `byte[6..9]` move together, and a fourfold-mirrored byte
on the SAVE page is very likely its counterpart. **Establishing that a byte
is the trigger by writing to it is exactly what is forbidden here**, so the
deliverable of this phase is a *named candidate with its evidence*, not a
confirmation.

## What gets built regardless of what phase C finds

The value is not only in firing a save. Even with the trigger fenced off:

- `save_source()` — read the SAVE page's state, the analogue of
  `load_source()`.
- `select_save_*()` — set destination drive / partition / volume / type,
  all of which are ordinary register writes.
- a TUI **Save** screen mirroring the Load one, which can present the whole
  operation and stop at the commit with *"press SAVE on the machine"* — the
  same honest ending `README` already gives for saving.

That is worth having on its own: it turns a save from "set nine things on
the panel by hand" into "set them remotely, then press one button".

## Fencing the trigger

`trigger_save()` will exist, and will refuse unless passed an explicit
argument that no other code path supplies — the same shape as the bridge's
refusal to load an operating system without a flag. The refusal is the
feature. It stays refused for this branch's entire life.

## Ordering

1. Branch, plan, tell mpc2emu. *(this document)*
2. Phase A, and record it.
3. Bridge: `save_source()` and the setters, with tests against the demo.
4. TUI Save screen, tests.
5. Phase B when the operator is available.
6. Phase C written up as evidence, not as a confirmation.
