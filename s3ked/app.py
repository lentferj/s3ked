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


class SourceScreen(ModalScreen[Optional[Tuple[str, int]]]):
    """Pick what the LOAD page points at: SCSI device, media, partition.

    Every row here is a miscellaneous-data byte found by changing it on the
    front panel and seeing which one moved -- the specification documents the
    addressing and not the meanings. The volume is listed and cannot be set,
    which is not an omission: there is no volume register. `byte[49]` reads
    like one because it carries whatever field the cursor sits on, and writing
    it moves nothing (§72).
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, source: Dict[str, int],
                 device_types: Dict[int, str]) -> None:
        super().__init__()
        self.source = source or {}
        # passed in rather than imported: app.py must not import s3k.bridge,
        # which pulls in rtmidi, or --demo stops working without it
        self.device_types = device_types

    def compose(self) -> ComposeResult:
        src = self.source
        drive = src.get("scsi_drive_id", "?")
        kind = src.get("device_type")
        part = src.get("partition")
        kind_name = self.device_types.get(kind, f"? ({kind})")
        with Vertical(id="source-box"):
            yield Label("[b]Load source[/b]", id="source-title")
            yield Label(f"  SCSI drive      [b]{drive}[/b]"
                        "        press [b]0[/b]-[b]7[/b]")
            yield Label(f"  Device          [b]{kind_name}[/b]"
                        "     [b]f[/b] floppy   [b]h[/b] hard   [b]x[/b] flash")
            yield Label("  Partition       "
                        f"[b]{chr(65 + part) if isinstance(part, int) else '?'}[/b]"
                        "        [b][[/b] and [b]][/b], or here too")
            yield Label("  Volume          [dim]panel only — no register "
                        "exists for it[/dim]")
            yield Label("")
            yield Label("[dim]Each of these writes to the machine and "
                        "re-reads the directory.[/dim]")
            yield Label("[b]Esc[/b] close")

    def on_key(self, event) -> None:
        key = event.key
        if key in "01234567":
            self.dismiss(("drive", int(key)))
        elif key in ("f", "h", "x"):
            self.dismiss(("device", {"f": 0, "h": 1, "x": 2}[key]))
        elif key in ("[", "]"):
            self.dismiss(("partition", -1 if key == "[" else +1))
        else:
            return
        event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Every key, generated from the app's own BINDINGS.

    Exists because the footer cannot show them. Textual's Footer truncates
    rather than reflowing, and at 80x24 it displayed six of thirteen -- so the
    disk, load, menu, boards and audit keys were unreachable unless you had
    read the README.

    Generated rather than written out, because a hand-kept list of keys is
    the thing this project has already found stale twice in sibling code.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, bindings) -> None:
        super().__init__()
        self.rows = [(b.key, b.description) for b in bindings
                     if b.description and b.action != "help"]

    def compose(self) -> ComposeResult:
        pretty = {"question_mark": "?", "tab": "tab", "escape": "esc"}
        with Vertical(id="help-box"):
            yield Label("[b]Keys[/b]", id="help-title")
            # Scrollable because the list is longer than the smallest
            # supported terminal is tall -- the same 80x24 that made this
            # screen necessary in the first place.
            with VerticalScroll(id="help-scroll"):
                for key, description in self.rows:
                    shown = pretty.get(key, key)
                    yield Label(f"  [b]{shown:<5}[/b] {description}")
            yield Label("")
            yield Label("[dim]The footer shows only what fits; this is all of "
                        "them.[/dim]")
            yield Label("[b]esc[/b] close")

    def action_close(self) -> None:
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
    """Jump the machine to one of its eight main-menu pages.

    This is not button injection -- there is no keypress message anywhere in
    this protocol. The current page is a variable, `byte[91]`, and writing it
    moves the machine.

    Three of the eight values are named. The rest were never observed, because
    naming one requires somebody at the machine to read the display while the
    probe runs, and the enumeration has gaps that rule out guessing: GLOBAL is
    the second button of the second row and reads 8, where its position would
    make it 5.
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
            yield Label(f"  now showing: [b]{here}[/b]")
            yield Label("")
            for key, (value, name) in self._CHOICES.items():
                yield Label(f"  [b]{key}[/b]  {name}  [dim]({value})[/dim]")
            yield Label("")
            yield Label("[dim]EDIT is a modifier, not a page: eight buttons, "
                        "seven modes, and EDIT\n  combines with four of them "
                        "— which is the eleven the manual counts.[/dim]")
            yield Label("[b]Esc[/b] close")

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
        if event.key in self._CHOICES:
            self.dismiss(self._CHOICES[event.key][0])
            event.stop()

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
    #status { height: 1; padding: 0 1; }
    /* Red, not the accent colour. The armed gate is the one state where a
       keypress reaches the hardware, and $accent is also used for ordinary
       emphasis elsewhere -- a warning that looks like decoration is not a
       warning. $error is the theme's red and stays red across themes. */
    Header.-write-armed { background: $error; color: $text; text-style: bold; }
    #confirm-box, #master-box, #edit-box {
        width: 70; padding: 1 2; border: thick $panel; background: $surface;
    }
    #edit-desc, #edit-note { color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Reload"),
        # Not Enter: a focused DataTable consumes it for row selection, so an
        # app-level Enter binding never fires. Enter still works, via
        # on_data_table_row_selected below.
        Binding("e", "edit", "Edit"),
        Binding("w", "toggle_write", "Gate"),
        Binding("z", "undo", "Undo"),
        Binding("m", "master", "Master"),
        # Everything below is hidden from the footer and listed by `?`.
        #
        # Textual's Footer does not reflow: at 80x24 -- the smallest size this
        # project claims to support -- it rendered six of thirteen bindings and
        # silently truncated the rest, so `d`, `l`, `s`, `g`, `B`, `i` and `u`
        # were undiscoverable. Neither `height: auto` nor a grid layout fixes
        # it; the widget simply cuts. Thirteen entries cannot fit in eighty
        # columns at any sensible wording, so the footer shows the handful a
        # user reaches for constantly and `?` shows all of them.
        Binding("question_mark", "help", "Keys"),
        Binding("d", "disk", "Read disk", show=False),
        Binding("[", "partition_prev", "Prev partition", show=False),
        Binding("]", "partition_next", "Next partition", show=False),
        Binding("l", "load_volume", "Load volume", show=False),
        Binding("s", "source", "Load source", show=False),
        Binding("g", "menu", "Main menu", show=False),
        Binding("B", "boards", "Boards fitted", show=False),
        Binding("i", "integrity", "Integrity", show=False),
        Binding("u", "usage", "Who uses", show=False),
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
        self._disk_entries: List[object] = []
        self._words_free: Optional[int] = None
        self._total_words: Optional[int] = None
        self._param_values: Dict[str, object] = {}
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
                yield Static("Samples", classes="pane-title")
                yield DataTable(id="samples", cursor_type="row")
                yield Static("Disk", classes="pane-title", id="disk-title")
                yield DataTable(id="volumes", cursor_type="row")
            with Vertical(id="right"):
                yield Static("Parameters", classes="pane-title", id="param-title")
                yield DataTable(id="parameters", cursor_type="row")
        yield Static("", id="status")
        # Not Footer(): it is one line and truncates. See KeyHints.
        yield KeyHints(
            [f"{b.key} {b.description}"
             for b in self.BINDINGS if b.description and b.show],
            id="keyhints")

    def on_mount(self) -> None:
        self.title = "s3ked"
        self.sub_title = self.bridge.description
        for table_id, columns in (
            ("programs", ("num", "name")),
            ("keygroups", ("kg", "range")),
            ("samples", ("num", "name")),
            ("volumes", ("vol", "name")),
            ("parameters", ("off", "name", "value")),
        ):
            table = self.query_one(f"#{table_id}", DataTable)
            table.add_columns(*columns)
        self._refresh_write_badge()
        self.action_refresh()

    # -- status / badges ----------------------------------------------------

    def notify_status(self, message: str) -> None:
        self.last_status = message
        try:
            self.query_one("#status", Static).update(message)
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
        table.clear()
        for index, name in enumerate(programs):
            table.add_row(str(index), name)
        table = self.query_one("#samples", DataTable)
        table.clear()
        for index, name in enumerate(samples):
            table.add_row(str(index), name)
        # A refresh triggered by a write must not clobber that write's
        # confirmation -- the catalog reload finishes last, so without this
        # the user only ever sees the program count.
        if announce:
            self.notify_status(
                f"{len(programs)} program(s), {len(samples)} sample(s)"
            )
        if programs:
            self._load_program(0)

    @work(thread=True)
    def _load_program_worker(self, index: int) -> None:
        try:
            with self._bridge_lock:
                header = self.bridge.get_header("program", index)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        self.call_from_thread(self._apply_program, index, header)

    def _load_program(self, index: int) -> None:
        self._load_program_worker(index)

    def _apply_program(self, index: int, header: Dict[str, object]) -> None:
        self._keygroups = int(header.get("GROUPS", 0) or 0)
        table = self.query_one("#keygroups", DataTable)
        table.clear()
        for kg in range(self._keygroups):
            table.add_row(str(kg), "-")
        self.query_one("#param-title", Static).update(
            f"Parameters — program {index} ({header.get('PRNAME', '')})"
        )
        self._show_params("program", header)

    def _show_params(self, region: str, values: Dict[str, object]) -> None:
        self._param_values = values
        self._param_rows = p.region_params(region)
        table = self.query_one("#parameters", DataTable)
        table.clear()
        for param in self._param_rows:
            table.add_row(
                str(param.offset),
                param.name,
                p.describe_value(param, values.get(param.name)),
            )

    # -- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        self.notify_status("reading catalog...")
        self._load_catalog()

    def action_disk(self) -> None:
        """Read the volume list off the attached disk.

        Deliberately not part of the startup catalog. It is 7 round trips and
        about 1.2 s for a full disk, and a machine with no disk attached would
        make that a failure on every launch rather than on request.
        """
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
        self.call_from_thread(self._show_volumes, volumes, entries, source,
                              source_error)

    @staticmethod
    def _describe_source(source) -> str:
        """The LOAD page as the panel writes it: HARD-:C vol 001."""
        if not source:
            return ""
        device = {0: "FLOPPY", 1: "HARD", 2: "FLASH"}.get(
            source.get("device_type"), f"DEV{source.get('device_type')}")
        letter = chr(65 + source.get("partition", 0))
        return (f"{device}-:{letter} vol {source.get('volume', 0):03d}"
                f"  (SCSI {source.get('scsi_drive_id')})")

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
            self.notify_status("write gate is locked — press w to arm it")
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
        """Every zone naming the selected sample."""
        table = self.query_one("#samples", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._samples):
            self.notify_status("select a sample first")
            return
        name = self._samples[row]
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
        try:
            with self._bridge_lock:
                source = self.bridge.load_source()
        except Exception as exc:
            self.notify_status(f"load source unavailable: {exc}")
            return

        def chosen(result) -> None:
            if result is None:
                return
            what, value = result
            if not self.allow_write:
                self.notify_status("write gate is locked — press w to arm it")
                return
            if what == "partition":
                self._step_partition_worker(value)
            else:
                self._select_source_worker(what, value)

        self.push_screen(
            SourceScreen(source, self.bridge.DEVICE_TYPES), chosen
        )

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

    def action_help(self) -> None:
        """Show every key, since the footer can only show some of them."""
        self.push_screen(HelpScreen(self.BINDINGS))

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
        try:
            with self._bridge_lock:
                current = self.bridge.mode()
        except Exception as exc:
            self.notify_status(f"main menu unavailable: {exc}")
            return

        def chosen(value: Optional[int]) -> None:
            if value is None:
                return
            if not self.allow_write:
                self.notify_status("write gate is locked — press w to arm it")
                return
            self._select_mode_worker(value)

        self.push_screen(MenuScreen(current, self.bridge.MODES), chosen)

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

    def action_load_volume(self) -> None:
        """Load the selected volume into the machine. **This writes.**

        This is the panel's LOAD softkey and only that one. It APPENDS to
        what is already resident; the panel's other softkey, CLR, erases
        memory first and **has no remote equivalent** -- the trigger register
        acts on the value 1 and stores every other value without doing
        anything (§74). To start from empty, clear at the panel or delete
        what is resident.

        Not the Master screen's arm-then-fire: it adds, and what it adds can
        be deleted again. But it moves megabytes, takes seconds to minutes,
        and fails messily when the volume is larger than free memory -- so it
        confirms, and the confirmation shows whether it fits, which is the one
        thing the machine will not tell you until it has already half-loaded.
        """
        if not self.allow_write:
            self.notify_status("write gate is locked — press w to arm it")
            return
        if not self._disk_entries:
            self.notify_status("no volume read yet — press d first")
            return

        needed = sum(getattr(e, "audio_words", 0) for e in self._disk_entries)
        free = self._words_free
        mb = lambda w: f"{w * 2 / 1024 / 1024:.2f} MB"
        # the load appends, so the budget is what is free right now and not
        # the size of the machine
        fits = free is None or needed <= free
        headline = (f"Load {len(self._disk_entries)} item(s), {mb(needed)}?"
                    if fits else
                    f"Load {len(self._disk_entries)} item(s), {mb(needed)} — "
                    f"THIS DOES NOT FIT")
        detail = (f"\n\nfree memory: {mb(free)}" if free is not None else "")
        if not fits:
            detail += ("\n\nThe machine will load what it can and stop with "
                       "'insufficient waveform memory'. Programs whose samples "
                       "did not arrive play silence.")

        def go(confirmed) -> None:
            if confirmed:
                self._load_worker()

        self.push_screen(ConfirmScreen(headline + detail), go)

    @work(thread=True)
    def _load_worker(self, load_type: int = 1) -> None:
        try:
            with self._bridge_lock:
                self.bridge.trigger_load(load_type)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"load: {exc}")
            return
        self.call_from_thread(
            self.notify_status,
            "load started. The machine is busy — nothing is being sent to it. "
            "Press r when the display settles.")

    def _show_volumes(self, volumes, entries, source=None,
                      source_error=None) -> None:
        table = self.query_one("#volumes", DataTable)
        table.clear()
        for volume in volumes:
            table.add_row(f"v{volume.index}", volume.name)
        for entry in entries:
            table.add_row(f"  {entry.index}", entry.name)
        self._disk_entries = list(entries or [])
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
        if entries:
            self.notify_status(
                f"{len(volumes)} volume(s); {len(entries)} item(s) in the "
                f"loaded volume. Press l to load it; [ and ] step the "
                f"partition. Choosing a volume is still a panel job.{note}"
            )
        else:
            self.notify_status(
                f"{len(volumes)} volume(s). Nothing loaded, so the directory "
                f"is empty; select a volume at the panel, then d to read "
                f"it and l to load it.{note}"
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
            self.notify_status(f"{param.name} is {why}")
            return
        if not self.allow_write:
            self.notify_status("write gate is locked — press w to arm it")
            return
        current = self._param_values.get(param.name)

        def apply(value: Optional[str]) -> None:
            if value is not None:
                self._write_param(param, current, value)

        self.push_screen(EditValueScreen(param, current), apply)

    @work(thread=True)
    def _write_param_worker(
        self, param: p.Parameter, index: int, value, old
    ) -> None:
        try:
            with self._bridge_lock:
                self.bridge.set_parameter(param, index, value)
                header = self.bridge.get_header(param.region, index)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"error: {exc}")
            return
        self.call_from_thread(self._after_write, param, index, old, value, header)

    def _write_param(self, param: p.Parameter, current, raw: str) -> None:
        index = self._selected_program()
        if index is None:
            self.notify_status("no program selected")
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
        self._write_param_worker(param, index, value, current)

    def _after_write(
        self,
        param: p.Parameter,
        index: int,
        old,
        new,
        header: Dict[str, object],
    ) -> None:
        self._undo.append(
            _Change(
                region=param.region,
                index=index,
                keygroup=0,
                name=param.name,
                old=old,
                new=new,
            )
        )
        # Blunt invalidation: any write could have changed a name, and a name
        # change moves what the catalog says. Cheaper to re-read than to
        # reason about which caches a given write could not have touched.
        self._show_params(param.region, header)
        self._refresh_write_badge()
        self.notify_status(f"{param.name} = {p.describe_value(param, new)}")
        self._load_catalog(announce=False)

    def action_undo(self) -> None:
        if not self._undo:
            self.notify_status("nothing to undo")
            return
        if not self.allow_write:
            self.notify_status("write gate is locked — undo is a write")
            return
        change = self._undo.pop()
        param = p.lookup((change.region, change.name))
        self._write_param_worker(param, change.index, change.old, change.new)
        self._refresh_write_badge()

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
            self.notify_status("write gate is locked — press w to arm it")
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
            self.notify_status("write gate is locked — press w to arm it")
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

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id == "programs":
            self._load_program(event.cursor_row)

    def on_data_table_row_selected(self, event) -> None:
        # Enter on the parameters table means "edit this one".
        if event.data_table.id == "parameters":
            self.action_edit()


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
        bridge.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
