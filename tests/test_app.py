# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""The Textual TUI, driven through ``run_test()`` against DemoBridge subclasses.

The safety behaviours are the point of this file: the write gate, the
arm-then-fire path to the deletes, and the fact that no single keypress
reaches a destructive operation.
"""

import pytest
from textual.widgets import DataTable

from s3ked.app import ConfirmScreen, EditValueScreen, MasterScreen, S3kedApp
from s3ked.demo import DemoBridge


async def _settled(pilot, app, tries=60):
    """Wait until the initial catalog load has painted."""
    for _ in range(tries):
        await pilot.pause()
        if app._programs and app._param_rows:
            return True
    return False


async def _app(allow_write=False, bridge=None):
    return S3kedApp(bridge or DemoBridge(), allow_write=allow_write)


# --- startup ----------------------------------------------------------------


async def test_loads_catalog_on_start():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        assert app._programs == [
            "BASS ROUND",
            "PAD WIDE",
            "KIT DRY",
            "BELL SOFT",
            "STRINGS LO",
        ]
        assert len(app._samples) == 9
        assert "5 program(s)" in app.last_status


async def test_program_pane_is_populated():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        table = app.query_one("#programs", DataTable)
        assert table.row_count == 5


async def test_parameters_pane_shows_the_selected_program():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        assert len(app._param_rows) == 85   # 85 since PRIDENT was added
        assert app.query_one("#parameters", DataTable).row_count == 85


async def test_keygroup_pane_follows_the_programs_group_count():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        assert app._keygroups == 2  # first demo program
        assert app.query_one("#keygroups", DataTable).row_count == 2


async def test_empty_machine_does_not_crash():
    class Empty(DemoBridge):
        def program_list(self, *, timeout=None):
            return []

        def sample_list(self, *, timeout=None):
            return []

    app = await _app(bridge=Empty())
    async with app.run_test() as pilot:
        for _ in range(20):
            await pilot.pause()
        assert "0 program(s)" in app.last_status


async def test_catalog_error_is_reported_not_raised():
    class Broken(DemoBridge):
        def program_list(self, *, timeout=None):
            raise RuntimeError("device fell over")

    app = await _app(bridge=Broken())
    async with app.run_test() as pilot:
        for _ in range(20):
            await pilot.pause()
            if "error" in app.last_status:
                break
        assert "device fell over" in app.last_status


# --- the write gate ---------------------------------------------------------


async def test_write_gate_is_locked_by_default():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        assert app.allow_write is False
        assert "locked" in app.sub_title


async def test_w_toggles_the_gate_and_the_header_badge():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("w")
        await pilot.pause()
        assert app.allow_write is True
        assert "ARMED" in app.sub_title
        await pilot.press("w")
        await pilot.pause()
        assert app.allow_write is False


async def test_edit_is_refused_while_the_gate_is_locked():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        app.query_one("#parameters", DataTable).move_cursor(row=2)  # PRGNUM
        await pilot.press("e")
        await pilot.pause()
        assert "write gate is locked" in app.last_status
        assert not isinstance(app.screen, EditValueScreen)


async def test_edit_opens_a_modal_once_armed():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(
            i for i, x in enumerate(app._param_rows) if x.name == "PRIORT"
        )
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EditValueScreen)


async def test_editing_writes_through_and_repaints():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRIORT")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#edit-input").value = "3"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if "PRIORT" in app.last_status:
                break
        assert app.last_status == "PRIORT = hold"
        assert app.bridge.get_parameter("PRIORT", 0) == 3


async def test_a_rename_updates_the_catalog():
    """Any write can change a name, so the catalog must be re-read."""
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRNAME")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#edit-input").value = "RENAMED"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if app._programs and app._programs[0] == "RENAMED":
                break
        assert app._programs[0] == "RENAMED"


async def test_read_only_parameters_cannot_be_edited():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "GROUPS")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        assert "read-only" in app.last_status
        assert not isinstance(app.screen, EditValueScreen)


async def test_internal_addresses_cannot_be_edited():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "KGRP1@")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        assert "internal block address" in app.last_status


async def test_edit_modal_cancels_without_writing():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        before = app.bridge.get_parameter("PRIORT", 0)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRIORT")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.bridge.get_parameter("PRIORT", 0) == before


# --- undo -------------------------------------------------------------------


async def test_undo_restores_the_previous_value():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRIORT")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#edit-input").value = "3"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if app._undo:
                break
        assert app.bridge.get_parameter("PRIORT", 0) == 3
        await pilot.press("z")
        for _ in range(30):
            await pilot.pause()
            if app.bridge.get_parameter("PRIORT", 0) == 1:
                break
        assert app.bridge.get_parameter("PRIORT", 0) == 1


async def test_undo_with_nothing_to_undo():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("z")
        await pilot.pause()
        assert "nothing to undo" in app.last_status


async def test_undo_is_itself_gated():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        app._undo.append(
            type(app._undo, (), {})  # placeholder replaced below
            if False
            else __import__("s3ked.app", fromlist=["_Change"])._Change(
                region="program",
                index=0,
                keygroup=0,
                name="PRIORT",
                old=1,
                new=3,
            )
        )
        await pilot.press("w")  # lock the gate
        await pilot.press("z")
        await pilot.pause()
        assert "undo is a write" in app.last_status


async def test_pending_change_count_is_in_the_subtitle():
    """It must not scroll away with the transient status line."""
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRIORT")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#edit-input").value = "2"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if app._undo:
                break
        assert "1 change(s)" in app.sub_title


# --- destructive operations -------------------------------------------------


async def test_no_single_key_reaches_a_delete():
    """The standing rule, asserted rather than trusted.

    Every bound key is pressed with the gate armed; none may remove a program.
    """
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        before = list(app._programs)
        for binding in S3kedApp.BINDINGS:
            key = binding.key
            if key in ("q", "tab"):
                continue
            await pilot.press(key)
            await pilot.pause()
            if app.screen is not app.screen.app.screen_stack[0]:
                await pilot.press("escape")
                await pilot.pause()
        for _ in range(10):
            await pilot.pause()
        assert app.bridge.program_list() == before


async def test_master_screen_needs_arm_then_fire():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MasterScreen)
        # Enter with nothing armed must do nothing at all.
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert app.bridge.program_list()[0] == "BASS ROUND"


async def test_master_arm_then_fire_reaches_a_confirmation_not_the_device():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")  # arm delete_program
        await pilot.pause()
        await pilot.press("enter")  # fire
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        # Still nothing deleted -- there is one more confirmation to go.
        assert len(app.bridge.program_list()) == 5


async def test_confirmed_delete_removes_the_program():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()
            if len(app._programs) == 4:
                break
        assert app._programs == ["PAD WIDE", "KIT DRY", "BELL SOFT", "STRINGS LO"]


async def test_declining_the_confirmation_deletes_nothing():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("n")
        for _ in range(10):
            await pilot.pause()
        assert len(app.bridge.program_list()) == 5


async def test_delete_is_refused_while_the_gate_is_locked():
    app = await _app(allow_write=False)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "write gate is locked" in app.last_status
        assert len(app.bridge.program_list()) == 5


async def test_a_delete_clears_the_undo_log():
    """A delete cannot be replayed backwards; the log must not imply it can."""
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        row = next(i for i, x in enumerate(app._param_rows) if x.name == "PRIORT")
        app.query_one("#parameters", DataTable).move_cursor(row=row)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#edit-input").value = "3"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if app._undo:
                break
        assert app._undo
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()
            if not app._undo:
                break
        assert app._undo == []


async def test_master_screen_cancels_cleanly():
    app = await _app(allow_write=True)
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, MasterScreen)


async def test_delete_error_is_reported_not_raised():
    class Broken(DemoBridge):
        def delete_program(self, program, *, confirm=True):
            raise RuntimeError("no such program")

    app = await _app(allow_write=True, bridge=Broken())
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()
            if "error" in app.last_status:
                break
        assert "no such program" in app.last_status


# --- misc -------------------------------------------------------------------


async def test_refresh_rereads_the_catalog():
    app = await _app()
    async with app.run_test() as pilot:
        assert await _settled(pilot, app)
        app.bridge._programs[0] = "CHANGED"
        await pilot.press("r")
        for _ in range(30):
            await pilot.pause()
            if app._programs and app._programs[0] == "CHANGED":
                break
        assert app._programs[0] == "CHANGED"


async def test_every_shown_binding_has_a_description():
    for binding in S3kedApp.BINDINGS:
        if binding.show:
            assert binding.description


# --- the screens render, which nothing checked until there were screenshots --

@pytest.mark.parametrize("name,keys,allow_write,param,must_contain", [
    ("catalog", (), False, None, "Programs"),
    ("write-gate", ("w",), False, None, "write ARMED"),
    ("edit", ("w", "e"), False, "PRIORT", "range:"),
    ("master", ("m",), True, None, "Destructive operations"),
])
async def test_each_documented_screen_actually_renders(
    name, keys, allow_write, param, must_contain, tmp_path
):
    """The README's screenshots, as assertions.

    tools/screenshots.py was the first thing to drive this app's key handling
    end to end, and it caught three wrong assumptions immediately -- the app
    was right in all three. This keeps the screens under test so a later change
    cannot quietly break one and leave the picture in the README claiming
    otherwise.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=allow_write)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        if param is not None:
            table = app.query_one("#parameters", DataTable)
            names = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
            table.move_cursor(row=names.index(param))
            table.focus()
            await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await pilot.pause()
        out = tmp_path / f"{name}.svg"
        app.save_screenshot(str(out))

    text = out.read_text().replace("&#160;", " ").replace("&quot;", '"')
    assert must_contain in text, f"{name} did not render {must_contain!r}"


async def test_the_editor_refuses_a_block_address():
    """Row 0 of the parameters pane is PRIDENT, and `e` must not open on it.

    Found by a screenshot script aiming at the wrong row and getting a refusal
    -- correct behaviour that nothing had asserted.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        table = app.query_one("#parameters", DataTable)
        names = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
        table.move_cursor(row=names.index("PRIDENT"))
        table.focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert len(app.screen_stack) == 1, "no modal should have opened"


@pytest.mark.parametrize("keys,should_delete", [
    (("m",), False),
    (("m", "1"), False),
    (("m", "1", "enter"), False),
    (("m", "1", "enter", "n"), False),
    (("m", "1", "enter", "escape"), False),
    (("m", "1", "enter", "y"), True),
])
async def test_every_prefix_of_the_delete_path_is_safe(keys, should_delete):
    """Only the complete four-keypress sequence deletes. Every prefix is inert.

    The individual steps were already tested; this walks the path one key at a
    time, which is how a user meets it -- and how a stray Enter or a mistyped
    confirmation would meet it. `m 1 enter` fires with nothing armed only if a
    number was never pressed, and firing unarmed dismisses with None.
    """
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        before = len(app.bridge.program_list())
        for key in keys:
            await pilot.press(key)
            for _ in range(3):
                await pilot.pause()
        for _ in range(4):
            await pilot.pause()
        after = len(app.bridge.program_list())

    assert (after < before) is should_delete, (
        f"{' '.join(keys)} {'deleted' if after < before else 'did not delete'}"
    )


async def test_the_whole_delete_path_does_nothing_with_the_gate_locked():
    """Four correct keypresses, and still nothing, because the gate is shut."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        before = len(app.bridge.program_list())
        for key in ("m", "1", "enter", "y"):
            await pilot.press(key)
            for _ in range(3):
                await pilot.pause()
        assert len(app.bridge.program_list()) == before


async def test_the_armed_header_is_red_and_the_locked_one_is_not(tmp_path):
    """The armed gate is the one state where a keypress reaches the hardware.

    It used to use $accent, which is also ordinary emphasis elsewhere -- a
    warning that looks like decoration is not a warning. Asserted by rendering
    both states and diffing the colours rather than by reading the stylesheet,
    because what matters is what appears on the screen.
    """
    import re
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    async def render(name, keys):
        app = S3kedApp(DemoBridge(), allow_write=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            out = tmp_path / f"{name}.svg"
            app.save_screenshot(str(out))
        return set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', out.read_text()))

    locked = await render("locked", ())
    armed = await render("armed", ("w",))

    introduced = armed - locked
    assert introduced, "arming the gate changed no colour at all"

    def red_dominance(hexcolour: str) -> int:
        r = int(hexcolour[1:3], 16)
        g = int(hexcolour[3:5], 16)
        b = int(hexcolour[5:7], 16)
        return r - max(g, b)

    assert any(red_dominance(c) > 40 for c in introduced), (
        f"arming introduced {sorted(introduced)}, none of which reads as red"
    )


async def test_the_disk_pane_is_empty_until_asked():
    """Reading the disk is 7 round trips, so it is not part of startup.

    A machine with no disk attached would otherwise turn a failure into a
    startup failure rather than a message.
    """
    from textual.widgets import DataTable, Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#volumes", DataTable).row_count == 0
        assert "Disk" in str(app.query_one("#disk-title", Static).render())

        await pilot.press("d")
        for _ in range(15):
            await pilot.pause()

        table = app.query_one("#volumes", DataTable)
        assert table.row_count > 0
        assert "vol" in str(app.query_one("#disk-title", Static).render())
        # volumes are prefixed "v", the loaded volume's contents are indented,
        # so one pane can carry both without a second table
        assert str(table.get_row_at(0)[0]) == "v0"
        labels = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        assert any(x.startswith("v") for x in labels)
        assert any(x.startswith(" ") for x in labels), "directory rows too"


async def test_the_disk_pane_reports_a_failure_instead_of_crashing():
    """No disk, or a device that refuses, must not take the app down."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class NoDisk(DemoBridge):
        def volume_list(self, *, limit=512, timeout=None):
            raise RuntimeError("no disk attached")

    app = S3kedApp(NoDisk(), allow_write=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(15):
            await pilot.pause()
        assert "no disk attached" in app.last_status
        assert len(app.screen_stack) == 1


async def test_reading_the_disk_needs_no_write_gate():
    """It is a read. Arming the gate must not be a precondition."""
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(15):
            await pilot.pause()
        assert app.query_one("#volumes", DataTable).row_count > 0
        assert app.allow_write is False


async def test_the_disk_pane_shows_the_load_source():
    """The panel's own words: HARD-:C vol 001."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        title = str(app.query_one("#disk-title", Static).render())

    assert "HARD-:A" in title
    assert "vol 001" in title
    assert "SCSI 4" in title


async def test_stepping_the_partition_is_behind_the_write_gate():
    """It loads nothing, but it does change what the machine has selected.

    Anything that changes the device belongs behind the same gate as an edit,
    even when it is navigation rather than editing.
    """
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        before = str(app.query_one("#disk-title", Static).render())

        await pilot.press("]")
        for _ in range(20):
            await pilot.pause()

        assert "write gate is locked" in app.last_status
        assert str(app.query_one("#disk-title", Static).render()) == before


async def test_stepping_the_partition_moves_the_listing_once_armed():
    from textual.widgets import Static, DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        assert "HARD-:A" in str(app.query_one("#disk-title", Static).render())

        await pilot.press("]")
        for _ in range(25):
            await pilot.pause()
        title = str(app.query_one("#disk-title", Static).render())
        assert "HARD-:B" in title

        # and the listing followed, not just the label
        rows = app.query_one("#volumes", DataTable)
        names = [str(rows.get_row_at(i)[1]) for i in range(rows.row_count)]
        assert any(n.startswith("B ") for n in names)


async def test_a_machine_without_a_load_page_says_so():
    """Silence would look like a device that has no LOAD page."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class NoLoadPage(DemoBridge):
        def load_source(self, *, timeout=None):
            raise RuntimeError("no such operation")

    app = S3kedApp(NoLoadPage(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        assert "load source unavailable" in app.last_status


async def test_loading_a_volume_is_gated_and_confirmed():
    """A load is not a delete -- it adds -- but it moves megabytes."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()

        await pilot.press("l")
        await pilot.pause()
        assert "write gate is locked" in app.last_status
        assert len(app.screen_stack) == 1, "no confirmation while locked"

        app.allow_write = True
        await pilot.press("l")
        await pilot.pause()
        assert len(app.screen_stack) == 2, "armed, so it must confirm"

        prompt = str(app.screen_stack[-1].query_one("#confirm-prompt", Static).render())
        assert "MB" in prompt and "free memory" in prompt

        await pilot.press("n")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_the_confirmation_says_when_a_volume_does_not_fit():
    """The one thing the machine will not tell you until it has half-loaded.

    It reports "insufficient waveform memory" once, then behaves as though
    all is well -- so programs whose samples never arrived play silence.
    """
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Huge(DemoBridge):
        def hd_directory(self, kind=1, *, limit=512, timeout=None):
            return super().hd_directory(kind, limit=limit, timeout=timeout) * 30

    app = S3kedApp(Huge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        prompt = str(app.screen_stack[-1].query_one("#confirm-prompt", Static).render())

    assert "DOES NOT FIT" in prompt
    assert "play silence" in prompt


async def test_loading_without_reading_the_disk_first_is_refused():
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert "press d first" in app.last_status
        assert len(app.screen_stack) == 1


async def test_the_app_does_not_poll_a_loading_machine():
    """A 58.7 MB load probed every 8 s ran in bursts and then wedged.

    So the worker triggers and stops, and tells the user to refresh by hand.
    """
    import inspect
    from s3ked.app import S3kedApp

    src = inspect.getsource(S3kedApp._load_worker)
    assert "trigger_load" in src
    # no READS of the machine afterwards -- notify_status is not a read, so
    # the check has to name the bridge calls rather than the substring
    assert "self.bridge.status(" not in src
    assert "self.bridge.hd_directory(" not in src
    assert "self.bridge.volume_list(" not in src
    assert "Press r" in src


async def test_free_memory_comes_from_the_machine_not_a_constant():
    """A 2 MB machine must not be told that 32 MB fits."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Small(DemoBridge):
        def status(self, *, timeout=None):
            s = super().status(timeout=timeout)
            s.max_words = s.free_words = 1024 * 1024  # 2 MB, the base machine
            return s

    app = S3kedApp(Small(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        assert app._words_free == 1024 * 1024
        await pilot.press("l")
        await pilot.pause()
        prompt = str(app.screen_stack[-1].query_one("#confirm-prompt", Static).render())

    assert "2.00 MB" in prompt, prompt
    assert "DOES NOT FIT" in prompt, "3.56 MB does not fit in 2.00 MB"


async def test_the_disk_status_line_offers_the_keys_that_exist():
    """It used to say loading was a front-panel job. It is not, since `l`."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        status = app.last_status

    assert "the protocol cannot" not in status
    assert "press l" in status.lower()
    # the one part that IS still manual, because there is no volume register
    assert "panel" in status.lower()


async def test_the_source_screen_shows_what_can_and_cannot_be_set():
    """The volume is listed precisely because it CANNOT be set."""
    from s3ked.app import S3kedApp, SourceScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], SourceScreen)
        text = " ".join(str(w.render()) for w in app.screen_stack[-1].query("Label"))

    assert "SCSI drive" in text and "HARD" in text
    assert "Volume" in text and "panel only" in text


async def test_the_source_screen_writes_only_through_the_gate():
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    seen = []

    class Watch(DemoBridge):
        def select_drive(self, scsi_id, *, timeout=None):
            seen.append(scsi_id)
            return super().select_drive(scsi_id, timeout=timeout)

    app = S3kedApp(Watch(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("3")
        for _ in range(10):
            await pilot.pause()
        assert seen == [], "locked gate must not reach the machine"
        assert "write gate is locked" in app.last_status

        app.allow_write = True
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("3")
        for _ in range(30):
            await pilot.pause()

    assert seen == [3]


async def test_the_menu_screen_offers_every_page_the_register_has():
    """All eleven, read off the panel 2026-08-13 (§84).

    This test previously asserted the opposite -- that only three were named
    and the rest were "not guessed". That was right while it was true: the
    enumeration has gaps (GLOBAL is 8 where its button position would be 5),
    so guessing was never available. What settled it was a person reading the
    display while the register stepped, because no eyes-free discriminator
    exists: RMULTIDATA answers in every mode (§78).
    """
    from s3ked.app import S3kedApp, MenuScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, MenuScreen)
        text = " ".join(str(w.render()) for w in screen.query("Label"))
        await pilot.press("2")
        for _ in range(20):
            await pilot.pause()

    for name in ("SINGLE", "MULTI", "SAMPLE", "EFFECTS", "GLOBAL", "SAVE",
                 "LOAD", "EDIT"):
        assert name in text, name
    assert "modifier" in text, "EDIT is a modifier lamp, not a page"
    assert app.bridge.mode() == 2, "key 2 selects MULTI, which is value 2"


async def test_every_mode_the_bridge_names_is_reachable_from_the_menu():
    """The screen and the register's own table must not drift apart."""
    from s3ked.app import MenuScreen
    from s3ked.demo import DemoBridge

    offered = {value for value, _name in MenuScreen._CHOICES.values()}
    assert offered == set(DemoBridge.MODES), (
        f"menu offers {sorted(offered)}, bridge names "
        f"{sorted(DemoBridge.MODES)}")
    assert len(offered) == 11, "eight buttons, seven modes, EDIT on four"


async def test_no_clr_is_offered_because_the_machine_has_none_to_offer():
    """The panel's CLR softkey is not reachable, so the TUI must not imply it.

    It was offered here briefly, armed like a delete, on the reasoning that
    the trigger register's value picked the softkey. Measurement killed that:
    writing 1 loads, and 0 and 2-7 store cleanly and do nothing (§74).
    """
    from s3ked.app import MasterScreen

    offered = [a for a, _d in MasterScreen._ACTIONS.values()]
    assert "load_clr" not in offered
    assert all("load" not in a for a in offered), \
        "no load belongs in the destructive menu -- clearing is a delete"


async def test_plain_load_stays_at_the_appending_value():
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    fired = []

    class Watch(DemoBridge):
        def trigger_load(self, load_type=1, *, timeout=None):
            fired.append(load_type)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(20):
            await pilot.pause()

    assert fired == [1], "LOAD appends and is value 1"


async def test_clearing_memory_is_armed_and_says_what_survives():
    """The remote stand-in for CLR, built from the deletes it is made of."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        before = len(app.bridge.sample_list())
        assert before

        await pilot.press("m")
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        assert len(app.bridge.sample_list()) == before, "arming must not fire"

        await pilot.press("enter")
        await pilot.pause()
        prompt = str(app.screen_stack[-1].query_one("#confirm-prompt", Static).render())
        assert "no undo" in prompt
        assert "last one" in prompt, "the surviving program must be stated"

        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()

        assert app.bridge.sample_list() == []
        assert len(app.bridge.program_list()) == 1, "one program always survives"


async def test_clearing_memory_needs_the_write_gate():
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "write gate is locked" in app.last_status
        assert app.bridge.sample_list() != []


async def test_the_integrity_check_names_the_programs_that_play_silence():
    """The demo carries one dangling reference on purpose."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp, ReportScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        for _ in range(40):
            await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, ReportScreen)
        body = str(screen.query_one("#report-body", Static).render())
        footer = str(screen.query_one("#report-footer", Static).render())

    assert "silent zone" in body
    assert "DANGLING" in footer
    # reading is not writing: no gate needed for either of these
    assert app.allow_write is False


async def test_who_uses_this_sample_lists_the_zones():
    from textual.widgets import DataTable, Static
    from s3ked.app import S3kedApp, ReportScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        table = app.query_one("#samples", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.press("u")
        for _ in range(40):
            await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, ReportScreen)
        title = str(screen.query_one("#report-title", Static).render())
        body = str(screen.query_one("#report-body", Static).render())

    assert app._samples[0] in title
    assert "keygroup" in body or "nothing uses it" in body


async def test_the_boards_screen_declares_and_does_not_guess():
    """The device cannot be asked which boards it has, so this is a
    declaration -- and the default is to assume none."""
    from s3ked.app import S3kedApp, BoardsScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("B")
        await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, BoardsScreen)
        text = " ".join(str(w.render()) for w in screen.query("Label"))
        assert "IB304F" in text and "EB16" in text
        assert "not fitted" in text, "nothing is assumed fitted"

        await pilot.press("1")            # toggle IB304F on
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()

    assert app.bridge.boards == {"IB304F"}
    assert "IB304F" in app.last_status


async def test_the_demo_never_writes_a_config():
    """A demo that persisted settings would edit a user's file on a machine
    they may not even own."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("B")
        await pilot.pause()
        await pilot.press("2")
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()

    assert app._config_path is None
    assert "this session only" in app.last_status


async def test_the_key_legend_shows_every_binding_at_80_columns():
    """80x24 is the smallest size this project claims to support.

    Textual's Footer is one line and truncates rather than wrapping: with
    thirteen bindings it showed six and silently cut the rest, so the disk,
    load, menu, boards and audit keys were undiscoverable to anyone who had
    not read the README. Neither `height: auto` nor a grid layout changes
    that, both measured.

    KeyHints folds instead -- k2kremote's and eosed's answer to the same
    problem, ported rather than reinvented. Nothing is hidden; the legend
    grows a line.
    """
    from s3ked.app import S3kedApp, wrap_blocks

    blocks = [f"{b.key} {b.description}" for b in S3kedApp.BINDINGS
              if b.description and b.show]
    assert len(blocks) >= 13, "this test would be weak with few bindings"

    for width in (80, 100, 120):
        folded = wrap_blocks(blocks, width)
        for block in blocks:
            assert block in folded, f"{block!r} lost at {width} columns"
        widest = max(len(line) for line in folded.splitlines())
        assert widest <= width, f"line of {widest} exceeds {width}"


def test_wrap_blocks_never_splits_a_hint():
    """A break inside a label would read as two bindings that do not exist."""
    from s3ked.app import wrap_blocks

    blocks = ["q Quit", "l Load volume", "B Boards fitted"]
    for width in range(8, 60):
        folded = wrap_blocks(blocks, width)
        for block in blocks:
            assert block in folded, f"{block!r} split at width {width}"


def test_a_hint_wider_than_the_terminal_takes_its_own_line():
    """Rather than being cut, which is the behaviour being replaced."""
    from s3ked.app import wrap_blocks

    folded = wrap_blocks(["q Quit", "x " + "a very long description" * 3], 20)
    assert "a very long description" in folded
    assert folded.splitlines()[0] == "q Quit"


async def test_the_keygroup_pane_shows_real_key_ranges():
    """It showed a literal "-" for every keygroup until 2026-08-14.

    TODO said why: one request per keygroup, and the offsets should be
    trusted before spending them. §81 then wrote LONOTE/HINOTE while
    measuring whether overlapping keygroups layer, and the machine sounded or
    stayed silent exactly as predicted across six settings -- so offsets 3
    and 4 are behaviourally confirmed, which is the only confirmation
    available for a field with no readout of its own.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#keygroups", DataTable)
        shown = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]

    assert shown, "no keygroups listed at all"
    assert all(s != "-" for s in shown), f"placeholder still there: {shown}"
    assert all("–" in s for s in shown), f"not a range: {shown}"


async def test_a_keygroup_that_will_not_read_blanks_only_its_own_row():
    """A partial answer about the others beats none.

    The pane is one read per keygroup, so one failing read must not cost the
    whole pane -- which is what a single try around the loop would do.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Flaky(DemoBridge):
        def get_header_bytes(self, region, index, offset, count, *,
                             selector=0, timeout=None):
            if region == "keygroup" and count == 2 and selector == 0:
                raise RuntimeError("no reply")
            return super().get_header_bytes(region, index, offset, count,
                                            selector=selector, timeout=timeout)

    app = S3kedApp(Flaky(), allow_write=False)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#keygroups", DataTable)
        shown = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]

    assert shown[0] == "?", f"failed row should be '?', got {shown[0]!r}"
    assert len(shown) > 1 and "–" in shown[1], "the others must still resolve"


async def test_an_inverted_key_range_is_labelled_dead():
    """lo > hi selects nothing, measured (§81). Printing it as a plain range
    would read as a keygroup that plays across the whole span backwards."""
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Inverted(DemoBridge):
        def get_header_bytes(self, region, index, offset, count, *,
                             selector=0, timeout=None):
            if region == "keygroup" and count == 2:
                return bytes([72, 48])
            return super().get_header_bytes(region, index, offset, count,
                                            selector=selector, timeout=timeout)

    app = S3kedApp(Inverted(), allow_write=False)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#keygroups", DataTable)
        first = str(app.query_one("#keygroups", DataTable).get_row_at(0)[1])

    assert "dead" in first, f"inverted range not flagged: {first!r}"
