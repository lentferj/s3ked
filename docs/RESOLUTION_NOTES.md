<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
-->

# Resolution notes

*How* to resolve what `TODO.md` lists as open: protocol findings, probe
procedures, and ready-to-apply code. Numbered sections, referenced from code
comments as "see RESOLUTION_NOTES §N".

---

## §1 — Protocol survey: what this family has, and what it does not (resolved, 2026-08-08)

This section exists so the question that started the project is never
re-derived. It was settled from primary sources before a line of code was
written.

### The question

Does the Akai S3000(XL) offer anything like the protocol the sibling
**k2kremote** project uses for the Kurzweil K2000 — read the device's LCD,
inject front-panel button presses, mirror the screen in a terminal?

### The answer: no. There is no panel protocol on this family.

Checked against the Akai scan held by the Internet Archive as item
`S3000XLOM`, file `S2000S3000xlS3200xl-SysexDocumentation_djvu.txt`.
Grepping the whole document for `screen|front panel|panel|display|keypress|
button|LCD|wheel` yields **one** hit, and it is only naming the "Mode
Buttons". There is no display read, no button injection, and no panel echo
anywhere in the documented command set.

The S1000 document is the mild tease here: it describes the machine's own UI
as "a 240x64 graphic screen, two S900-like rotary encoders and a similar set
of function and numeric keys… 8 'soft' function keys under the screen" —
*the same panel geometry as the K2000*. Those pixels are simply not reachable
over MIDI.

### What the family does have: one editor/librarian protocol

```
F0 | 47 | cc | op | 48 | item-index(14b) | selector | offset(14b) | count(14b) | <nibbled data> | F7
```

`47` = Akai, `cc` = MIDI exclusive channel, `48` = model identity (shared by
the whole S1000/S3000 line). Three stacked opcode layers:

| Layer | Opcodes | Content |
|---|---|---|
| S1000 base | `00`–`16`, `1D` | RSTAT/STAT, RPLIST/PLIST, RSLIST/SLIST, RPDATA/PDATA, RKDATA/KDATA, RSDATA/SDATA, RSPACK/ASPACK/CASPACK, RDDATA/DDATA, RMDATA/MDATA, DELP/DELK/DELS, SETEX, REPLY |
| S2800/S3000/S3200 | `27`–`38` | byte-offset request/write for Program / Keygroup / Sample headers, FX/Reverb, Cue-List, Take List, Misc, Volume List, HD directory |
| S2000/S3000XL/S3200XL | `41`, `42` | REQUEST MULTI DATA / MULTI DATA |

The S3000 document states the design intent plainly: "anything that can be
done from the S3000 front panel should be able to be done by means of such
MIDI system exclusive operations". That is functional equivalence — *not*
panel mirroring, and the distinction is the whole reason this project is an
editor and not a remote.

### Two places this protocol beats the sibling eosed project's

1. **The device repaints its own LCD.** Item-index bit 13 is "postpone screen
   update" and bit 12 "postpone recalculation" — opt-*out* flags. eosed had to
   reverse-engineer a forged Program Change to force a redraw because EOS has
   no such command at all. **Do not port that workaround.** Bit 12 carries its
   own hazard, in the spec's words: "the machine may be in an undetermined
   state until the same parameter is sent with this bit cleared."
2. **Writes are acknowledged.** `REPLY` (`16h`) returns `0` = ok, `1` = error.
   eosed must read a value back to know whether a write landed.

### Where Akai *does* have the k2kremote model: the next generation

Z4/Z8/S5000/S6000/MPC4000. Prior art is `aksy`
(<https://github.com/watzo/aksy>). Front-panel injection is genuinely SysEx,
section `0x2C` (from `data/z48/frontpaneltools`):

```
2C 01  keypress_hold(BYTE)          2C 03  move_datawheel(SBYTE)
2C 02  keypress_release(BYTE)       2C 04  set_qlink_control(BYTE, WORD)
2C 10/11  ascii_keypress_hold/release(WORD, WORD)
2C 20/21  mouseclick_at_screen / mousedoubleclick_at_screen(WORD, WORD)
```

But **the screen read is not SysEx**, and that is what rules it out here.
`get_panel_state()` is a raw USB bulk command `CMD_LCD_GET` on endpoint
`EP_IN 0x82` (`src/aksyx/aksyxusb.c:412`), returning
`PANEL_PIXEL_DATA_LENGTH = 1860` bytes of pixels (248×60 ÷ 8) plus
`PANEL_CONTROL_DATA_LENGTH = 18` bytes of control/LED state. aksy ships a
working GTK mirror client at `src/aksui/UI/lcdscreen.py` — worth reading if a
Z-series ever turns up. The S5000/S6000 SysEx spec adds front-panel
*lock/unlock* only, not a screen read.

So a k2kremote-style mirror needs the USB port this family does not have.

### Sources

- S1000: <https://lakai.sourceforge.net/docs/s1000_sysex.html>
- S2800/S3000/S3200: <https://lakai.sourceforge.net/docs/s2800_sysex.html>
- S2000/S3000XL/S3200XL: <https://lakai.sourceforge.net/docs/s2000_sysex.html>,
  and the scan in archive.org item `S3000XLOM`
- Z-series prior art: <https://github.com/watzo/aksy>

---

## §2 — Provenance of the parameter tables, and what that costs us (open)

`s3k/params.py` is transcribed from the S2800/S3000/S3200 document's 251
`Parameter:` entries (Offset / Field size / Range / Description) across three
192-byte regions: Program (84 entries), Keygroup (130 after §3), Sample (35).

**There are two transcription steps between the hardware and this code, and
the first one is known to contain errors.** The lakai text is itself a
hand-typed transcription of a printed Akai document by a third party signing
as "Frank", who marked passages he believed were wrong with `FN` and
disclaimed any warranty of correctness.

Where his `FN` marks actually fall matters, and is narrower than feared:

- **Not in the parameter tables.** Grepping all 251 entries for `FN` or `???`
  returns **zero** hits. The offsets and ranges are unmarked.
- **In the message-format sections**, twice, and both touch code we ship:
  - *Request for Sample Header bytes* (`0x2B`) — the source lists a nibbled
    data portion in a **request**, marked "??? typo in specs..? - FN". He is
    almost certainly right: every other request in the family is a bare
    12-byte header plus EOX, and a request carrying data makes no sense.
    `s3k.messages.HeaderRequest` implements it as a bare header.
  - *Receive Cue-List bytes* (`0x30`) — the data lines are marked "this is not
    in the orig docs - typo? - FN". Not currently exercised; s3ked does not
    touch cue lists.

**To close this:** read a known program header off a real machine and diff it
against the table. `s3kcli --demo header program 0` shows the shape the code
expects; the same command without `--demo` gets the real thing. Specifically
check that `PRNAME` lands at offset 3 and is 12 bytes — if the name comes back
readable, the early offsets are right and confidence in the rest rises sharply.

**Blocked on:** hardware.

---

## §3 — The keygroup header documents offsets 161/162 twice (resolved by decision, unverified)

The keygroup listing defines offsets 161 and 162 **twice**:

- mid-listing as `PFXCHAN` / `PFXSLEV` — the same names the *program* header
  uses — with enumeration `0 = OFF, 1 = FX1, 2 = FX2, 3 = RV3, 4 = RV4`;
- again at the very end of the region, *after* `V_ENV3` at offset 191 (so
  plainly a later addendum), as `KFXCHAN` / `KFXSLEV`, described as "Keygroup
  override Effects Bus select" with enumeration `0 = PRG (use the global
  program header selection), 1 = OFF, 2 = FX1, 3 = FX2, 4 = RV3, 5 = RV4`.

Read together these are **one byte documented at two points in the machine's
life**, not two bytes: the keygroup-level override gained a "use the program's
setting" option, which was prepended and shifted every later value up by one.

**Decision:** the later definition wins. `s3k/params.py` keeps `KFXCHAN` /
`KFXSLEV` and records the superseded names in `notes`, flagged `UNVERIFIED`.
`tests/test_params.py::test_superseded_definition_is_recorded_not_silently_dropped`
pins that the earlier name is still discoverable rather than quietly deleted.

**Why this matters:** if the real machine follows the *earlier* enumeration,
every value read from these two bytes is off by one — displaying "FX2" where
the panel says "FX1".

**To close this:** on hardware, set a keygroup's effects bus from the front
panel to a known value, read offset 161, and see which enumeration matches.
One reading settles it.

**Blocked on:** hardware.

---

## §4 — The note-name octave offset cannot be right as written (open)

The spec words the note-valued fields (`PLAYLO`, `PLAYHI`, `LONOTE`,
`HINOTE`) as "21 to 127 represents A1 to G8". **Those two ends cannot both be
true.** If note 21 is A1 then note 127 is G9; if note 127 is G8 then note 21
is A0. No octave offset satisfies both.

`s3k.params.note_name` anchors to the **low** end — note 21 renders `A1` —
because that is the value the range's own start pins down, and because an
off-by-one-octave label is a display nuisance rather than a data hazard (the
byte written is the same either way).

**To close this:** select a keygroup on the front panel, set its low note to
the lowest available, and read what the panel calls it.

**Blocked on:** hardware.

---

## §5 — The miscellaneous data-index table is missing from the source (open)

`RMISCDATA` / `MISCDATA` (`0x33` / `0x34`) address "miscellaneous variables
and functions" by a 14-bit Data Index, with a bank byte selecting the type
(`1=byte, 2=word, 3=dword, 4=smpte, 5=signed smpte, 6=name, 7=16byteflag`).
Note this family reuses the header shape but **not** its meaning: the bytes
that are "byte offset" elsewhere are documented as reserved zeroes here.

**The table of Data Index values is not in the transcription.** Grepping for
`Data Index` finds only the two message-format definitions; no listing of
which index is which variable or function.

The practical consequence is `BTSORT`. The spec says twice that after writing
`PRGNUM`, "Miscellaneous function BTSORT should be triggered to resort the
list of programs into order and to flag active programs" — but never says what
index invokes it. So s3ked cannot offer a `trigger_btsort()`, and
`s3k/params.py` records the requirement in `PRGNUM`'s `desc` so a user at
least knows to do it from the panel.

**To close this:** find a fuller copy of the Akai document (the printed
original, or another transcription), or discover the index by capturing what
MESA sends when it renumbers a program.

**Blocked on:** a better source, or a MIDI capture of MESA.

---

## §6 — The SysEx send gap is a guess (open)

`s3k.bridge.SEND_GAP` is **0.05 s and unverified**. It is labelled as such at
the point of use.

What is *not* transferable: k2kremote's 120 ms floor was reverse-engineered
against a Kurzweil K2000, whose CPU garbles its LCD under a MIDI flood. That
is a Kurzweil finding. Nothing equivalent is known for this family, and
vintage samplers are entirely capable of hanging when flooded.

`ThrottledOut` already separates the read gap from the write gap, so the two
can be tuned independently once there is evidence — reads are paced by their
own round trip and can usually be cut, writes are fire-and-forget and are the
ones that need protecting.

**To close this:** with something unimportant loaded, walk the gap down
(50 → 25 → 10 → 5 ms) while reading a whole header in a loop, and watch for
garbled replies or a hung front panel. Then repeat for a write loop, which is
the riskier of the two.

**Blocked on:** hardware.

---

## §7 — Autodetect has no broadcast address to lean on (designed, never run live)

The sibling eosed project probes with a standard Universal Device Inquiry sent
to the broadcast device id. **This protocol has neither**: a device answers
only on its own exclusive channel, and the only message that reports what that
channel is (`STAT`) can itself only be obtained by addressing the right
channel. There is no way to ask "who is out there?".

So discovery is a sweep, structured like k2kremote's rather than eosed's:

1. try the remembered `send_port`/`recv_port` pair from `config.toml` first;
2. otherwise open **every** input as a listener, then send `RSTAT`
   (`F0 47 cc 00 48 F7` — harmless and read-only) out each output in turn;
3. accept a port whose reply carries operation code **`STAT` (`0x01`)**, not
   the `RSTAT` (`0x00`) that was sent. Matching on a *different* opcode is
   what distinguishes a real device from a MIDI-Thru loop echoing our own
   bytes — the same trick k2kremote uses with ALLTEXT/SCREENREPLY, and it is
   covered by
   `tests/test_bridge.py::test_autodetect_rejects_a_thru_loop_echoing_our_probe`;
4. wait the whole timeout even after an answer, so a second machine is heard;
5. collapse replies by exclusive channel (one machine on two inputs is one
   machine), and raise `AmbiguousDevice` rather than pick.

`channels=` defaults to `(0,)`, the factory value. Widening it multiplies the
sweep cost by its length, which is why it is not the default.

**To close this:** confirm a real machine answers `RSTAT` on channel 0 out of
the box, and time one full sweep.

**Blocked on:** hardware. The multi-machine paths are additionally
synthetic-only — no two real samplers have ever been connected at once.
