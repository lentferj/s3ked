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
        assert "volume" in str(app.query_one("#disk-title", Static).render())
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
