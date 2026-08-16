# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.

"""``s3ked`` -- the Textual terminal editor.

Four panes, left to right and top to bottom: **Programs** (from RPLIST),
**Keygroups** (of the selected program), **Parameters** (of whichever of the
two is focused), and **Samples** (from RSLIST).

Three rules this file exists to enforce, all inherited from the sibling
projects and all learned from real breakage:

* **Every MIDI call runs in a worker thread, serialised by one lock.** The
  bridge is not thread-safe and nothing may issue two requests on one
  connection at once.
* **Writes are gated and destructive operations are never key-bound.** The
  write gate is off by default and shown in the header when armed; the
  deletes live behind an arm-then-fire modal reached only from the Master
  screen.
* **Caches are memory-only and invalidated bluntly.** A front-panel edit is
  invisible to us, so anything persisted would confidently lie.

Unlike the sibling eosed project there is no selection-scoping machinery
here, and that absence is deliberate: EOS's protocol is stateful, so eosed
must re-establish PRESET_SELECT before every access and record the scope on
every undo entry. This protocol carries program number, keygroup and byte
offset explicitly in each message, so none of that complexity is needed.
"""

from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Header,
    Input,
    Label,
    Static,
)

from s3k import messages as m
from s3k import params as p

__all__ = ["S3kedApp", "main"]


@dataclass(frozen=True)
class _Change:
    """One recorded write, enough to replay it backwards.

    Simpler than the sibling eosed project's equivalent because this protocol
    is not stateful: a ``(region, index, keygroup, offset)`` tuple means the
    same thing whenever it is replayed, so there is no selection scope to
    restore first.
    """

    region: str
    index: int
    keygroup: int
    name: str
    old: object
    new: object


class ConfirmScreen(ModalScreen[bool]):
    """Generic yes/no, used for anything that cannot be undone."""

    BINDINGS = [
        Binding("escape", "dismiss_false", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "dismiss_false", "No"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.prompt, id="confirm-prompt")
            yield Label("[b]y[/b] confirm    [b]n[/b] / [b]esc[/b] cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss_false(self) -> None:
        self.dismiss(False)


class LoadOptionsScreen(ModalScreen[Optional[Tuple[bool, bool]]]):
    """How to load: onto what is resident, or onto an emptied machine.

    Three things could be asked here and only two can be answered.

    **All three questions have answers**, which they did not until §94.

    - **What to load** — the LOAD page's eight types. The register the load
      trigger uses *is* that field, and writing value n performs type n, so
      every one of them is a real choice here. The screen opens on whatever
      the panel is showing.

      `Operating System` is deliberately absent from the cycle. It loads an
      OS off the disc over the running one; the bridge guards it behind an
      explicit flag and a keypress is not that.

    The other two:

    - **Clearing first** is not the panel's CLR, which is a panel chain with
      its own on-screen prompt and no remote equivalent (§75). It is deleting
      every resident sample and program, and the last program cannot be
      deleted, so one always survives (`clear_memory`).
    - **Renumbering** is the panel's `RNUM` → `SEQU`, and it matters because
      the load appends and `PRGNUM` is reloaded verbatim: load four volumes
      and four programs claim number 1, stacking on one program change (§91).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "go", "Continue"),
        Binding("t", "next_type", "Load type"),
        Binding("a", "add", "Add to memory"),
        Binding("c", "clear", "Clear first"),
        Binding("n", "toggle_renumber", "Renumber"),
    ]

    #: Offered in this order, panel order minus the guarded one. `Operating
    #: System` (6) is not cycled to: the bridge refuses it without force, and
    #: a load screen is not where somebody should be able to reach an OS
    #: overwrite by holding a key down.
    OFFERED_TYPES = (0, 1, 2, 3, 4, 5, 7)

    #: Types that act on a single directory entry rather than the whole
    #: volume. s3ked places the machine's highlight itself, at `word[7]`
    #: (§97), so these load the row selected in the Disk pane.
    CURSOR_TYPES = frozenset({4, 5})

    def __init__(self, *, resident_programs: int = 0,
                 load_type: Optional[int] = None,
                 item: Optional[int] = None,
                 item_label: str = "") -> None:
        super().__init__()
        self.clear_first = False
        self.renumber = False
        self.resident_programs = resident_programs
        #: The directory entry the Disk pane is sitting on, if any. Passed to
        #: the machine's own highlight before a cursor-type load fires.
        self.item = item
        self.item_label = item_label
        #: What the panel was on when this opened, kept so the screen can say
        #: when the choice has moved away from it.
        self.panel_type = load_type
        self.load_type = load_type if load_type in self.OFFERED_TYPES else 1

    def compose(self) -> ComposeResult:
        with Vertical(id="loadopts-box"):
            yield Label("[b]Load[/b]")
            yield Label("")
            yield Label("", id="loadopts-what")
            yield Label("", id="loadopts-cursor")
            yield Label("", id="loadopts-where")
            yield Label("", id="loadopts-renumber")
            yield Label("")
            yield Label("[b]t[/b] type   [b]a[/b] add   [b]c[/b] clear first"
                        "   [b]n[/b] renumber   [b]enter[/b] go   "
                        "[b]esc[/b] cancel")

    def on_mount(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        # Readable, not settable: the panel writes its selection into the
        # trigger register, and triggering IS writing that register, so
        # firing a load always sets the type to 1 (§93). Showing the value
        # and what firing will do to it beats a menu that cannot reach it.
        name = m.LOAD_TYPES.get(self.load_type, "unnamed")
        if self.panel_type is None:
            note = "[dim]— panel setting could not be read[/dim]"
        elif self.panel_type == self.load_type:
            note = "[dim]— as on the panel[/dim]"
        else:
            note = (f"[dim]— panel: "
                    f"{m.LOAD_TYPES.get(self.panel_type, self.panel_type)}"
                    f"[/dim]")
        self.query_one("#loadopts-what", Label).update(
            rf"  [dim]what:[/dim]  [b]{name}[/b]  \[[b]t[/b]] {note}")
        cursor_note = ""
        if self.load_type in self.CURSOR_TYPES:
            cursor_note = (
                f"  [dim]item:[/dim]  [b]{self.item_label}[/b]"
                if self.item is not None else
                "  [b]no item selected[/b] — pick a row in the Disk pane")
        self.query_one("#loadopts-cursor", Label).update(cursor_note)

        mark = lambda on: "[b]>[/b]" if on else " "
        self.query_one("#loadopts-where", Label).update(
            f"  [dim]where:[/dim]  {mark(not self.clear_first)} [b]a[/b]dd to "
            f"what is resident    {mark(self.clear_first)} [b]c[/b]lear first")

        if self.clear_first:
            note = ("  [dim]clear deletes every sample and program; one "
                    "program survives[/dim]")
        elif self.renumber:
            note = ("  [dim]renumber:[/dim] [b]on[/b] — every program gets a "
                    "distinct number, in list order")
        else:
            note = ("  [dim]renumber:[/dim] off — loaded programs keep their "
                    "own numbers and may collide")
        self.query_one("#loadopts-renumber", Label).update(note)

    def action_next_type(self) -> None:
        """Step the load type. Nothing is sent -- writing it would load."""
        offered = self.OFFERED_TYPES
        at = offered.index(self.load_type) if self.load_type in offered else 0
        self.load_type = offered[(at + 1) % len(offered)]
        self._redraw()

    def action_add(self) -> None:
        self.clear_first = False
        self._redraw()

    def action_clear(self) -> None:
        self.clear_first = True
        self._redraw()

    def action_toggle_renumber(self) -> None:
        # Meaningless when memory is emptied first: nothing is there to
        # collide with, and the volume's own numbering arrives intact.
        if self.clear_first:
            return
        self.renumber = not self.renumber
        self._redraw()

    def action_go(self) -> None:
        if self.load_type in self.CURSOR_TYPES and self.item is None:
            self.app.notify_status(
                "that type loads one item — select one in the Disk pane",
                refused=True)
            return
        self.dismiss((self.clear_first,
                      self.renumber and not self.clear_first,
                      self.load_type,
                      self.item if self.load_type in self.CURSOR_TYPES
                      else None))

    def action_cancel(self) -> None:
        self.dismiss(None)


class MasterScreen(ModalScreen[Optional[str]]):
    """Arm-then-fire menu for the destructive operations.

    Deliberately requires two keypresses -- arm, then Enter to fire -- rather
    than binding any single key to a destructive action. The protocol offers
    no device-side confirmation and no undo for any of these, so the guard
    has to live here. See DISCLAIMER.md and CLAUDE.md's hardware rules.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "fire", "Fire"),
    ]

    _ACTIONS: Dict[str, Tuple[str, str]] = {
        "1": ("delete_program", "Delete the selected program and its keygroups"),
        "2": ("delete_keygroup", "Delete the selected keygroup"),
        "3": ("delete_sample", "Delete the selected sample"),
        "4": ("clear_memory", "Clear memory — delete EVERY program and sample"),
    }

    def __init__(self, context: str) -> None:
        super().__init__()
        self.context = context
        self.armed: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="master-box"):
            yield Label("[b]Destructive operations[/b]", id="master-title")
            yield Label(self.context, id="master-context")
            for key, (_action, description) in self._ACTIONS.items():
                yield Label(f"  [b]{key}[/b]  {description}")
            yield Label("", id="master-armed")
            yield Label(
                "Press a number to arm, [b]Enter[/b] to fire, [b]Esc[/b] to cancel."
            )

    def on_key(self, event) -> None:
        if event.key in self._ACTIONS:
            self.armed = self._ACTIONS[event.key][0]
            self.query_one("#master-armed", Label).update(
                f"[b][ARMED][/b] {self._ACTIONS[event.key][1]} — Enter to fire"
            )
            event.stop()

    def action_fire(self) -> None:
        self.dismiss(self.armed)

    def action_cancel(self) -> None:
        self.dismiss(None)


#: Separator between key hints in the legend, matching k2kremote and eosed.
_LEGEND_SEP = " · "


def wrap_blocks(blocks, width: int, sep: str = _LEGEND_SEP) -> str:
    """Pack ``blocks`` into lines no wider than ``width``, joined by ``sep``.

    Ported from the sibling k2kremote via eosed (same author,
    GPL-2.0-or-later), which solved the identical problem for their own key
    legends. Breaks happen only *between* blocks, so a hint like
    ``l Load volume`` is never split mid-label; a block wider than ``width``
    on its own simply takes its own line rather than being cut.
    """
    lines, current = [], ""
    for block in blocks:
        candidate = block if not current else current + sep + block
        if width and len(candidate) > width and current:
            lines.append(current)
            current = block
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


class KeyHints(Static):
    """The key legend, folded to the terminal's width over as many lines as it needs.

    **Replaces Textual's ``Footer``**, which is hardcoded to one line and
    truncates rather than wrapping. At 80x24 -- the smallest size this project
    claims to support -- it showed six of thirteen bindings and silently cut
    the rest, so the disk, load, menu, boards and audit keys were
    undiscoverable to anyone who had not read the README. Neither
    ``height: auto`` nor a grid layout changes that; both were measured.

    The approach is k2kremote's and eosed's, ported rather than reinvented:
    one line on a wide terminal, more on a narrow one, and nothing ever
    hidden.
    """

    DEFAULT_CSS = "KeyHints { height: auto; }"

    def __init__(self, blocks, *, id=None):
        super().__init__(id=id)
        self._blocks = list(blocks)

    def on_mount(self) -> None:
        self._render_hints()

    def on_resize(self, event) -> None:
        self._render_hints()

    def set_blocks(self, blocks) -> None:
        """Replace the legend. The right column shows one of two things and
        half the keys change with it, so the hints change too."""
        self._blocks = list(blocks)
        self._render_hints()

    def _render_hints(self) -> None:
        self.update(wrap_blocks(self._blocks, self.size.width))


class ReportScreen(ModalScreen[None]):
    """A read-only result: integrity, or who uses a sample."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, title: str, body: str, footer: str) -> None:
        super().__init__()
        self.title_text = title
        self.body = body
        self.footer = footer

    def compose(self) -> ComposeResult:
        with Vertical(id="report-box"):
            yield Label(f"[b]{self.title_text}[/b]", id="report-title")
            with VerticalScroll(id="report-scroll"):
                yield Static(self.body, id="report-body")
            yield Label(self.footer, id="report-footer")
            yield Label("[b]Esc[/b] close")

    def action_close(self) -> None:
        self.dismiss(None)


class SourceScreen(ModalScreen[None]):
    """Pick what the LOAD page points at: SCSI device, media, partition.

    **Stays open across changes.** It used to dismiss on every keypress, so
    setting a drive and a partition meant opening it twice -- and the disk was
    re-read after each, seven round trips a time. Now each key applies
    immediately, the rows update in place, and the directory is re-read once
    when the dialog closes.

    Every row here is a miscellaneous-data byte found by changing it on the
    front panel and seeing which one moved -- the specification documents the
    addressing and not the meanings.

    The volume is settable too, since §96: it is `byte[4]`, which this
    project had already found and called a hold flag. It is chosen from the
    Disk pane rather than here, because choosing it needs the list of volumes
    beside it -- `byte[49]` still reads like a volume and still is not one.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, source: Dict[str, int],
                 device_types: Dict[int, str]) -> None:
        super().__init__()
        self.source = dict(source or {})
        # passed in rather than imported: app.py must not import s3k.bridge,
        # which pulls in rtmidi, or --demo stops working without it
        self.device_types = device_types
        self.changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="source-box"):
            # "SCSI" is the sampler's own word for this page --
            # the LOAD page reaches it with F5, labelled SCSI.
            yield Label("[b]SCSI[/b]", id="source-title")
            yield Label(self._drive_row(), id="source-drive")
            yield Label(self._device_row(), id="source-device")
            yield Label(self._partition_row(), id="source-partition")
            yield Label("  Volume          [dim]settable — Enter on a row in "
                        "the Disk pane[/dim]", id="source-volume")
            yield Label("")
            yield Label("[b]Volumes on this drive and partition[/b]",
                        id="source-vols-title")
            # Cheap enough to re-read on every change: the volume list pages
            # sixteen records a request, so a 30-volume disc is two round
            # trips. The DIRECTORY is the expensive one -- one request per
            # entry -- and is still deferred to when this closes.
            with VerticalScroll(id="source-vols"):
                yield Label("", id="source-vols-body")
            yield Label("")
            yield Label("[dim]Each key writes to the machine at once. The "
                        "directory is re-read when\n  this closes, not after "
                        "every change.[/dim]")
            yield Label("[b]Esc[/b] close", id="source-close")

    def _drive_row(self) -> str:
        return (f"  SCSI drive      [b]{self.source.get('scsi_drive_id', '?')}"
                f"[/b]        press [b]0[/b]-[b]7[/b]")

    def _device_row(self) -> str:
        kind = self.source.get("device_type")
        name = self.device_types.get(kind, f"? ({kind})")
        return (f"  Device          [b]{name}[/b]"
                "     [b]f[/b] floppy   [b]h[/b] hard   [b]x[/b] flash")

    def _partition_row(self) -> str:
        part = self.source.get("partition")
        shown = chr(65 + part) if isinstance(part, int) else "?"
        # \[ is Rich's escape for a literal bracket. Writing [b][[/b] renders
        # "[/b]" instead: Rich reads [[ as the escape and the /b] falls
        # through as plain text.
        return (f"  Partition       [b]{shown}[/b]"
                "        [b]\\[[/b] and [b]][/b] here, or in the panes")

    def update_volumes(self, volumes) -> None:
        """List what is on the drive and partition now selected.

        The point is finding the disc you meant: stepping the SCSI ID with
        nothing to show is guesswork, and this family gives no other way to
        tell one drive from another without loading from it.
        """
        try:
            body = self.query_one("#source-vols-body", Label)
            title = self.query_one("#source-vols-title", Label)
        except Exception:
            return

        # The title is set on EVERY path. Updating it only when there were
        # volumes left the old count sitting above "nothing here", so an
        # empty partition read as though it still held the previous disc's
        # eighteen.
        if volumes is None:
            title.update("[b]Volumes here[/b]")
            body.update("  [dim]could not be read[/dim]")
        elif not volumes:
            title.update("[b]Volumes here[/b]  [dim](none)[/dim]")
            body.update("  [dim]nothing on this partition[/dim]")
        else:
            title.update(f"[b]Volumes here[/b]  [dim]({len(volumes)})[/dim]")
            body.update("\n".join(
                f"  [b]v{v.index}[/b]  {v.name.strip()}" for v in volumes))

    def update_source(self, source: Dict[str, int]) -> None:
        """Re-render the rows from what the machine now reports."""
        self.source = dict(source or {})
        self.changed = True
        try:
            self.query_one("#source-drive", Label).update(self._drive_row())
            self.query_one("#source-device", Label).update(self._device_row())
            self.query_one("#source-partition", Label).update(
                self._partition_row())
        except Exception:
            pass

    def on_key(self, event) -> None:
        # `event.character`, not `event.key`. Textual names punctuation keys:
        # "[" arrives as "left_square_bracket", so matching on `key` silently
        # never fired and the partition could not be stepped from this dialog
        # at all -- while the same keys worked on the main screen, because a
        # Binding("[") is resolved by Textual rather than compared here.
        key = event.character or event.key
        if key in "01234567" and len(key) == 1:
            change = ("drive", int(key))
        elif key in ("f", "h", "x"):
            change = ("device", {"f": 0, "h": 1, "x": 2}[key])
        elif key in ("[", "]"):
            change = ("partition", -1 if key == "[" else +1)
        else:
            return
        event.stop()
        self.app.apply_source_change(*change)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LoadingScreen(ModalScreen[None]):
    """Held open while the sampler loads, because nothing else can tell us.

    There is no completion signal. `STAT` carries no busy flag, and the only
    way to watch progress is to poll -- which is what preceded the machine
    sitting at BUSY until a power cycle (§71). So the person watching the
    front panel is the sensor, and closing this is the signal.

    Deliberately short. It is read while waiting for a machine, not studied.
    """

    BINDINGS = [
        Binding("escape", "done", "Done"),
        Binding("enter", "done", "Done"),
        Binding("q", "done", "Done"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-box"):
            yield Label("[b]Loading[/b]")
            yield Label("")
            yield Label("Close when the sampler has finished.")
            yield Label("[dim]Nothing is being sent to it meanwhile.[/dim]")
            yield Label("")
            yield Label("[b]Esc[/b] — then the lists refresh")

    def action_done(self) -> None:
        self.dismiss(None)


class BoardsScreen(ModalScreen[Optional[set]]):
    """Declare which expansion boards the machine has.

    The device cannot be asked. No reply carries a fitted-options field, and
    the mode register opens pages the panel refuses (§86), so nothing on the
    wire distinguishes a fitted board from an absent one.

    It matters because the fields behind these boards are not merely inert
    without them: the panel gates their pages outright, and an S3000XL was
    crashed twice in one session with the same flooding-display signature
    while that area was being exercised (§85, §90). So the default is to
    assume nothing is fitted and refuse those fields.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    _BOARDS = {
        "1": ("IB304F", "2nd filter board — filter 2, tone section, envelope 3"),
        "2": ("EB16", "multi-effects board — the EFFECTS pages"),
    }

    def __init__(self, fitted: set) -> None:
        super().__init__()
        self.fitted = set(fitted)

    def compose(self) -> ComposeResult:
        with Vertical(id="boards-box"):
            yield Label("[b]Expansion boards fitted[/b]", id="boards-title")
            yield Label("")
            for key, (name, what) in self._BOARDS.items():
                yield Label(self._row(key, name, what), id=f"board-{name}")
            yield Label("")
            yield Label("[dim]Fields behind an undeclared board are refused, "
                        "for reading and writing\n  alike. The machine cannot "
                        "be asked which are fitted, so this is a\n  declaration "
                        "— and a wrong one is how a sampler gets "
                        "crashed.[/dim]")
            yield Label("[b]1[/b]/[b]2[/b] toggle    [b]enter[/b] save    "
                        "[b]esc[/b] cancel")

    def _row(self, key: str, name: str, what: str) -> str:
        mark = "[b]fitted[/b]" if name in self.fitted else "[dim]not fitted[/dim]"
        return f"  [b]{key}[/b]  {name:<8} {mark}   [dim]{what}[/dim]"

    def on_key(self, event) -> None:
        if event.key in self._BOARDS:
            name = self._BOARDS[event.key][0]
            self.fitted ^= {name}
            self.query_one(f"#board-{name}", Label).update(
                self._row(event.key, name, self._BOARDS[event.key][1]))
            event.stop()
        elif event.key == "enter":
            self.dismiss(self.fitted)
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)


class MenuScreen(ModalScreen[Optional[int]]):
    """Jump the machine to any of its eleven main-menu pages.

    This is not button injection -- there is no keypress message anywhere in
    this protocol. The current page is a variable, `byte[91]`, and writing it
    moves the machine.

    All eleven values are named, read off the panel one at a time (§84).
    Naming them needed somebody at the machine, because the enumeration has
    gaps that rule out guessing: GLOBAL is the second button of the second
    row and reads 8, where its position would make it 5.

    **Stays open across changes**, like the SCSI screen. It used to
    dismiss on the first key that matched, so choosing the wrong page meant
    pressing `g` again to get back -- and this is the sort of tool where Esc
    should be the only way out of a dialog.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: Optional[int], modes: Dict[int, str]) -> None:
        super().__init__()
        self.current = current
        self.modes = modes

    def compose(self) -> ComposeResult:
        here = self.modes.get(self.current, f"unnamed ({self.current})")
        with Vertical(id="menu-box"):
            yield Label("[b]Main menu[/b]", id="menu-title")
            yield Label(f"  now showing: [b]{here}[/b]", id="menu-here")
            yield Label("")
            for key, (value, name) in self._CHOICES.items():
                yield Label(f"  [b]{key}[/b]  {name}  [dim]({value})[/dim]")
            yield Label("")
            yield Label("[dim]EDIT is a modifier, not a page: eight buttons, "
                        "seven modes, and EDIT\n  combines with four of them "
                        "— which is the eleven the manual counts.[/dim]")
            yield Label("[b]Esc[/b] close  [dim]— the page changes as you "
                        "press, and this stays open[/dim]")

    #: All eleven, keyed 0-9 then a for LOAD. The order is the register's
    #: own, which is also the panel's: base/edit pairs, then the three
    #: disk-and-system pages.
    _CHOICES = {
        "0": (0, "SINGLE"),   "1": (1, "SINGLE EDIT"),
        "2": (2, "MULTI"),    "3": (3, "MULTI EDIT"),
        "4": (4, "SAMPLE"),   "5": (5, "SAMPLE EDIT"),
        "6": (6, "EFFECTS"),  "7": (7, "EFFECTS EDIT"),
        "8": (8, "GLOBAL"),   "9": (9, "SAVE"),
        "a": (10, "LOAD"),
    }

    def on_key(self, event) -> None:
        # `event.character`, not `event.key`, for the same reason SourceScreen
        # uses it: Textual names some keys rather than passing the character.
        key = event.character or event.key
        if key in self._CHOICES:
            event.stop()
            self.app.apply_menu_change(self._CHOICES[key][0])

    def show_current(self, current) -> None:
        """Repaint the "now showing" line from what the machine REPORTS.

        Not from what was asked for. Writing 0 answers REPLY *error* and
        switches the page anyway, and other values answer OK -- the
        acknowledgement is wrong in both directions, so only the read-back
        tells the truth (§79).
        """
        self.current = current
        here = self.modes.get(current, f"unnamed ({current})")
        try:
            self.query_one("#menu-here", Label).update(
                f"  now showing: [b]{here}[/b]")
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditValueScreen(ModalScreen[Optional[str]]):
    """Prompt for a new parameter value."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, param: p.Parameter, current) -> None:
        super().__init__()
        self.param = param
        self.current = current

    def compose(self) -> ComposeResult:
        param = self.param
        span = (
            f"{param.minimum}..{param.maximum}"
            if param.kind == "num"
            else f"text, up to {param.size} characters"
        )
        with Vertical(id="edit-box"):
            yield Label(f"[b]{param.name}[/b]  ({param.group})")
            if param.desc:
                yield Label(param.desc, id="edit-desc")
            yield Label(f"range: {span}")
            if param.notes:
                yield Label(f"note: {param.notes}", id="edit-note")
            yield Input(value=str(self.current), id="edit-input")

    def on_mount(self) -> None:
        self.query_one("#edit-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class S3kedApp(App):
    """The editor."""

    CSS = """
    Screen { layout: vertical; }
    #panes { height: 1fr; }
    #left { width: 40%; }
    /* Four stacked tables in the left column. The disk is the one that is
       empty until asked for, so it gets the smallest share. */
    #volumes { height: 1fr; }
    #right { width: 60%; }
    DataTable { height: 1fr; }
    .pane-title { background: $panel; padding: 0 1; }
    #status { height: auto; padding: 0 1; }
    /* A refusal has to be seen. The gate-locked message sat in the same
       muted style as "5 program(s), 9 sample(s)", so pressing a key and
       having nothing happen read as a broken feature rather than a closed
       gate -- which is exactly how it was reported. */
    #status.-refused { background: $error; color: $text; text-style: bold; }
    /* Red, not the accent colour. The armed gate is the one state where a
       keypress reaches the hardware, and $accent is also used for ordinary
       emphasis elsewhere -- a warning that looks like decoration is not a
       warning. $error is the theme's red and stays red across themes. */
    Header.-write-armed { background: $error; color: $text; text-style: bold; }
    /* max-width, not just width: a fixed 70 columns is wider than the
       window the moment somebody resizes, and the overflow is CLIPPED rather
       than wrapped -- the same failure the Footer had, which is why KeyHints
       exists. Reported from live use as truncated dialog text. */
    #confirm-box, #master-box, #edit-box, #loadopts-box {
        width: 70; max-width: 100%; padding: 1 2;
        border: thick $panel; background: $surface;
    }
    /* And the labels inside must wrap to the box rather than size to their
       own content and push past it. */
    #loadopts-box Label, #confirm-box Label, #master-box Label {
        width: 100%;
    }
    #edit-desc, #edit-note { color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        # Not Enter: a focused DataTable consumes it for row selection, so an
        # app-level Enter binding never fires. Enter still works, via
        # on_data_table_row_selected below.
        Binding("e", "edit", "Edit value"),
        Binding("w", "toggle_write", "Write gate"),
        Binding("z", "undo", "Undo"),
        Binding("Z", "undo_all", "Undo all"),
        Binding("plus", "nudge_up", "Nudge +", show=False),
        Binding("minus", "nudge_down", "Nudge -", show=False),
        Binding("equals_sign", "nudge_up", "Nudge +", show=False),
        Binding("h", "history", "History"),
        Binding("m", "master", "Master"),
        Binding("d", "disk", "Read disk"),
        Binding("[", "partition_prev", "Prev partition", show=False),
        Binding("]", "partition_next", "Next partition", show=False),
        Binding("l", "load_volume", "Load"),
        Binding("escape", "close_disk", "Back", show=False),
        Binding("s", "source", "SCSI"),
        Binding("g", "menu", "Main menu"),
        Binding("B", "boards", "Boards fitted"),
        Binding("i", "integrity", "Integrity"),
        Binding("a", "all_samples", "All samples"),
        Binding("u", "usage", "Who uses"),
        Binding("tab", "focus_next", "Next pane", show=False),
    ]

    def __init__(self, bridge, *, allow_write: bool = False,
                 config_path: Optional[str] = None) -> None:
        super().__init__()
        self.bridge = bridge
        self.allow_write = allow_write
        #: Where a board declaration is persisted. None means this session
        #: only -- the demo and most tests, which must not write a user's file.
        self._config_path = config_path
        self._bridge_lock = threading.Lock()
        self._programs: List[str] = []
        self._samples: List[str] = []
        self._keygroups: int = 0
        self._program_keygroups: List[dict] = []
        self._disk_entries: List[object] = []
        #: Whether `d` has ever run. An empty Disk pane is the normal startup
        #: state, not a failure, and `r` only picks the disk up once there is
        #: something to pick up -- see action_refresh.
        self._disk_read = False
        #: Which of the two things the right column is showing.
        self._disk_showing = False
        #: Set by a refresh so _apply_program can put the parameter pane back
        #: where it was, rather than dragging it to the program view.
        self._restore_context = None
        #: True while a table is being repopulated by us. Filling a DataTable
        #: fires row-highlighted events, and the programs branch of that
        #: handler has no has-focus guard -- by design, so arrowing a program
        #: loads it -- which meant every refresh reloaded program 0 and
        #: dragged the parameter pane along with it.
        self._refilling = False
        #: Which program the parameter pane is currently built from. The
        #: row-highlighted handler compares against it, because a flag cannot
        #: cover a Textual message that is DELIVERED LATER: move_cursor posts
        #: its event, the handler runs after the guard has been cleared, and
        #: the reload lands anyway. Comparing state works whenever it runs.
        self._loaded_program = None
        #: Set for the duration of a nudge's write, so _after_write can
        #: collapse a run into one undo entry and skip the catalog re-read.
        self._nudging = None
        #: Whether the samples pane lists everything resident or only what
        #: the selected program references. The program-centric redesign
        #: dropped the global list entirely, which lost the view the audit
        #: work is done from.
        self._samples_show_all = False
        self._words_free: Optional[int] = None
        self._total_words: Optional[int] = None
        self._param_values: Dict[str, object] = {}
        #: What the parameter pane is currently showing: (region, index,
        #: keygroup). The edit path writes through this, not through the
        #: selected program -- showing keygroup fields while writing to the
        #: program header would corrupt a different parameter at the same
        #: offset, silently.
        self._param_context: Tuple[str, int, int] = ("program", 0, 0)
        self._param_rows: List[p.Parameter] = []
        self._undo: List[_Change] = []
        self.last_status = ""  # exposed for tests

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            with Vertical(id="left"):
                yield Static("Programs", classes="pane-title")
                yield DataTable(id="programs", cursor_type="row")
                yield Static("Keygroups", classes="pane-title")
                yield DataTable(id="keygroups", cursor_type="row")
                yield Static("Samples used", classes="pane-title",
                             id="progsamples-title")
                yield DataTable(id="samples", cursor_type="row")
            # The right column is one pane showing one of two things. The
            # disk browser used to be a quarter of the left column, where a
            # 55-item volume listing had five rows to live in and the four
            # program-centric panes were squeezed for it. Neither is wanted
            # at the same time as the other: reading a parameter table and
            # picking something to load are different jobs.
            with Vertical(id="right"):
                yield Static("Parameters", classes="pane-title", id="param-title")
                yield DataTable(id="parameters", cursor_type="row")
                yield Static("Disk", classes="pane-title", id="disk-title")
                yield DataTable(id="volumes", cursor_type="row")
        yield Static("", id="status")
        # Not Footer(): it is one line and truncates. See KeyHints.
        yield KeyHints(
            [f"{b.key} {b.description}"
             for b in self.BINDINGS if b.description and b.show],
            id="keyhints")

    def on_mount(self) -> None:
        self.title = "s3ked"
        self.sub_title = self.bridge.description
        # Says what to press rather than sitting blank: an empty pane with a
        # one-word title reads as broken, and this one is empty on every
        # launch by design.
        self.query_one("#disk-title", Static).update("Disk — press [b]d[/b]")
        # The right column starts on Parameters. Both sets of widgets exist
        # from the start so nothing has to be built on the way in.
        for widget_id in ("disk-title", "volumes"):
            self.query_one(f"#{widget_id}").display = False
        for table_id, columns in (
            ("programs", ("num", "name")),
            ("keygroups", ("kg", "range")),
            ("samples", ("sample", "status")),
            ("volumes", ("vol", "name")),
            ("parameters", ("off", "name", "value")),
        ):
            table = self.query_one(f"#{table_id}", DataTable)
            table.add_columns(*columns)
        self._refresh_write_badge()
        self.action_refresh()

    # -- status / badges ----------------------------------------------------

    def notify_status(self, message: str, *, refused: bool = False) -> None:
        """Put a message on the status line.

        ``refused`` marks it as something the user asked for and did not
        get -- a locked gate, an undeclared board, a guard that fired. Those
        render on the error colour, because a refusal that looks like a
        progress report is indistinguishable from the feature being broken.
        """
        self.last_status = message
        try:
            widget = self.query_one("#status", Static)
            widget.update(message)
            widget.set_class(refused, "-refused")
        except Exception:
            pass

    def _refresh_write_badge(self) -> None:
        header = self.query_one(Header)
        header.set_class(self.allow_write, "-write-armed")
        pending = f"  |  {len(self._undo)} change(s)" if self._undo else ""
        self.sub_title = (
            f"{self.bridge.description}  |  "
            f"write {'ARMED' if self.allow_write else 'locked'}{pending}"
        )

    # -- workers ------------------------------------------------------------

    @work(thread=True)
    def _load_catalog(self, announce: bool = True) -> None:
        try:
            with self._bridge_lock:
                programs = self.bridge.program_list()
                samples = self.bridge.sample_list()
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        self.call_from_thread(self._apply_catalog, programs, samples, announce)

    def _apply_catalog(
        self, programs: List[str], samples: List[str], announce: bool = True
    ) -> None:
        self._programs = programs
        self._samples = samples
        table = self.query_one("#programs", DataTable)
        self._refilling = True
        try:
            table.clear()
            for index, name in enumerate(programs):
                table.add_row(str(index), name)
        finally:
            self._refilling = False
        # The #samples pane is the SELECTED PROGRAM's samples now, filled by
        # _fill_program_samples. The global resident list stays in
        # self._samples, which the integrity check and `u` both use.
        # A refresh triggered by a write must not clobber that write's
        # confirmation -- the catalog reload finishes last, so without this
        # the user only ever sees the program count.
        if announce:
            self.notify_status(
                f"{len(programs)} program(s), {len(samples)} sample(s)"
            )
        if not programs:
            return
        # Keep the selection. This re-selected program 0 unconditionally, and
        # since every parameter write ends with a catalog reload, editing a
        # keygroup field bounced the pane back to program 0's parameters
        # immediately afterwards -- so a second edit went somewhere else
        # entirely. The same class as the disk pane's cursor jumping to v0:
        # a refresh must not move the user.
        selected = self._selected_program() or 0
        if selected >= len(programs):
            selected = 0
        table.move_cursor(row=selected)
        region, index, keygroup = self._param_context
        self._load_program(selected, restore=(region, index, keygroup))

    @work(thread=True)
    def _load_program_worker(self, index: int) -> None:
        try:
            with self._bridge_lock:
                header = self.bridge.get_header("program", index)
                keygroups = self._read_keygroups(index, header)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        self.call_from_thread(self._apply_program, index, header,
                              keygroups)

    def _read_keygroups(self, program: int, header) -> List[dict]:
        """Each keygroup's key range and the samples its zones name.

        **One read per keygroup, not five.** The whole 192-byte header comes
        back in a single request, so the key range and all four zone names
        cost exactly what the range alone used to. A 61-keygroup program is
        61 round trips.

        The offsets are good: §81 wrote ``LONOTE``/``HINOTE`` while measuring
        whether overlapping keygroups layer, and the machine sounded or
        stayed silent exactly as predicted across six settings. The zone
        names are the same field `analysis.collect` walks.

        A keygroup that will not read yields a row marked unreadable rather
        than failing the pane: a partial answer about the others is worth
        more than none.
        """
        count = int(header.get("GROUPS", 0) or 0)
        size = p.REGION_SIZES["keygroup"]
        lo_off = p.lookup(("keygroup", "LONOTE")).offset
        hi_off = p.lookup(("keygroup", "HINOTE")).offset
        zone_offs = [p.lookup(("keygroup", n)).offset
                     for n in ("SNAME1", "SNAME2", "SNAME3", "SNAME4")]
        out: List[dict] = []
        for kg in range(count):
            try:
                raw = self.bridge.get_header_bytes(
                    "keygroup", program, 0, size, selector=kg)
            except Exception:
                out.append({"lo": -1, "hi": -1, "samples": [], "read": False})
                continue
            names = []
            for off in zone_offs:
                chunk = raw[off:off + m.NAME_LENGTH]
                if not any(chunk):
                    continue                     # unwritten
                name = m.decode_name(list(chunk))
                if name.strip():                 # twelve spaces = unassigned
                    names.append(name.strip())
            out.append({"lo": int(raw[lo_off]), "hi": int(raw[hi_off]),
                        "samples": names, "read": True})
        return out

    def _load_program(self, index: int, restore=None) -> None:
        """Read one program and fill the panes from it.

        ``restore`` is the ``_param_context`` to put back afterwards, used by
        a refresh so that re-reading the catalog does not drag the parameter
        pane back to the program view while somebody is editing a keygroup.
        """
        self._restore_context = restore
        self._load_program_worker(index)

    def _apply_program(self, index: int, header: Dict[str, object],
                       keygroups=None) -> None:
        """Fill the keygroup pane and the samples-used pane for one program.

        The panes are program-centric on purpose. The samples pane used to be
        the machine's whole `SLIST` -- a global inventory, which answers "what
        is in memory" rather than "what does this program need". Work here is
        program-first, so the pane now lists what the selected program
        references and says which of those the machine does not hold.
        """
        self._keygroups = int(header.get("GROUPS", 0) or 0)
        self._program_keygroups = list(keygroups or ())

        table = self.query_one("#keygroups", DataTable)
        table.clear()
        for kg in range(self._keygroups):
            row = (self._program_keygroups[kg]
                   if kg < len(self._program_keygroups) else None)
            if row is None or not row["read"]:
                table.add_row(str(kg), "?")
                continue
            lo, hi = row["lo"], row["hi"]
            # An inverted range selects nothing, measured (§81). Printing it
            # as a range would read as a keygroup spanning it backwards.
            span = (f"{p.note_name(lo)}–{p.note_name(hi)}"
                    + ("  (dead)" if lo > hi else ""))
            table.add_row(str(kg), span)

        self._fill_program_samples()
        self.query_one("#param-title", Static).update(
            f"Parameters — program {index} ({header.get('PRNAME', '')})"
        )
        # A refresh restores whatever the pane was showing; a deliberate
        # program selection shows the program, which is what was asked for.
        self._loaded_program = index
        restore = getattr(self, "_restore_context", None)
        self._restore_context = None
        if restore and restore[0] != "program":
            region, at, keygroup = restore
            self._show_params("program", header, index)
            self._param_context = (region, at, keygroup)
        else:
            self._show_params("program", header, index)

    def action_all_samples(self) -> None:
        """Swap the samples pane between this program's and every resident one.

        The redesign made that pane program-centric and quietly removed the
        only view of what the machine actually holds. `u` and the audit both
        still read the full list; nothing showed it.
        """
        self._samples_show_all = not self._samples_show_all
        self._fill_program_samples()
        self.notify_status(
            "samples: everything resident" if self._samples_show_all
            else "samples: what this program uses")

    def _fill_all_samples(self) -> None:
        """Every resident sample, marking the ones this program references.

        Deliberately NOT a full cross-reference: knowing which samples no
        program anywhere uses means walking every keygroup of every program,
        which is what `i` does and what it takes seconds to do. This is the
        cheap view -- the machine's own sample list, with the current
        program's usage overlaid from data already in hand.
        """
        table = self.query_one("#samples", DataTable)
        table.clear()
        used = {
            name
            for row in self._program_keygroups
            for name in row.get("samples", ())
        }
        for name in self._samples:
            table.add_row(name, "used" if name.strip() in
                          {u.strip() for u in used} else "")
        self.query_one("#progsamples-title", Static).update(
            f"All samples  [dim]({len(self._samples)} resident — "
            f"[b]a[/b] for this program)[/dim]")

    def _fill_program_samples(self) -> None:
        """What this program references, and which of it the machine lacks.

        Missing first, because that is the only thing here with no other
        indicator: an over-budget load reports "insufficient waveform memory"
        once and then behaves normally, leaving programs resident, selectable
        and silent (§73). Everything else in this pane the sampler will tell
        you itself.

        A zone that cannot sound is not counted as missing -- an inverted or
        zero-height velocity range keeps whatever name it last held (§81),
        and reporting that as a fault is a problem the user cannot act on and
        did not cause.
        """
        if self._samples_show_all:
            self._fill_all_samples()
            return
        table = self.query_one("#samples", DataTable)
        table.clear()
        resident = {s.strip() for s in self._samples}

        used: List[str] = []
        for row in self._program_keygroups:
            for name in row.get("samples", ()):
                if name not in used:
                    used.append(name)

        missing = [n for n in used if n not in resident]
        present = [n for n in used if n in resident]
        for name in missing:
            table.add_row(name, "MISSING")
        for name in present:
            table.add_row(name, "ok")

        title = self.query_one("#progsamples-title", Static)
        hint = "  [dim]([b]a[/b] for all resident)[/dim]"
        if missing:
            title.update(
                f"Samples used — {len(missing)} MISSING of {len(used)}{hint}")
        else:
            title.update(f"Samples used — {len(used)}{hint}")

    def _show_params(self, region: str, values: Dict[str, object],
                     index: int = 0, keygroup: int = 0) -> None:
        self._param_values = values
        self._param_context = (region, index, keygroup)
        self._param_rows = p.region_params(region)
        table = self.query_one("#parameters", DataTable)
        # Keep the cursor on the same PARAMETER across a rebuild. clear()
        # resets it to row 0, and this runs after every write -- so an edit
        # bounced the cursor onto PRIDENT, a read-only block address, and a
        # held nudge stepped its field once and then refused for as long as
        # the key was down. Remembered by NAME, since the row set changes
        # with the region.
        was_on = None
        if 0 <= table.cursor_row < len(self._param_rows):
            was_on = self._param_rows[table.cursor_row].name
        table.clear()
        for param in self._param_rows:
            table.add_row(
                str(param.offset),
                param.name,
                p.describe_value(param, values.get(param.name)),
            )
        if was_on is not None:
            for row, param in enumerate(self._param_rows):
                if param.name == was_on:
                    table.move_cursor(row=row)
                    break

    # -- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        """Re-read what is on screen.

        The disk joins in only once `d` has read it. It is 7 round trips for
        a full disk and a machine with none attached fails the read, so
        paying that on every refresh -- including the one at startup -- would
        make the common case worse to spare one keypress in the other. Once
        the pane is showing something, a refresh that leaves it stale is the
        surprising behaviour instead, which is how this got noticed.
        """
        self.notify_status("reading catalog...")
        self._load_catalog()
        if self._disk_read:
            self._read_disk_worker()

    def action_disk(self) -> None:
        """Show the disk browser, reading the volume list off the disk.

        Deliberately not part of the startup catalog. It is 7 round trips and
        about 1.2 s for a full disk, and a machine with no disk attached would
        make that a failure on every launch rather than on request.
        """
        self._show_disk_pane(True)
        self.notify_status("reading the disk…")
        self._read_disk_worker()

    @work(thread=True)
    def _read_disk_worker(self) -> None:
        try:
            with self._bridge_lock:
                source, source_error = None, None
                try:
                    source = self.bridge.load_source()
                except Exception as exc:
                    # Carried to the end rather than reported here: a status
                    # emitted mid-worker is overwritten by the completion
                    # message a moment later, so the user never sees it. An
                    # older machine may genuinely not answer, and silence
                    # would look like a device with no LOAD page.
                    source_error = str(exc)
                volumes = self.bridge.volume_list()
                try:
                    entries = self.bridge.hd_directory(1)
                except Exception:
                    # A machine with no volume loaded answers with empty
                    # records rather than an error, but an older one might
                    # not implement RHDDIR at all -- the volumes are still
                    # worth showing.
                    entries = []
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"disk: {exc}")
            return
        # A load -- from `l`, or from the front panel while this was open --
        # changes what is resident, and the machine announces nothing. Jan hit
        # exactly this: loaded a volume, and the program list still showed the
        # seven from before. Two extra requests, taken whenever the user is
        # already doing disk work, which is when a load is likely to have
        # happened.
        try:
            with self._bridge_lock:
                programs = self.bridge.program_list()
                samples = self.bridge.sample_list()
        except Exception:
            programs = samples = None
        self.call_from_thread(self._show_volumes, volumes, entries, source,
                              source_error)
        if programs is not None:
            self.call_from_thread(self._refresh_catalog_lists, programs, samples)

    def _refresh_catalog_lists(self, programs, samples) -> None:
        """Re-fill the program list if it has gone stale under us."""
        if programs == self._programs and samples == self._samples:
            return
        previous = len(self._programs)
        self._programs, self._samples = list(programs), list(samples)
        table = self.query_one("#programs", DataTable)
        row = table.cursor_row
        self._refilling = True
        try:
            table.clear()
            for index, name in enumerate(self._programs):
                table.add_row(str(index), name)
            if row is not None and row < table.row_count:
                table.move_cursor(row=row)
        finally:
            self._refilling = False
        self.notify_status(
            f"catalog changed while you were away: {previous} → "
            f"{len(self._programs)} program(s), {len(self._samples)} sample(s)")

    @staticmethod
    def _describe_source(source) -> str:
        """The LOAD page as the panel writes it: HARD-:C vol 003 (SCSI 3).

        The volume is 1-based here and 0-based in the register, matching the
        panel (§96). It went missing from this line for an hour on the way:
        the key really was absent from `load_source()`, because the register
        had been found and misnamed rather than not found.
        """
        if not source:
            return ""
        device = {0: "FLOPPY", 1: "HARD", 2: "FLASH"}.get(
            source.get("device_type"), f"DEV{source.get('device_type')}")
        letter = chr(65 + source.get("partition", 0))
        volume = source.get("volume")
        shown = f" vol {volume + 1:03d}" if volume is not None else ""
        return f"{device}-:{letter}{shown}  (SCSI {source.get('scsi_drive_id')})"

    def action_partition_prev(self) -> None:
        self._step_partition(-1)

    def action_partition_next(self) -> None:
        self._step_partition(+1)

    def _step_partition(self, delta: int) -> None:
        """Move the LOAD selection. This WRITES, so the gate applies.

        It loads nothing -- the protocol cannot -- but it does change what the
        machine has selected, and the front panel follows. Anything that
        changes the device belongs behind the same gate as an edit.
        """
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return
        self._step_partition_worker(delta)

    @work(thread=True)
    def _step_partition_worker(self, delta: int) -> None:
        try:
            with self._bridge_lock:
                current = self.bridge.load_source()["partition"]
                self.bridge.select_partition(max(0, min(7, current + delta)))
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"partition: {exc}")
            return
        self.call_from_thread(self.action_disk)

    def action_integrity(self) -> None:
        """Which zones name a sample the machine does not hold.

        These are the silent ones. A load that overran memory leaves the
        programs resident and selectable, and nothing on the machine says
        which of them lost their samples (§73) -- this is the only place
        that information exists.
        """
        self.notify_status("walking every keygroup…")
        self._audit_worker(None)

    def action_usage(self) -> None:
        """Every zone naming the selected sample, across all programs.

        The name comes from the row, not from indexing the global sample
        list. That list and this pane were the same thing until the pane
        became program-centric; indexing one by the other's cursor now picks
        a different sample entirely, and would do it silently.
        """
        table = self.query_one("#samples", DataTable)
        row = table.cursor_row
        if row is None or row >= table.row_count:
            self.notify_status("select a sample first")
            return
        name = str(table.get_row_at(row)[0]).strip()
        self.notify_status(f"looking for uses of {name!r}…")
        self._audit_worker(name)

    @work(thread=True)
    def _audit_worker(self, sample: Optional[str]) -> None:
        from s3k import analysis

        try:
            with self._bridge_lock:
                audit = analysis.collect(self.bridge)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"audit: {exc}")
            return
        self.call_from_thread(self._show_audit, audit, sample)

    def _show_audit(self, audit, sample: Optional[str]) -> None:
        from s3k import analysis

        if sample is not None:
            where = audit.usage(sample)
            body = (
                "\n".join(f"  program {r.program} ({r.program_name})  "
                           f"keygroup {r.keygroup}  zone {r.zone}"
                           for r in where[:24])
                or "  nothing uses it")
            extra = (f"\n  … and {len(where) - 24} more" if len(where) > 24
                     else "")
            self.push_screen(ReportScreen(
                f"Uses of [b]{sample}[/b]", body + extra,
                f"{len(where)} zone(s)"))
            return

        dangling = audit.dangling()
        if dangling:
            lines = []
            for program, refs in audit.programs_playing_silence().items():
                names = sorted({r.sample for r in refs})
                lines.append(
                    f"  program {program} ({refs[0].program_name}): "
                    f"{len(refs)} silent zone(s)")
                lines.append(f"      naming {', '.join(names[:4])}"
                             + (" …" if len(names) > 4 else ""))
            body = "\n".join(lines)
        else:
            body = "  Every zone names a sample the machine holds."
        self.push_screen(ReportScreen("Integrity", body, audit.summary()))

    def action_source(self) -> None:
        """Show the load source and let it be changed."""
        # Checked BEFORE the dialog opens, matching action_load_volume. The
        # old order pushed the screen, took a keypress, closed, and then
        # reported the refusal in the status line -- which reads as "pressing
        # any button just returns without changing anything", because that is
        # exactly what it looks like.
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it before changing "
                "the load source", refused=True)
            return
        self.notify_status("reading the load source…")
        self._open_source()

    @work(thread=True)
    def _open_source(self) -> None:
        # Same reason as _open_menu: a bridge read on the UI thread stalls
        # the app behind any worker holding the lock, and reads as a dead key.
        try:
            with self._bridge_lock:
                source = self.bridge.load_source()
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"load source unavailable: {exc}",
                refused=True)
            return
        self.call_from_thread(self._show_source, source)

    def _show_source(self, source) -> None:
        self.notify_status("")
        self._source_volumes_worker()

        def closed(_result) -> None:
            # One disk re-read when the dialog closes, not one per keypress.
            # Each is seven round trips, and a user setting a drive and a
            # partition would otherwise pay for both before seeing either.
            if getattr(self, "_source_dirty", False):
                self._source_dirty = False
                self.action_disk()

        self.push_screen(
            SourceScreen(source, self.bridge.DEVICE_TYPES), closed
        )

    @work(thread=True)
    def _source_volumes_worker(self) -> None:
        """Fill the dialog's listing when it opens, not only after a change."""
        try:
            with self._bridge_lock:
                volumes = self.bridge.volume_list()
        except Exception:
            volumes = None
        self.call_from_thread(self._fill_source_volumes, volumes)

    def _fill_source_volumes(self, volumes) -> None:
        screen = self.screen_stack[-1] if self.screen_stack else None
        if isinstance(screen, SourceScreen):
            screen.update_volumes(volumes)

    def apply_source_change(self, what: str, value: int) -> None:
        """Apply one change from the open SCSI dialog.

        The dialog stays up, so this writes and then refreshes its rows from
        what the machine reports -- not from what was asked for. Three of
        these registers acknowledge an error and perform the write anyway,
        and one acknowledges OK and ignores it (§76), so the display has to
        follow the read-back.
        """
        self._source_dirty = True
        self._source_change_worker(what, value)

    @work(thread=True)
    def _source_change_worker(self, what: str, value: int) -> None:
        try:
            with self._bridge_lock:
                if what == "drive":
                    source = self.bridge.select_drive(value)
                elif what == "device":
                    source = self.bridge.select_device(value)
                else:
                    current = self.bridge.load_source()["partition"]
                    source = self.bridge.select_partition(
                        max(0, min(7, current + value)))
            try:
                with self._bridge_lock:
                    volumes = self.bridge.volume_list()
            except Exception:
                volumes = None
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"{what}: {exc}",
                                  refused=True)
            return
        self.call_from_thread(self._refresh_source_dialog, source, volumes)

    def _refresh_source_dialog(self, source, volumes=None) -> None:
        screen = self.screen_stack[-1] if self.screen_stack else None
        if isinstance(screen, SourceScreen):
            screen.update_source(source)
            screen.update_volumes(volumes)
        part = source.get("partition")
        self.notify_status(
            f"source: SCSI {source.get('scsi_drive_id')}, "
            f"{self.bridge.DEVICE_TYPES.get(source.get('device_type'), '?')}, "
            f"partition {chr(65 + part) if isinstance(part, int) else '?'}")

    @work(thread=True)
    def _select_source_worker(self, what: str, value: int) -> None:
        try:
            with self._bridge_lock:
                if what == "drive":
                    source = self.bridge.select_drive(value)
                else:
                    source = self.bridge.select_device(value)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"{what}: {exc}")
            return
        got = source.get("scsi_drive_id" if what == "drive" else "device_type")
        if got != value:
            # the machine is the authority, not the acknowledgement -- writing
            # byte[4] is acked and ignored, and writing mode 0 errors and works
            self.call_from_thread(
                self.notify_status,
                f"{what}: asked for {value}, machine reads {got}")
            return
        self.call_from_thread(self.action_disk)

    def action_boards(self) -> None:
        """Declare which expansion boards this machine has. Saved to config.

        Upper-case B deliberately: this changes what the editor will let you
        touch, and a mistyped lower-case key should not silently unfence
        fields that have crashed a sampler.
        """
        from s3k import bridge as bridge_mod

        def chosen(fitted) -> None:
            if fitted is None:
                return
            self.bridge.boards = {b.upper() for b in fitted}
            shown = ", ".join(sorted(fitted)) or "none"
            if self._config_path is None:
                self.notify_status(f"boards fitted: {shown} — this session only")
                return
            try:
                bridge_mod.save_boards(fitted, self._config_path)
            except Exception as exc:
                self.notify_status(f"boards: {shown}, but not saved ({exc})")
                return
            self.notify_status(f"boards fitted: {shown} — saved")

        self.push_screen(BoardsScreen(getattr(self.bridge, "boards", set())),
                         chosen)

    def action_menu(self) -> None:
        """Move the machine to another main-menu page."""
        # Checked BEFORE the dialog opens, matching action_load_volume. The
        # old order pushed the screen, took a keypress, closed, and then
        # reported the refusal in the status line -- which reads as "pressing
        # any button just returns without changing anything", because that is
        # exactly what it looks like.
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it before changing "
                "the main menu", refused=True)
            return
        self.notify_status("reading the current page…")
        self._open_menu()

    @work(thread=True)
    def _open_menu(self) -> None:
        # In a worker, not inline. Reading the page is a MIDI round trip, and
        # taking the bridge lock on the UI thread blocks the whole app for as
        # long as any background worker holds it -- which looks exactly like
        # the key doing nothing. Reported twice as "g Main Menu does nothing";
        # the first fix moved the write-gate check earlier and left this.
        try:
            with self._bridge_lock:
                current = self.bridge.mode()
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"main menu unavailable: {exc}",
                refused=True)
            return
        self.call_from_thread(self._show_menu, current)

    def _show_menu(self, current: int) -> None:
        self.notify_status("")
        self.push_screen(MenuScreen(current, self.bridge.MODES))

    def apply_menu_change(self, value: int) -> None:
        """Apply one page change from the open Main menu dialog.

        The dialog stays up. It used to dismiss on the first key that
        matched, so choosing the wrong page meant pressing `g` again to get
        back -- reported in live use, and the same complaint the SCSI
        screen drew before it was changed the same way. Esc is now the only
        way out of either.
        """
        self._select_mode_worker(value)

    @work(thread=True)
    def _select_mode_worker(self, value: int) -> None:
        try:
            with self._bridge_lock:
                got = self.bridge.select_mode(value)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"main menu: {exc}")
            return
        name = self.bridge.MODES.get(got, str(got))
        self.call_from_thread(
            self.notify_status,
            f"main menu: {name}" if got == value
            else f"main menu: asked for {value}, machine shows {name}")
        # The dialog is still up, so it repaints from the read-back.
        self.call_from_thread(self._refresh_menu_screen, got)

    def _refresh_menu_screen(self, current: int) -> None:
        screen = self.screen_stack[-1] if self.screen_stack else None
        if isinstance(screen, MenuScreen):
            screen.show_current(current)

    def _show_disk_pane(self, showing: bool) -> None:
        """Swap the right column between Parameters and the disk browser."""
        self._disk_showing = showing
        for widget_id in ("param-title", "parameters"):
            self.query_one(f"#{widget_id}").display = not showing
        for widget_id in ("disk-title", "volumes"):
            self.query_one(f"#{widget_id}").display = showing
        if showing:
            self.query_one("#volumes", DataTable).focus()
        else:
            self.query_one("#parameters", DataTable).focus()
        self._refresh_key_hints()

    def action_close_disk(self) -> None:
        if self._disk_showing:
            self._show_disk_pane(False)
            self.notify_status("")

    #: Extra hints for the disk browser -- keys whose meaning is specific to
    #: it, or which have no app-level binding to describe them.
    _DISK_HINTS = ("esc back", "enter select volume")

    def _refresh_key_hints(self) -> None:
        """The legend, ADDING to the bindings rather than replacing them.

        This used to swap in a hardcoded list of eight strings while the disk
        browser was open, which silently dropped thirteen keys that still
        worked -- `m` for the Master screen among them, reported from live
        use as "master is missing in the menu list". Two copies of a list is
        the shape; the copy without a compiler behind it is the one that
        rots.
        """
        blocks = [f"{b.key} {b.description}"
                  for b in self.BINDINGS if b.description and b.show]
        if self._disk_showing:
            blocks = list(self._DISK_HINTS) + blocks
        try:
            self.query_one("#keyhints", KeyHints).set_blocks(blocks)
        except Exception:
            pass

    def action_load_volume(self) -> None:
        """Load the selected volume into the machine. **This writes.**

        The trigger is the panel's LOAD softkey and only that one, and it
        APPENDS. The panel's other softkey, CLR, has no remote equivalent --
        the trigger register acts on the value 1 and stores every other value
        without doing anything (§74) -- so "clear first" here is not CLR; it
        is deleting what is resident and then loading, which is why it goes
        through the same arm-then-fire confirmation the Master screen uses.

        Not the Master screen's arm-then-fire: it adds, and what it adds can
        be deleted again. But it moves megabytes, takes seconds to minutes,
        and fails messily when the volume is larger than free memory -- so it
        confirms, and the confirmation shows whether it fits, which is the one
        thing the machine will not tell you until it has already half-loaded.
        """
        # `l` from the main view opens the browser rather than firing
        # anything: choosing WHAT to load is the first half of loading, and
        # it needs the listing in front of you. `l` again, from inside,
        # is the one that starts a load.
        if not self._disk_showing:
            self.action_disk()
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return
        if not self._disk_entries:
            self.notify_status("nothing read yet — press d")
            return

        self._open_load_options()

    @work(thread=True)
    def _open_load_options(self) -> None:
        # Read the panel's load type fresh rather than caching it: it is a
        # front-panel setting and the person may have changed it since the
        # disk was read. One round trip, ~10 ms.
        try:
            with self._bridge_lock:
                current = self.bridge.load_type()
        except Exception:
            current = None
        self.call_from_thread(self._show_load_options, current)

    def _show_load_options(self, current_type) -> None:
        def chosen(options) -> None:
            if options is None:
                return
            self._confirm_load(*options)

        item, label = self._selected_disk_item()
        # A row is selected, so default to loading just that -- which is why
        # somebody navigated to it. The whole-volume types are one `t` away.
        if item is not None and current_type not in LoadOptionsScreen.CURSOR_TYPES:
            current_type = 4 if label.startswith("prog") else 5
        self.push_screen(
            LoadOptionsScreen(resident_programs=len(self._programs),
                              load_type=current_type,
                              item=item, item_label=label), chosen)

    def _selected_disk_item(self):
        """Which directory entry the Disk pane is sitting on, if any.

        Returns ``(index, label)`` -- the index is `hd_directory`'s and is
        what `word[7]` takes (§97). A volume row or the divider is not an
        item and gives ``(None, "")``.
        """
        try:
            table = self.query_one("#volumes", DataTable)
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return None, ""
        kind, name = str(row[0]), str(row[1])
        if kind not in ("prog", "samp"):
            return None, ""
        # The pane lists volumes first, then a divider, then the entries in
        # order -- so the entry's position among the entry rows is its
        # directory index.
        seen = 0
        for at in range(table.cursor_row):
            try:
                if str(table.get_row_at(at)[0]) in ("prog", "samp"):
                    seen += 1
            except Exception:
                break
        return seen, f"{kind} {name}"

    def _confirm_load(self, clear_first: bool, renumber: bool,
                      load_type: int = 1, item: Optional[int] = None) -> None:
        """Show what the load costs, then fire it.

        The budget depends on the answer to the first question. Adding is
        measured against what is free right now, because the load appends
        (§73). Clearing first frees everything held, so the budget is the
        machine's whole memory -- measuring a clear-then-load against current
        free would refuse loads that would comfortably fit.
        """
        if item is not None and item < len(self._disk_entries):
            # One item, so the budget is that item and not the volume.
            wanted = [self._disk_entries[item]]
        else:
            wanted = self._disk_entries
        needed = sum(getattr(e, "audio_words", 0) for e in wanted)
        mb = lambda w: f"{w * 2 / 1024 / 1024:.2f} MB"
        budget = self._total_words if clear_first else self._words_free
        fits = budget is None or needed <= budget

        type_name = m.LOAD_TYPES.get(load_type, load_type)
        headline = (f"{type_name}: {len(wanted)} item(s), {mb(needed)}?"
                    if fits else
                    f"{type_name}: {len(wanted)} item(s), {mb(needed)} — "
                    f"THIS DOES NOT FIT")
        detail = ""
        if clear_first:
            detail += (
                "\n\nEVERY resident program and sample is deleted first. "
                "There is no undo, and this is not the panel's CLR — it is "
                "a delete, so one program will survive it.")
            if budget is not None:
                detail += f"\n\ntotal memory: {mb(budget)}"
        elif budget is not None:
            detail += f"\n\nfree memory: {mb(budget)}"
        if renumber:
            detail += ("\n\nAfterwards every program is renumbered in list "
                       "order, so nothing shares a MIDI program number.")
        if load_type not in (0, 1) and item is None and fits:
            detail += ("\n\nThe size above is the whole volume; this type "
                       "loads part of it, so it will use less.")
        if not fits:
            detail += ("\n\nThe machine will load what it can and stop with "
                       "'insufficient waveform memory'. Programs whose samples "
                       "did not arrive play silence.")

        def go(confirmed) -> None:
            if confirmed:
                self._load_worker(clear_first=clear_first, renumber=renumber,
                                  load_type=load_type, item=item)

        self.push_screen(ConfirmScreen(headline + detail), go)

    @work(thread=True)
    def _load_worker(self, *, clear_first: bool = False,
                     renumber: bool = False, load_type: int = 1,
                     item: Optional[int] = None) -> None:
        try:
            with self._bridge_lock:
                if clear_first:
                    self.call_from_thread(
                        self.notify_status, "clearing memory…")
                    self.bridge.clear_memory()
                self.bridge.trigger_load(load_type, item=item)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"load: {exc}")
            return
        self.call_from_thread(self._await_load, renumber)

    def _await_load(self, renumber: bool = False) -> None:
        """Wait for the person to say the load finished, then refresh.

        The alternative is polling, and a 58.7 MB load probed every eight
        seconds ran in stop-start bursts and ended sitting at BUSY until the
        machine was power cycled (§71). Whether the probing caused that is
        not established -- which is reason enough not to repeat it.

        The renumber has to happen here rather than in the load worker: it
        reads the program list, and the program list is not final until the
        load is. This dialog is the only signal that it is.
        """
        def done(_result) -> None:
            if renumber:
                self.notify_status("renumbering…")
                self._renumber_worker()
                return
            self.notify_status("re-reading after the load…")
            self._load_catalog()

        self.push_screen(LoadingScreen(), done)

    @work(thread=True)
    def _renumber_worker(self) -> None:
        try:
            with self._bridge_lock:
                result = self.bridge.renumber_programs()
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"renumber: {exc}")
            return
        message = f"renumbered {result['renumbered']} program(s)"
        if result.get("beyond_range"):
            message += (f", {result['beyond_range']} past program 128 and "
                        f"left alone")
        self.call_from_thread(self.notify_status, message)
        self._load_catalog(announce=False)

    def _show_volumes(self, volumes, entries, source=None,
                      source_error=None) -> None:
        table = self.query_one("#volumes", DataTable)
        # Selecting a volume re-reads the disk, which rebuilds this table --
        # and a rebuilt DataTable puts its cursor back on row 0. So pressing
        # Enter on v4 selected v4 and then jumped the cursor to v0, which
        # reads as the selection having been undone. Remembered by LABEL
        # rather than by row number, because the directory below the divider
        # changes length with the volume.
        was_on = None
        if table.row_count:
            try:
                was_on = str(table.get_row_at(table.cursor_row)[0])
            except Exception:
                was_on = None

        table.clear()
        selected = (source or {}).get("volume")
        for volume in volumes:
            # Which one the machine is actually pointing at. The title says
            # so too, but the mark is where the eye already is.
            here = " ◂" if volume.index == selected else ""
            table.add_row(f"v{volume.index}", f"{volume.name}{here}")
        # The rows below are a DIFFERENT list: the directory of whichever
        # volume the panel has selected, not more volumes. Concatenated with
        # no divider they read as volumes with odd numbering -- asked in live
        # use as "what are these entries below v8?".
        if entries:
            table.add_row("", "── in the selected volume ──")
        for entry in entries:
            kind = "prog" if getattr(entry, "is_program", False) else "samp"
            table.add_row(f"{kind}", entry.name)

        if was_on is not None:
            for row in range(table.row_count):
                try:
                    if str(table.get_row_at(row)[0]) == was_on:
                        table.move_cursor(row=row)
                        break
                except Exception:
                    break
        self._disk_entries = list(entries or [])
        self._disk_read = True
        try:
            # DeviceStatus calls it free_words. Asking for words_free got None
            # and fell through to a hardcoded 16 Mword machine, which is right
            # only for a fully expanded one -- so a 2 MB S3000XL was told
            # everything fit.
            status = self.bridge.status()
            self._words_free = status.free_words
            self._total_words = status.max_words
        except Exception:
            self._words_free = None
            self._total_words = None
        where = self._describe_source(source)
        loaded = f", {len(entries)} items" if entries else ""
        cost = ""
        if entries:
            words = sum(getattr(e, "audio_words", 0) for e in entries)
            if words:
                cost = f", {words * 2 / 1024 / 1024:.1f} MB"
        head = f"Disk — {where}" if where else "Disk"
        self.query_one("#disk-title", Static).update(
            f"{head} — {len(volumes)} vol{loaded}{cost}"
            if volumes or entries else f"{head} — empty"
        )
        note = f"  (load source unavailable: {source_error})" if source_error else ""
        # Neither of these may say a volume is a front-panel job: it stopped
        # being one in §96, and the first of them was still saying so a day
        # later, in the status line of the very screen that selects volumes.
        if entries:
            self.notify_status(
                f"{len(volumes)} volume(s); {len(entries)} item(s) in the "
                f"selected volume. Enter selects a volume; l loads; "
                f"[ and ] step the partition.{note}"
            )
        else:
            self.notify_status(
                f"{len(volumes)} volume(s), and the selected one reads as "
                f"empty. Enter on a volume row selects it.{note}"
            )

    def action_toggle_write(self) -> None:
        self.allow_write = not self.allow_write
        self._refresh_write_badge()
        self.notify_status(
            "write gate ARMED — edits will reach the device"
            if self.allow_write
            else "write gate locked"
        )

    def _selected_program(self) -> Optional[int]:
        table = self.query_one("#programs", DataTable)
        if not table.row_count:
            return None
        return table.cursor_row

    def _selected_param(self) -> Optional[p.Parameter]:
        table = self.query_one("#parameters", DataTable)
        if not table.row_count or table.cursor_row >= len(self._param_rows):
            return None
        return self._param_rows[table.cursor_row]

    def action_edit(self) -> None:
        param = self._selected_param()
        if param is None:
            self.notify_status("no parameter selected")
            return
        if not param.writable:
            why = "read-only" if param.readonly else "an internal block address"
            self.notify_status(f"{param.name} is {why}", refused=True)
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return
        current = self._param_values.get(param.name)

        def apply(value: Optional[str]) -> None:
            if value is not None:
                self._write_param(param, current, value)

        self.push_screen(EditValueScreen(param, current), apply)

    def action_nudge_up(self) -> None:
        self._nudge(+1)

    def action_nudge_down(self) -> None:
        self._nudge(-1)

    def _nudge(self, delta: int) -> None:
        """Step the selected parameter by one. **This writes.**

        Held down, the terminal's own key repeat drives it, so this has to be
        cheap: an ordinary edit ends by re-reading the whole catalog, which is
        two list requests and is nowhere near fast enough to sit under a
        repeating key. A nudge cannot change a name -- it is refused on text
        and array fields -- so the catalog cannot have gone stale and the
        re-read is skipped.

        Consecutive nudges of one field **collapse into a single undo entry**,
        keeping the value the run started from. Ten taps of `+` should be one
        thing to undo, not ten; the sibling eosed does the same.
        """
        param = self._selected_param()
        if param is None:
            self.notify_status("no parameter selected")
            return
        if not param.writable:
            why = "read-only" if param.readonly else "an internal block address"
            self.notify_status(f"{param.name} is {why}", refused=True)
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return
        if param.kind == "text" or param.is_array:
            self.notify_status(
                f"{param.name} is not a number — use e", refused=True)
            return
        current = self._param_values.get(param.name)
        if not isinstance(current, int):
            self.notify_status(f"{param.name} has no value to step yet")
            return

        value = current + delta
        if not param.minimum <= value <= param.maximum:
            self.notify_status(
                f"{param.name} is at its "
                f"{'maximum' if delta > 0 else 'minimum'} "
                f"({p.describe_value(param, current)})", refused=True)
            return

        region, index, keygroup = self._param_context
        self._nudging = (region, index, keygroup, param.name)
        self._write_param_worker(param, index, value, current, keygroup)

    @work(thread=True)
    def _write_param_worker(
        self, param: p.Parameter, index: int, value, old, keygroup: int = 0,
        record: bool = True,
    ) -> None:
        try:
            with self._bridge_lock:
                self.bridge.set_parameter(param, index, value,
                                          keygroup=keygroup)
                header = self.bridge.get_header(param.region, index,
                                                keygroup=keygroup)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        self.call_from_thread(self._after_write, param, index, old, value,
                              header, keygroup, record)

    def _write_param(self, param: p.Parameter, current, raw: str) -> None:
        # Through the pane's own context, not the selected program. The two
        # were the same while the pane only ever showed program fields;
        # they are not now, and using the program index for a keygroup or
        # sample field would write to a different structure at the same
        # offset without any error.
        region, index, keygroup = self._param_context
        if param.region != region:
            self.notify_status(
                f"{param.name} is a {param.region} field but the pane is "
                f"showing {region} — refusing rather than guessing", refused=True)
            return
        if index is None:
            self.notify_status("nothing selected")
            return
        if param.is_array:
            # One value per element, comma-separated. Without this the field
            # is only reachable through an error message: int() rejects the
            # list and a bare number is refused by encode_field, which is
            # correct but leaves TEMPER uneditable.
            parts = [x for x in raw.replace(" ", ",").split(",") if x]
            if len(parts) != param.elements:
                self.notify_status(
                    f"{param.name} needs {param.elements} values, got "
                    f"{len(parts)}"
                )
                return
            try:
                value = [int(x, 0) for x in parts]
            except ValueError:
                self.notify_status(f"{raw!r} is not {param.elements} numbers")
                return
        elif param.kind == "text":
            value = raw
        else:
            try:
                value = int(raw, 0)
            except ValueError:
                self.notify_status(f"{raw!r} is not a number")
                return
        self._write_param_worker(param, index, value, current, keygroup)

    def _after_write(
        self,
        param: p.Parameter,
        index: int,
        old,
        new,
        header: Dict[str, object],
        keygroup: int = 0,
        record: bool = True,
    ) -> None:
        # `record` is False when the write IS an undo. Without it the undo
        # appends its own reversal to the log, so the log never drains:
        # pressing z twice undid and then REDID, and Z replayed a stack of
        # undo-entries instead of the edits. Found on hardware; every
        # synthetic test passed, because they never pressed z and then looked
        # at the log.
        #
        # The KEYGROUP is the one the write actually went to, not 0. This
        # recorded a hardcoded zero until 2026-08-15, so undoing an edit made
        # on keygroup 3 put the old value into keygroup 0.
        nudge = self._nudging
        self._nudging = None
        if record and nudge and self._undo:
            last = self._undo[-1]
            same = (last.region, last.index, last.keygroup, last.name)
            if same == nudge and last.new == old:
                # Extend the run rather than logging every tap: the entry
                # keeps the value the run STARTED from, so one undo puts the
                # whole run back.
                self._undo[-1] = _Change(
                    region=last.region, index=last.index,
                    keygroup=last.keygroup, name=last.name,
                    old=last.old, new=new)
                record = False
        if record:
            self._undo.append(
                _Change(
                    region=param.region,
                    index=index,
                    keygroup=keygroup,
                    name=param.name,
                    old=old,
                    new=new,
                )
            )
        # Blunt invalidation: any write could have changed a name, and a name
        # change moves what the catalog says. Cheaper to re-read than to
        # reason about which caches a given write could not have touched.
        # WITH the index and keygroup. Defaulting them reset _param_context
        # to (region, 0, 0) after every write, so a second edit on keygroup 3
        # silently landed on keygroup 0 -- the same silent wrong-target write
        # the log bug produced, by a different route.
        self._show_params(param.region, header, index, keygroup)
        self._refresh_write_badge()
        self.notify_status(f"{param.name} = {p.describe_value(param, new)}")
        if nudge is None:
            # A nudge cannot rename anything -- text and array fields are
            # refused -- so the catalog cannot be stale, and re-reading it
            # under a repeating key would make the whole thing unusable.
            self._load_catalog(announce=False)

    def action_undo(self) -> None:
        if not self._undo:
            self.notify_status("nothing to undo")
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — undo is a write", refused=True)
            return
        change = self._undo.pop()
        param = p.lookup((change.region, change.name))
        self._write_param_worker(param, change.index, change.old,
                                 change.new, change.keygroup, record=False)
        self._refresh_write_badge()

    def action_undo_all(self) -> None:
        """Put everything back, newest first. **This writes, repeatedly.**

        The sibling eosed offers `Z` alongside `z` and this did not. Replayed
        backwards because two edits to the same field must land in reverse
        order or the older value wins.
        """
        if not self._undo:
            self.notify_status("nothing to undo")
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — undo is a write", refused=True)
            return
        self.notify_status(f"undoing {len(self._undo)} change(s)…")
        self._undo_all_worker()

    @work(thread=True)
    def _undo_all_worker(self) -> None:
        done, failed = 0, None
        while self._undo:
            change = self._undo[-1]
            param = p.lookup((change.region, change.name))
            try:
                with self._bridge_lock:
                    self.bridge.set_parameter(param, change.index, change.old,
                                              keygroup=change.keygroup)
            except Exception as exc:
                failed = f"{change.name}: {exc}"
                break
            # Popped only after the write lands, so a failure part-way leaves
            # the rest of the log intact and retryable rather than discarded.
            self._undo.pop()
            done += 1
        message = f"undid {done} change(s)"
        if failed:
            message += f"; stopped at {failed}"
        self.call_from_thread(self.notify_status, message)
        self.call_from_thread(self._refresh_write_badge)
        self._load_catalog(announce=False)

    def action_history(self) -> None:
        """Every write this session, with where it went.

        Region, index and keygroup are shown as their own column for the
        reason eosed shows scope: the same parameter name at two different
        keygroups is two genuinely different fields, and a list that omits
        that is a list of things you cannot tell apart.
        """
        if not self._undo:
            self.push_screen(ReportScreen(
                "Change history",
                "  nothing written this session.",
                "Edits are logged here with the value they replaced."))
            return
        lines = [f"  {'#':>3}  {'where':<22}  {'parameter':<10}  "
                 f"{'old':>12}  {'new':>12}", "  " + "-" * 68]
        for number, change in enumerate(self._undo, 1):
            where = f"{change.region} {change.index}"
            if change.region == "keygroup":
                where += f" kg {change.keygroup}"
            param = p.lookup((change.region, change.name))
            lines.append(
                f"  {number:>3}  {where:<22}  {change.name:<10}  "
                f"{p.describe_value(param, change.old):>12}  "
                f"{p.describe_value(param, change.new):>12}")
        self.push_screen(ReportScreen(
            "Change history",
            "\n".join(lines),
            f"{len(self._undo)} change(s) — z undoes the last, Z undoes all"))

    def action_master(self) -> None:
        index = self._selected_program()
        context = (
            f"program {index}: {self._programs[index]}"
            if index is not None and index < len(self._programs)
            else "no program selected"
        )

        def chosen(action: Optional[str]) -> None:
            if action == "clear_memory":
                self._confirm_clear()
            elif action:
                self._confirm_destructive(action, index)

        self.push_screen(MasterScreen(context), chosen)

    def _confirm_clear(self) -> None:
        """The remote half of the panel's CLR: delete everything resident.

        CLR itself cannot be reached -- it is a panel chain with its own
        on-screen prompt (§74). This is the same effect built out of DELS and
        DELP, which is why it lives behind the same arm-then-fire as they do
        rather than next to the load.
        """
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return
        held = (self._total_words - self._words_free
                if self._total_words and self._words_free is not None else None)
        detail = (f"\n\n{held * 2 / 1024 / 1024:.2f} MB resident"
                  if held is not None else "")

        def go(confirmed: bool) -> None:
            if confirmed:
                self._clear_worker()

        self.push_screen(
            ConfirmScreen(
                f"Delete [b]every resident program and sample[/b]?{detail}"
                "\n\nThere is no undo. One program always survives — the "
                "machine refuses to delete the last one."
            ),
            go,
        )

    @work(thread=True)
    def _clear_worker(self) -> None:
        try:
            with self._bridge_lock:
                result = self.bridge.clear_memory()
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"clear: {exc}")
            return
        self.call_from_thread(
            self.notify_status,
            f"cleared {result['samples']} sample(s) and "
            f"{result['programs']} program(s); "
            f"{result['programs_left']} program(s) left")
        self.call_from_thread(self.action_refresh)

    def _confirm_destructive(self, action: str, index: Optional[int]) -> None:
        if index is None:
            self.notify_status("nothing selected")
            return
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it", refused=True)
            return

        if action == "delete_keygroup":
            target = self.query_one("#keygroups", DataTable).cursor_row
            if not self._keygroups:
                self.notify_status("this program has no keygroups")
                return
            what = f"keygroup {target} of program {index}"
        elif action == "delete_sample":
            target = self.query_one("#samples", DataTable).cursor_row
            if not self._samples:
                self.notify_status("no samples")
                return
            what = f"sample {target} ({self._samples[target]})"
        else:
            target = index
            what = f"program {index} ({self._programs[index]}) and its keygroups"

        def go(confirmed: bool) -> None:
            if confirmed:
                self._destructive_worker(action, index, target)

        self.push_screen(
            ConfirmScreen(
                f"Delete [b]{what}[/b]?\n\n"
                "This cannot be undone. The device will not ask again, and "
                "nothing in this project has been verified against hardware."
            ),
            go,
        )

    @work(thread=True)
    def _destructive_worker(self, action: str, program: int, target: int) -> None:
        try:
            with self._bridge_lock:
                if action == "delete_program":
                    self.bridge.delete_program(target)
                elif action == "delete_keygroup":
                    self.bridge.delete_keygroup(program, target)
                else:
                    self.bridge.delete_sample(target)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        # The undo log describes parameter writes; a delete cannot be replayed
        # backwards, so the log is dropped rather than left implying it could.
        self.call_from_thread(self._after_destructive, f"{action} on {target} done")

    def _after_destructive(self, message: str) -> None:
        self._undo.clear()
        self._refresh_write_badge()
        self.notify_status(message)
        self._load_catalog()

    # -- events -------------------------------------------------------------

    def _select_volume_row(self, event) -> None:
        """Enter on a volume row selects it. **This writes.**

        It used to say a volume was a front-panel job, because §72 said so.
        The register was there all along under another name (§96). Rows below
        the divider are the selected volume's directory, not volumes, and
        selecting one of those means nothing.
        """
        table = event.data_table
        try:
            label = str(table.get_row_at(event.cursor_row)[0])
        except Exception:
            return
        if not label.startswith("v"):
            return          # a directory row, or the divider
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it before changing "
                "the volume", refused=True)
            return
        try:
            index = int(label[1:])
        except ValueError:
            return
        self.notify_status(f"selecting volume {index + 1:03d}…")
        self._select_volume_worker(index)

    @work(thread=True)
    def _select_volume_worker(self, index: int) -> None:
        try:
            with self._bridge_lock:
                self.bridge.select_volume(index)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"volume: {exc}", refused=True)
            return
        # The directory now describes a different volume, so re-read it.
        self.call_from_thread(self._read_disk_worker)

    def on_descendant_focus(self, event) -> None:
        """Moving focus to a pane shows that pane's parameters.

        Without this, tabbing to the keygroup list left the parameter pane
        describing the program until the cursor happened to move -- and a
        cursor moved to the row it already occupies fires nothing, so
        arriving on a single-keygroup program showed the wrong fields
        indefinitely.
        """
        table = getattr(event, "widget", None)
        table_id = getattr(table, "id", None)
        if table_id == "keygroups":
            self._load_keygroup(table.cursor_row)
        elif table_id == "samples":
            self._load_sample_row(table.cursor_row)
        elif table_id == "programs":
            row = table.cursor_row
            if row is not None:
                self._load_program(row)

    def on_data_table_row_highlighted(self, event) -> None:
        """Follow the cursor: the parameter pane shows whatever is selected.

        **Only when the table has focus.** Filling the keygroup pane after a
        program loads moves its cursor too, and reacting to that would swap
        the parameters to a keygroup the moment a program was chosen -- the
        opposite of what selecting a program means.
        """
        table = event.data_table
        if table.id == "programs":
            if self._refilling or event.cursor_row == self._loaded_program:
                return          # our own repopulation, not the user moving
            self._load_program(event.cursor_row)
        elif not table.has_focus:
            return
        elif table.id == "keygroups":
            self._load_keygroup(event.cursor_row)
        elif table.id == "samples":
            self._load_sample_row(event.cursor_row)

    def _load_keygroup(self, keygroup: int) -> None:
        program = self._selected_program()
        if program is None or keygroup is None:
            return
        if keygroup >= self._keygroups:
            return
        self._load_keygroup_worker(program, keygroup)

    @work(thread=True)
    def _load_keygroup_worker(self, program: int, keygroup: int) -> None:
        try:
            with self._bridge_lock:
                header = self.bridge.get_header("keygroup", program,
                                                keygroup=keygroup)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"keygroup: {exc}")
            return
        self.call_from_thread(self._apply_keygroup, program, keygroup, header)

    def _apply_keygroup(self, program: int, keygroup: int, header) -> None:
        self.query_one("#param-title", Static).update(
            f"Parameters — program {program} keygroup {keygroup}")
        self._show_params("keygroup", header, program, keygroup)

    def _load_sample_row(self, row: Optional[int]) -> None:
        """Show the selected sample's own header.

        The pane lists the program's samples by NAME, so the machine's index
        has to be looked up. Names are not unique -- the sampler enforces no
        uniqueness and two resident samples can share one (§80) -- so this
        takes the first match and says so rather than pretending the choice
        was determined.
        """
        table = self.query_one("#samples", DataTable)
        if row is None or row >= table.row_count:
            return
        name = str(table.get_row_at(row)[0]).strip()
        matches = [i for i, n in enumerate(self._samples) if n.strip() == name]
        if not matches:
            self.notify_status(f"{name} is not resident — nothing to show")
            return
        if len(matches) > 1:
            self.notify_status(
                f"{len(matches)} resident samples are named {name!r}; "
                f"showing the first")
        self._load_sample_worker(matches[0], name)

    @work(thread=True)
    def _load_sample_worker(self, index: int, name: str) -> None:
        try:
            with self._bridge_lock:
                header = self.bridge.get_header("sample", index)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"sample: {exc}")
            return
        self.call_from_thread(self._apply_sample, index, name, header)

    def _apply_sample(self, index: int, name: str, header) -> None:
        self.query_one("#param-title", Static).update(
            f"Parameters — sample {index} ({name})")
        self._show_params("sample", header, index)

    def on_data_table_row_selected(self, event) -> None:
        # Enter on the parameters table means "edit this one".
        if event.data_table.id == "parameters":
            self.action_edit()
        elif event.data_table.id == "volumes":
            self._select_volume_row(event)
        elif event.data_table.id == "programs":
            self._activate_program_row(event)

    def _activate_program_row(self, event) -> None:
        """Enter on a program row makes it the active one. **This writes.**

        It selects a NUMBER, not a program: the machine's `PROGRAM NUMBER`
        register takes the row's `PRGNUM`, and every resident program sharing
        that number becomes active and sounds with it (§91). That is not a
        limitation of this code -- it is what the field means -- so when it
        happens the status line says how many will sound.
        """
        if not self.allow_write:
            self.notify_status(
                "write gate is locked — press w to arm it before changing "
                "the active program", refused=True)
            return
        row = event.cursor_row
        if not 0 <= row < len(self._programs):
            return
        self._activate_program_worker(row)

    @work(thread=True)
    def _activate_program_worker(self, index: int) -> None:
        try:
            with self._bridge_lock:
                number = self.bridge.get_header_bytes(
                    "program", index, p.lookup(("program", "PRGNUM")).offset, 1
                )[0]
                self.bridge.select_program_number(number)
                sharing = self.bridge.program_numbers().count(number)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"active program: {exc}", refused=True)
            return
        # 1-based, because that is what the machine's own display says.
        message = f"active program number {number + 1}"
        if sharing > 1:
            message += (f" — {sharing} programs share it and will all sound "
                        f"(press i, or renumber)")
        self.call_from_thread(self.notify_status, message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3ked",
        description="Terminal editor for the Akai S1000/S3000 sampler family.",
    )
    parser.add_argument("--port", help="MIDI port name (default: autodetect)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run against the built-in demo sampler; opens no MIDI ports",
    )
    parser.add_argument("--exclusive-channel", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="start with the write gate armed (default: locked)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Before any port is opened: SIGTERM otherwise ends the process where it
    # stands, leaving the port open and the sampler composing an answer
    # nobody will read. See s3k.bridge.install_clean_exit.
    if not args.demo:
        from s3k import bridge as _b

        _b.install_clean_exit()

    if args.demo:
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
    else:
        from s3k import bridge as b

        kwargs = {}
        if args.timeout is not None:
            kwargs["timeout"] = args.timeout
        try:
            if args.port:
                bridge = b.S3kBridge.standard(
                    args.port,
                    exclusive_channel=args.exclusive_channel or 0,
                    **kwargs,
                )
            else:
                channel = args.exclusive_channel
                bridge = b.S3kBridge.autodetect(
                    channels=(channel,) if channel is not None else (0,),
                    config_path=args.config or b.DEFAULT_CONFIG_PATH,
                )
        except Exception as exc:
            sys.exit(f"error: {exc}")

    # --demo gets no config path: a demo must never write a user's settings.
    app = S3kedApp(
        bridge, allow_write=args.allow_write,
        config_path=None if args.demo
        else (args.config or b.DEFAULT_CONFIG_PATH))
    try:
        app.run()
    finally:
        _close_when_idle(app, bridge)
    return 0


#: How long shutdown waits for an in-flight SysEx exchange to finish.
#: Generous: a whole-header read is ~128 ms and a directory walk is one
#: request per entry, so a worker mid-listing needs seconds, not milliseconds.
SHUTDOWN_GRACE = 8.0


def _close_when_idle(app, bridge, grace: float = SHUTDOWN_GRACE) -> None:
    """Close the bridge, but not while a worker is mid-exchange.

    **Closing the port tidily is not enough, and §95 said it was.** That
    section fixed SIGTERM ending the process where it stood, and reasoned
    that raising from the handler was safe because Python delivers signals
    between bytecodes, so the frame on the wire is whole -- "only the
    conversation is abandoned".

    An abandoned conversation IS the wedge. The application's bridge calls
    run in worker threads; a signal arriving on the main thread unwinds it
    and closes the port underneath a worker that has sent a request and is
    waiting for the answer. The sampler is left composing a reply for a
    listener that has gone, which is exactly the state §95 described and
    then failed to prevent -- demonstrated by wedging an S3000XL a second
    time with §95's fix in place, by SIGTERMing a TUI six seconds old that
    was still doing its startup catalog read.

    So this takes the same lock every bridge call takes. A worker mid-request
    holds it until its reply arrives; shutdown waits for that and then
    closes. The wait is bounded, because a hung worker must not hold the
    process open forever -- but a bounded wait that usually succeeds is a
    great deal better than no wait at all.
    """
    lock = getattr(app, "_bridge_lock", None)
    acquired = False
    if lock is not None:
        acquired = lock.acquire(timeout=grace)
    try:
        bridge.close()
    finally:
        if acquired:
            lock.release()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
