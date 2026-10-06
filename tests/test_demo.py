# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""The demo stand-in must answer like the machine it stands in for."""


def _real_load_source():
    """Call the SHIPPED load_source against a scripted device.

    The previous version of the key-parity test below parsed the bridge's
    source for quoted keys -- a line parser that passed while the method
    returned anything, and failed on any reformatting. Calling it exercises
    the request/reply path instead.
    """
    from s3k import bridge as b, messages as m

    values = {0: 1, 2: 0, 11: 4, 12: 6, 49: 0, 91: 10, 4: 0}
    pending = []

    class Out:
        def send_message(self, message, *, write=False):
            pending.append(bytes(message))

    class In:
        def get_message(self):
            if not pending:
                return None
            frame = pending.pop(0)
            ch, _command, _payload = m.parse_frame(frame)
            request = m.HeaderRequest.decode(frame)
            return (
                bytes(
                    m.HeaderData(
                        command=m.Command.MISCDATA,
                        index=request.index,
                        selector=request.selector,
                        offset=0,
                        data=bytes([values.get(request.index, 0)]),
                        exclusive_channel=ch,
                    ).encode()
                ),
                0.0,
            )

    return b.S3kBridge(Out(), In(), "fake", timeout=0.5).load_source()


def test_the_demo_load_source_matches_the_bridge_exactly():
    """The stand-in must not be more generous than the machine.

    DemoBridge returned a "volume" key S3kBridge did not, so the pane showed
    `vol 001` here and `vol 000` on hardware, and the tests agreed with the
    demo. The volume register turned out to exist after all (§96), so the key
    is back -- on both sides this time.

    Which side was wrong is a detail; that they disagreed is the defect, and
    it is why they are compared rather than each asserted alone.
    """
    from s3ked.demo import DemoBridge

    real = _real_load_source()
    assert set(real) == {
        "scsi_drive_id",
        "scsi_local_id",
        "device_type",
        "partition",
        "volume",
        "cursor_value",
        "mode",
    }, "the bridge's own key list moved; update both sides"
    assert set(DemoBridge().load_source()) == set(real)


def test_the_demo_answers_the_surface_the_app_reads():
    """The app's startup and refresh path, against the stand-in.

    A demo that raises -- or answers with the wrong shape -- where the
    machine answers breaks every app test for a reason outside the app.
    So the read surface is called here directly: catalogs, counts, the
    load source, and the number/cursor registers the panes render.
    """
    from s3ked.demo import DemoBridge

    demo = DemoBridge()
    programs = demo.program_list()
    samples = demo.sample_list()
    assert len(programs) == 5 and all(isinstance(n, str) for n in programs)
    assert samples and all(isinstance(n, str) for n in samples)

    numbers = demo.program_numbers()
    assert len(numbers) == len(programs), "one number per program"
    assert all(isinstance(v, int) and 0 <= v <= 127 for v in numbers)
    assert demo.resident_pairs() == list(zip(programs, numbers))

    source = demo.load_source()
    assert all(isinstance(v, int) for v in source.values())

    assert demo.status().free_words > 0
    assert demo.mode() == 0
    assert demo.program_number() == 0
    assert demo.item_cursor() == 0
    assert demo.load_type() == 1
    assert demo.volume_list(), "the disk pane needs volumes"
    assert demo.hd_directory(), "and the selected volume needs contents"
    assert demo.keygroup_count(0) == 2
