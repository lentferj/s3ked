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
        assert any(x in ("prog", "samp") for x in labels), (
            "the volume's directory is a second list and must be labelled "
            "as one, not run on from the volume rows")


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
    # The selected volume is shown 1-based, as the panel shows it (§96).
    # It vanished from this line for an hour when the register was thought
    # not to exist; the count ("34 vol") is a different number and both
    # belong here.
    import re
    assert re.search(r"vol 0*1\b", title), (
        f"the selected volume, 1-based: {title!r}")
    assert re.search(r"\d+ vol\b", title), "the volume count is still wanted"
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
        assert len(app.screen_stack) == 2, "armed, so it must ask how to load"
        from s3ked.app import LoadOptionsScreen
        assert isinstance(app.screen_stack[-1], LoadOptionsScreen)

        # the defaults are add-to-memory and no renumber, so Enter is the
        # shortest path to the load that `l` did before this screen existed
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 2, "and then it must confirm"

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
        await pilot.press("enter")
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
    # It used to end by asking the user to press r. That relied on them
    # judging when the load had finished with nothing to judge from, and
    # pressing r too early left the program list stale -- which is how this
    # was reported. It now hands off to LoadingScreen, which waits.
    assert "_await_load" in src
    assert "Press r" not in src


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
        await pilot.press("enter")
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


async def test_the_source_screen_shows_what_can_and_cannot_be_set():
    """The volume is listed, and says where it IS set -- since §96."""
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
    assert "Volume" in text and "settable" in text, (
        "the volume stopped being a front-panel job in §96")
    assert "panel only" not in text


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
        await pilot.press("enter")
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
            if region == "keygroup" and selector == 0 and count > 2:
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
            if region == "keygroup" and count > 2:
                raw = bytearray(super().get_header_bytes(
                    region, index, offset, count,
                    selector=selector, timeout=timeout))
                raw[3], raw[4] = 72, 48          # inverted: lo > hi
                return bytes(raw)
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


async def test_who_uses_reads_the_row_not_the_global_index():
    """The samples pane lists the PROGRAM's samples now, not the machine's.

    `u` used to take the pane's cursor row and index the global resident list
    with it. Those were the same list until the pane became program-centric;
    afterwards the same cursor picks a different sample, silently. So this
    asserts the report names the ROW's sample, on a program deliberately
    chosen because its first sample is not the machine's first sample.
    """
    from textual.widgets import DataTable, Static
    from s3ked.app import S3kedApp, ReportScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        for _ in range(30):
            await pilot.pause()
        app.query_one("#programs", DataTable).move_cursor(row=4)
        for _ in range(40):
            await pilot.pause()

        pane = app.query_one("#samples", DataTable)
        assert pane.row_count, "no samples listed for this program"
        row_name = str(pane.get_row_at(0)[0]).strip()
        assert row_name != app._samples[0].strip(), (
            "this test needs a program whose first sample differs from the "
            "machine's, or it cannot tell the two lookups apart")

        pane.focus()
        pane.move_cursor(row=0)
        await pilot.press("u")
        for _ in range(40):
            await pilot.pause()

        screen = app.screen_stack[-1]
        assert isinstance(screen, ReportScreen), screen
        title = str(screen.query_one("#report-title", Static).render())

    assert row_name in title, f"looked up the wrong sample: {title!r}"


async def test_a_write_action_refuses_before_opening_its_dialog():
    """`s` and `g` opened a dialog, took a keypress, closed, and only then
    reported the locked gate in the status line.

    Reported as "pressing any button just returns w/o changing the
    parameter", which is exactly what that looks like. action_load_volume
    always checked first; these two did not.
    """
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    for key, subject in (("s", "load source"), ("g", "main menu")):
        app = S3kedApp(DemoBridge(), allow_write=False)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            for _ in range(15):
                await pilot.pause()
            await pilot.press(key)
            await pilot.pause()

            assert len(app.screen_stack) == 1, (
                f"{key} opened a dialog with the gate locked")
            assert "write gate is locked" in app.last_status
            assert subject in app.last_status, (
                "the refusal should say what it refused")


async def test_a_refusal_is_marked_so_it_cannot_read_as_progress():
    """A locked gate rendered in the same muted style as "5 program(s)".

    A refusal that looks like a progress report is indistinguishable from
    the feature being broken.
    """
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        for _ in range(15):
            await pilot.pause()
        status = app.query_one("#status", Static)
        assert "-refused" not in status.classes, "clean at rest"

        await pilot.press("s")
        for _ in range(5):
            await pilot.pause()
        assert "-refused" in status.classes, "a refusal must be marked"

        await pilot.press("w")
        await pilot.press("r")
        for _ in range(25):
            await pilot.pause()
        assert "-refused" not in status.classes, (
            "the marker must clear once something ordinary happens")


async def test_the_parameter_pane_follows_the_selection():
    """Program, keygroup and sample each show their own fields.

    It always showed program fields, whatever was selected -- so a keygroup
    could be highlighted while the pane described the program.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        for _ in range(30):
            await pilot.pause()
        assert app._param_context[0] == "program"

        keygroups = app.query_one("#keygroups", DataTable)
        keygroups.focus()
        keygroups.move_cursor(row=0)
        for _ in range(40):
            await pilot.pause()
        assert app._param_context[0] == "keygroup", app._param_context

        samples = app.query_one("#samples", DataTable)
        samples.focus()
        samples.move_cursor(row=0)
        for _ in range(40):
            await pilot.pause()
        assert app._param_context[0] == "sample", app._param_context


async def test_filling_the_keygroup_pane_does_not_hijack_the_parameters():
    """Loading a program moves the keygroup cursor too.

    Reacting to that would swap the pane to a keygroup the instant a program
    was chosen -- the opposite of what selecting a program means. The guard
    is that the table must have focus.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause()
        programs = app.query_one("#programs", DataTable)
        programs.focus()
        programs.move_cursor(row=2)
        for _ in range(40):
            await pilot.pause()

    assert app._param_context[0] == "program", app._param_context


async def test_an_edit_writes_through_the_pane_context_not_the_program():
    """Showing keygroup fields while writing to the program header would
    corrupt a different parameter at the same offset, silently."""
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    app._param_context = ("keygroup", 1, 3)
    app._param_values = {"FILFRQ": 50}

    from s3k import params as p
    captured = {}

    def fake_worker(param, index, value, old, keygroup=0):
        captured.update(param=param.name, index=index, keygroup=keygroup)

    app._write_param_worker = fake_worker
    app._write_param(p.lookup(("keygroup", "FILFRQ")), 50, "60")

    assert captured == {"param": "FILFRQ", "index": 1, "keygroup": 3}, captured


async def test_the_source_dialog_stays_open_and_applies_every_key():
    """It used to dismiss on each keypress.

    Setting a drive and a partition meant opening it twice, and the disk was
    re-read after each -- seven round trips a time. It stays up now, applies
    immediately, and re-reads the directory once on close.

    Also covers the keys themselves: `[` and `]` did nothing here at all,
    because Textual names punctuation keys and `event.key in ("[", "]")`
    never matched. The same keys worked on the main screen, where Textual
    resolves the binding rather than this code comparing it.
    """
    from s3ked.app import S3kedApp, SourceScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen_stack[-1], SourceScreen)

        for key in ("3", "]", "]", "x"):
            await pilot.press(key)
            for _ in range(25):
                await pilot.pause()
            assert isinstance(app.screen_stack[-1], SourceScreen), (
                f"{key!r} closed the dialog")

        source = app.bridge.load_source()
        assert source["scsi_drive_id"] == 3, "a digit selects the SCSI drive"
        assert source["partition"] == 2, "] steps the partition, twice"
        assert source["device_type"] == 2, "x selects flash"

        await pilot.press("[")
        for _ in range(25):
            await pilot.pause()
        assert app.bridge.load_source()["partition"] == 1, "[ steps back"

        await pilot.press("escape")
        for _ in range(30):
            await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_the_dialog_shows_what_the_machine_reports_not_what_was_asked():
    """Three of these registers acknowledge an error and write anyway, and
    one acknowledges OK and ignores the write (§76). The rows follow the
    read-back, so a refused change cannot look like it took."""
    from textual.widgets import Label
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Stubborn(DemoBridge):
        def select_drive(self, scsi_id, *, timeout=None):
            super().select_drive(scsi_id, timeout=timeout)
            self._scsi_drive_id = 6          # the machine went elsewhere
            return self.load_source()

    app = S3kedApp(Stubborn(), allow_write=True)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("2")
        for _ in range(25):
            await pilot.pause()
        row = str(app.screen_stack[-1].query_one("#source-drive", Label).render())

    assert "6" in row, f"row should show what the machine reports: {row!r}"
    assert "2" not in row.split("press")[0], f"showed the request: {row!r}"


async def test_the_source_dialog_renders_its_bracket_hint():
    """`[b][[/b]` printed "[/b]": Rich reads `[[` as an escaped bracket and
    the `/b]` falls through as plain text."""
    from textual.widgets import Label
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        text = " ".join(str(w.render()) for w in app.screen_stack[-1].query(Label))

    assert "[/b]" not in text, f"markup leaked into the display: {text!r}"
    assert "[ and ]" in text, f"bracket hint not rendered: {text!r}"


async def test_the_source_dialog_lists_the_volumes_on_the_selected_drive():
    """Stepping the SCSI ID with nothing on screen is guesswork.

    This family gives no other way to tell one drive from another without
    loading from it, so the dialog shows what is there. The volume list pages
    sixteen records a request -- two round trips for a 30-volume disc -- and
    is cheap enough to re-read on every change. The DIRECTORY is the
    expensive one and stays deferred to when the dialog closes.
    """
    from textual.widgets import Label
    from s3ked.app import S3kedApp, SourceScreen
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause()
        await pilot.press("s")
        for _ in range(30):
            await pilot.pause()

        screen = app.screen_stack[-1]
        assert isinstance(screen, SourceScreen)
        listing = str(screen.query_one("#source-vols-body", Label).render())
        assert listing.strip() and "nothing here" not in listing, listing
        assert "v0" in listing, f"no volume rows: {listing!r}"


async def test_reading_the_disk_notices_a_catalog_that_changed_underneath():
    """A load -- from `l`, or from the front panel -- changes what is
    resident, and the machine announces nothing.

    Reported: loaded a volume, and the program list still showed the seven
    from before. The disk read now re-checks, because that is exactly when a
    load is likely to have happened.
    """
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge(), allow_write=False)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        for _ in range(25):
            await pilot.pause()
        before = app.query_one("#programs", DataTable).row_count
        assert before, "no programs to begin with"

        # something loads a volume while the editor is sitting there
        app.bridge._programs = list(app.bridge._programs) + ["NEW ARRIVAL"]

        await pilot.press("d")
        for _ in range(40):
            await pilot.pause()
        after = app.query_one("#programs", DataTable).row_count
        status = app.last_status

    assert after == before + 1, f"program list still stale: {before} -> {after}"
    assert "catalog changed" in status, status


async def test_an_empty_partition_clears_the_volume_count_too():
    """The title kept the previous count above "nothing here".

    So stepping onto an empty partition read as though it still held the
    last disc's volumes -- the one number a user scanning for the right
    drive is most likely to look at.
    """
    from textual.widgets import Label
    from s3ked.app import S3kedApp, SourceScreen
    from s3ked.demo import DemoBridge

    class Emptying(DemoBridge):
        empty = False

        def volume_list(self, *, limit=512, timeout=None):
            if self.empty:
                return []
            return super().volume_list(limit=limit, timeout=timeout)

    app = S3kedApp(Emptying(), allow_write=True)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        for _ in range(25):
            await pilot.pause()
        await pilot.press("s")
        for _ in range(30):
            await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, SourceScreen)
        title = str(screen.query_one("#source-vols-title", Label).render())
        assert "(" in title, f"expected a count to begin with: {title!r}"

        app.bridge.empty = True
        await pilot.press("]")
        for _ in range(30):
            await pilot.pause()
        title = str(screen.query_one("#source-vols-title", Label).render())
        body = str(screen.query_one("#source-vols-body", Label).render())

    assert "none" in title, f"stale count left in the title: {title!r}"
    assert "nothing on this partition" in body, body


async def test_a_load_holds_a_dialog_until_the_person_says_it_finished():
    """There is no completion signal to wait on.

    STAT carries no busy flag, and the only way to watch progress is to
    poll -- which preceded the machine sitting at BUSY until a power cycle
    (§71). So the person watching the front panel is the sensor, and closing
    the dialog is the signal. Reported as: loaded a volume, pressed r too
    early, program list still stale.
    """
    from s3ked.app import S3kedApp, LoadingScreen
    from s3ked.demo import DemoBridge

    fired = []

    class Watch(DemoBridge):
        def trigger_load(self, load_type=1, *, timeout=None):
            fired.append(load_type)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        for _ in range(25):
            await pilot.pause()
        await pilot.press("d")
        for _ in range(40):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(40):
            await pilot.pause()

        assert fired == [1], "the load must actually have been triggered"
        assert isinstance(app.screen_stack[-1], LoadingScreen), (
            "the dialog must hold until the user closes it")

        # a program arrives while the dialog is up, as one would during a load
        app.bridge._programs = list(app.bridge._programs) + ["ARRIVED"]
        await pilot.press("escape")
        for _ in range(40):
            await pilot.pause()
        names = app._programs

    assert "ARRIVED" in names, "closing the dialog must re-read the catalog"


async def test_the_loading_dialog_sends_nothing_while_it_waits():
    """Polling a loading machine is the one thing this must not do."""
    import inspect
    from s3ked.app import LoadingScreen, S3kedApp

    source = inspect.getsource(LoadingScreen)
    for forbidden in ("bridge", "status(", "program_list", "hd_directory"):
        assert forbidden not in source, (
            f"the waiting dialog touches {forbidden!r}")

    waiter = inspect.getsource(S3kedApp._await_load)
    assert "self.bridge" not in waiter, "the waiter must not talk to the device"


# --- how to load: the two questions that have answers -----------------------


async def test_the_load_screen_cycles_the_type_but_never_offers_the_OS():
    """All eight types are performable; one of them is not a keypress away.

    Writing value n performs load type n (§94), so the choice is real. Type
    6 loads an operating system off the disc over the running one -- the
    bridge guards it behind an explicit flag, and this screen must not be a
    way around that guard.
    """
    from textual.widgets import Label
    from s3ked.app import LoadOptionsScreen, S3kedApp
    from s3ked.demo import DemoBridge
    from s3k import messages as m

    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        for _ in range(20):
            await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, LoadOptionsScreen)
        what = str(screen.query_one("#loadopts-what", Label).render())

        # walk the whole cycle and record every type it can reach
        reached = {screen.load_type}
        for _ in range(len(m.LOAD_TYPES) * 2):
            await pilot.press("t")
            await pilot.pause()
            reached.add(screen.load_type)

    assert "ALL PROGS+SAMPLES" in what, "opens on the panel's setting, named"
    assert 6 not in reached, (
        "Operating System must not be reachable by holding a key down")
    assert reached == {0, 1, 2, 3, 4, 5, 7}, reached


async def test_clearing_first_deletes_then_loads_and_says_so():
    """Clear is not the panel's CLR -- it is the deletes it is made of."""
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    order = []

    class Watch(DemoBridge):
        def clear_memory(self, *, timeout=None):
            order.append("clear")
            return super().clear_memory(timeout=timeout)

        def trigger_load(self, load_type=1, *, timeout=None):
            order.append("load")

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("c")          # clear first
        await pilot.press("enter")
        await pilot.pause()
        prompt = str(
            app.screen_stack[-1].query_one("#confirm-prompt", Static).render())
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()

    assert "EVERY resident program and sample is deleted" in prompt
    assert "not the panel's CLR" in prompt
    assert "one program will survive" in prompt
    assert order == ["clear", "load"], "the clear must precede the load"


async def test_clearing_first_is_measured_against_the_whole_machine():
    """Adding is budgeted against free memory; clearing frees it all.

    Measuring a clear-then-load against current free would refuse loads that
    fit comfortably, which is the wrong direction to be wrong for an option
    whose whole point is making room.
    """
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    class Full(DemoBridge):
        def status(self, *, timeout=None):
            s = super().status(timeout=timeout)
            s.max_words = 8 * 1024 * 1024      # 16 MB machine
            s.free_words = 1024                # almost nothing free
            return s

    prompts = []
    app = S3kedApp(Full(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        for keys in (("enter",), ("c", "enter")):
            await pilot.press("l")
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
            await pilot.pause()
            prompts.append(str(app.screen_stack[-1]
                               .query_one("#confirm-prompt", Static).render()))
            await pilot.press("n")
            await pilot.pause()

    adding, clearing = prompts
    assert "DOES NOT FIT" in adding, "1024 words free cannot take the volume"
    assert "DOES NOT FIT" not in clearing, "16 MB can"


async def test_renumbering_runs_after_the_load_not_before():
    """The program list is not final until the load is, and the dialog is
    the only signal that it is. Renumbering before then numbers the old bank.
    """
    from s3ked.app import LoadingScreen, S3kedApp
    from s3ked.demo import DemoBridge

    order = []

    class Watch(DemoBridge):
        def trigger_load(self, load_type=1, *, timeout=None):
            order.append("load")

        def renumber_programs(self, *, timeout=None):
            order.append(f"renumber:{len(self._programs)}")
            return super().renumber_programs(timeout=timeout)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("n")          # renumber on
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()

        assert order == ["load"], "nothing may be renumbered while it loads"
        assert isinstance(app.screen_stack[-1], LoadingScreen)

        # programs arrive while the dialog is up, as they would during a
        # load -- carrying number 1, which is what makes them collide
        app.bridge.arrive("ARRIVED", program_number=1)
        await pilot.press("escape")
        for _ in range(40):
            await pilot.pause()

    assert order == ["load", "renumber:6"], (
        "the renumber must see the programs the load brought in")
    # 0-based: the panel calls these 1..6, the byte holds 0..5 (§91)
    assert app.bridge.program_numbers() == [0, 1, 2, 3, 4, 5]


async def test_renumbering_is_off_unless_asked_for():
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    called = []

    class Watch(DemoBridge):
        def trigger_load(self, load_type=1, *, timeout=None):
            pass

        def renumber_programs(self, *, timeout=None):
            called.append(True)
            return super().renumber_programs(timeout=timeout)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("escape")
        for _ in range(30):
            await pilot.pause()

    assert called == [], "renumber is opt-in"


async def test_renumber_is_meaningless_when_memory_is_cleared_first():
    """Nothing survives to collide with, so picking clear drops the option.

    Driven through the screen rather than its attributes, because the thing
    under test is what the keys do: n then c must not smuggle a renumber
    through on the strength of having been pressed first.
    """
    from s3ked.app import LoadOptionsScreen, S3kedApp
    from s3ked.demo import DemoBridge

    chosen = []
    app = S3kedApp(DemoBridge(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        app.push_screen(LoadOptionsScreen(), chosen.append)
        await pilot.pause()
        await pilot.press("n")      # renumber on...
        await pilot.press("c")      # ...then clear first
        await pilot.press("enter")
        for _ in range(10):
            await pilot.pause()

    assert len(chosen) == 1
    clear_first, renumber, _load_type = chosen[0]
    assert clear_first is True
    assert renumber is False, "clear wins; the renumber is dropped"


def test_renumbering_gives_every_program_a_distinct_number():
    """The remote SEQU: list order, so nothing stacks.

    The value written is the list position, NOT position+1. The field is
    0-based and the panel adds one for display -- measured by reading all
    fifteen resident programs straight after SEQU had shown them as 1..15,
    and the bytes read 0..14 (§91). Writing position+1 would leave the
    machine's first program number unused and shift every program up one.
    """
    from s3ked.demo import DemoBridge

    bridge = DemoBridge()
    # make them all collide first, the way four loaded volumes would
    for index in range(5):
        bridge.set_header_bytes("program", index, 15, bytes([0]))
    assert bridge.program_numbers() == [0, 0, 0, 0, 0]

    result = bridge.renumber_programs()
    assert result["renumbered"] == 5
    assert result["beyond_range"] == 0
    assert bridge.program_numbers() == [0, 1, 2, 3, 4]
    assert len(set(bridge.program_numbers())) == 5


def test_renumbering_stops_at_program_128():
    """There are 128 MIDI program numbers, 0-127, and a machine holds more."""
    from s3ked.demo import DemoBridge

    bridge = DemoBridge()
    bridge._programs = [f"P{i:03d}" for i in range(140)]

    calls = []
    original = bridge.set_header_bytes
    bridge.set_header_bytes = lambda *a, **k: calls.append(a[1])

    result = bridge.renumber_programs()
    bridge.set_header_bytes = original

    assert result["renumbered"] == 128
    assert result["beyond_range"] == 12
    assert calls == list(range(128)), "the first 128, and no write past them"


async def test_the_load_screen_shows_the_panels_load_type():
    """Readable, not choosable -- and it says which when they differ.

    The panel writes its LOAD-page selection into the trigger register, so
    this can show it. It cannot offer it: triggering IS writing that
    register and the value that loads is 1, so a load fired here is always
    type 1 and drags the panel's selection with it (§93).
    """
    from textual.widgets import Label
    from s3ked.app import LoadOptionsScreen, S3kedApp
    from s3ked.demo import DemoBridge

    seen = {}
    for panel_type in (1, 3):
        class Panel(DemoBridge):
            _load_type = panel_type

        app = S3kedApp(Panel(), allow_write=True)
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            await pilot.press("d")
            for _ in range(20):
                await pilot.pause()
            await pilot.press("l")
            for _ in range(20):
                await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, LoadOptionsScreen), panel_type
            seen[panel_type] = str(
                screen.query_one("#loadopts-what", Label).render())

    assert "ALL PROGS+SAMPLES" in seen[1]
    assert "as on the panel" in seen[1], (
        "opening on the panel's own type needs no warning")
    assert "all samples" in seen[3], "type 3 must be named, not numbered"
    assert "as on the panel" in seen[3], (
        "it opens on the panel's setting whatever that is")


async def test_an_unreadable_load_type_does_not_block_the_load():
    """One optional round trip must not cost the whole feature."""
    from textual.widgets import Label
    from s3ked.app import LoadOptionsScreen, S3kedApp
    from s3ked.demo import DemoBridge

    class Mute(DemoBridge):
        def load_type(self, *, timeout=None):
            raise RuntimeError("no answer")

    app = S3kedApp(Mute(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        for _ in range(20):
            await pilot.pause()
        screen = app.screen_stack[-1]
        assert isinstance(screen, LoadOptionsScreen)
        what = str(screen.query_one("#loadopts-what", Label).render())

    assert "could not be read" in what


async def test_the_chosen_load_type_reaches_the_bridge():
    """The screen's choice must be what gets written, not a default.

    Writing value n performs load type n (§94), so a choice that quietly
    fired type 1 would load the wrong thing -- and the machine gives no
    indication which type it just ran.
    """
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    fired = []

    class Watch(DemoBridge):
        _load_type = 0                      # panel sits on ENTIRE VOLUME

        def trigger_load(self, load_type=1, *, force=False, timeout=None):
            fired.append(load_type)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        for _ in range(20):
            await pilot.pause()
        await pilot.press("l")
        for _ in range(20):
            await pilot.pause()
        assert app.screen_stack[-1].load_type == 0, "opens on the panel's type"
        await pilot.press("t")              # 0 -> 1
        await pilot.press("t")              # 1 -> 2, "programs only"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        prompt = app.last_status
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()

    assert fired == [2], f"the chosen type must be the one fired, got {fired}"


def test_the_bridge_refuses_an_unknown_load_type():
    """The register performs what it is given, so an unknown value is an
    unknown operation -- not a no-op, which is what §74 believed."""
    import pytest
    from s3k import bridge as b

    class Bare(b.S3kBridge):
        def __init__(self):
            pass

    with pytest.raises(ValueError, match="not one of"):
        Bare().trigger_load(99)


def test_the_bridge_guards_the_operating_system_load():
    """Type 6 loads an OS off the disc over the running one."""
    import pytest
    from s3k import bridge as b
    from s3k import messages as m

    assert m.LOAD_TYPES[6] == "Operating System"

    sent = []

    class Bare(b.S3kBridge):
        def __init__(self):
            pass

        def invalidate_structure(self):
            pass

        def _drain(self):
            pass

        def _send(self, frame, write=False):
            sent.append(frame)

        exclusive_channel = 0

    with pytest.raises(ValueError, match="guarded"):
        Bare().trigger_load(6)
    assert sent == [], "a guarded type must not reach the wire"

    Bare().trigger_load(6, force=True)
    assert len(sent) == 1, "force=True means it"


async def test_the_disk_pane_says_what_to_press_before_it_is_read():
    """It is empty on every launch by design, so it must not look broken.

    The disk read is 7 round trips and fails outright on a machine with no
    disk attached, so it is deliberately not part of the startup catalog. A
    pane titled "Disk" and holding nothing is indistinguishable from one that
    tried and failed -- reported as "the disk pane came up empty".
    """
    from textual.widgets import Static
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    app = S3kedApp(DemoBridge())
    async with app.run_test(size=(130, 44)) as pilot:
        assert await _settled(pilot, app)
        title = str(app.query_one("#disk-title", Static).render())

    assert "d" in title and "Disk" in title, title


async def test_refresh_re_reads_the_disk_once_it_has_been_read():
    """`r` leaving a populated pane stale is the surprising behaviour.

    Not before, though: on a machine with no disk the read fails, and
    startup must not pay for it.
    """
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    reads = []

    class Watch(DemoBridge):
        def volume_list(self, *, limit=512, timeout=None):
            reads.append("disk")
            return super().volume_list(limit=limit, timeout=timeout)

    app = S3kedApp(Watch())
    async with app.run_test(size=(130, 44)) as pilot:
        assert await _settled(pilot, app)
        await pilot.press("r")
        for _ in range(25):
            await pilot.pause()
        assert reads == [], "refresh must not read a disk nobody asked for"

        await pilot.press("d")
        for _ in range(30):
            await pilot.pause()
        assert len(reads) == 1

        await pilot.press("r")
        for _ in range(30):
            await pilot.pause()

    assert len(reads) == 2, "once read, a refresh must keep it current"


async def test_enter_on_a_volume_row_selects_that_volume():
    """It used to say a volume was a front-panel job. It is not (§96)."""
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    chosen = []

    class Watch(DemoBridge):
        def select_volume(self, volume, *, timeout=None):
            chosen.append(volume)
            return super().select_volume(volume, timeout=timeout)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        assert await _settled(pilot, app)
        await pilot.press("d")
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#volumes", DataTable)
        table.focus()
        table.move_cursor(row=2)
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()

    assert chosen == [2], f"the row's own volume index, not the cursor: {chosen}"


async def test_a_directory_row_is_not_a_volume():
    """The rows under the divider are files. Selecting one means nothing."""
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    chosen = []

    class Watch(DemoBridge):
        def select_volume(self, volume, *, timeout=None):
            chosen.append(volume)
            return super().select_volume(volume, timeout=timeout)

    app = S3kedApp(Watch(), allow_write=True)
    async with app.run_test(size=(130, 44)) as pilot:
        assert await _settled(pilot, app)
        await pilot.press("d")
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#volumes", DataTable)
        labels = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        row = next(i for i, x in enumerate(labels) if x in ("prog", "samp"))
        table.focus()
        table.move_cursor(row=row)
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(25):
            await pilot.pause()

    assert chosen == [], "a directory row is not a volume"


async def test_selecting_a_volume_needs_the_write_gate():
    from textual.widgets import DataTable
    from s3ked.app import S3kedApp
    from s3ked.demo import DemoBridge

    chosen = []

    class Watch(DemoBridge):
        def select_volume(self, volume, *, timeout=None):
            chosen.append(volume)
            return super().select_volume(volume, timeout=timeout)

    app = S3kedApp(Watch(), allow_write=False)
    async with app.run_test(size=(130, 44)) as pilot:
        assert await _settled(pilot, app)
        await pilot.press("d")
        for _ in range(30):
            await pilot.pause()
        table = app.query_one("#volumes", DataTable)
        table.focus()
        table.move_cursor(row=1)
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()

    assert chosen == []
    assert "write gate is locked" in app.last_status
