# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# s3ked is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# s3ked is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""The local settings cache: the last good ports, and what is fitted.

This used to live inside :mod:`s3k.bridge`, together with a private
read-modify-write TOML store. Both have moved: the store is
:class:`vinsynlib.config.Settings`, and this module is what
:mod:`s3k.bridge` now re-exports, so ``from s3k import bridge as b;
b.load_last_ports(path)`` keeps working and ``s3ked/app.py`` keeps
importing it from ``bridge``.

Why the store was shared rather than kept here: every one of the traps
behaviour is written around is a property of *storing a preference*, not
of this sampler. Read-modify-write so unrelated keys survive each other's
saves. Refusing to overwrite a file that cannot be parsed, so one stray
bracket does not cost the user every other setting in it. Escaping
everything written, so a quote in an ALSA port name cannot produce a file
that is not TOML -- which the refusal above would then decline to repair
forever. Swallowing only :class:`OSError`, so a read-only directory costs a
preference rather than a run, while a real bug is still heard.

What stays here is what only this sampler knows.

**The expansion boards are one boolean each, not a list.** ``ib304f_fitted``
and ``eb16_fitted`` are separate keys so that a user editing the file by
hand does not have to guess list syntax, and so that declaring one board
does not silently clear the other. The flat writer handles booleans and
not sequences, which is the whole reason for the shape.

**The exclusive channel is 0-15, and is range-checked.** It is the number
the machine is told its siblings differ by, and a value outside the range
is not a channel: ``SETEX`` with one is refused by an S3000 and, on the
sibling machines, addressed to something else entirely.

BEHAVIOUR CHANGE, once: the output port is now written under the key
``port``, where this file wrote ``send_port``. An existing s3ked
``config.toml`` therefore forgets its remembered port on the first run
after this change, re-probes, and is rewritten in the new shape. That is
the whole cost and the file is explicitly disposable -- deleting it costs
one re-entry of each setting. The keys ``exclusive_channel``,
``ib304f_fitted`` and ``eb16_fitted`` are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

from vinsynlib.config import Settings

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "load_boards",
    "load_exclusive_channel",
    "load_last_ports",
    "save_boards",
    "save_exclusive_channel",
    "save_last_ports",
    "settings",
]

#: Local settings, gitignored. Named the way this project has always named
#: it; the library calls the same thing ``DEFAULT_PATH``.
DEFAULT_CONFIG_PATH = "config.toml"

#: One store for this application. Exposed so a caller that needs a key no
#: wrapper below covers can use ``settings.update(path, ...)`` rather than
#: growing a second TOML reader beside it.
settings = Settings("s3ked", DEFAULT_CONFIG_PATH)

#: The two expansion boards this sampler family has, and the config key each
#: one is declared with. A mapping rather than two pairs of constants so
#: that adding a board cannot leave the reader and the writer disagreeing
#: about its name -- which is the shape that has bitten the others.
_BOARDS: Dict[str, str] = {"IB304F": "ib304f_fitted", "EB16": "eb16_fitted"}

#: A SysEx exclusive channel is a nibble, 0-15. A value outside it is not a
#: channel at all.
MAX_EXCLUSIVE_CHANNEL = 15


def load_last_ports(path: str = DEFAULT_CONFIG_PATH) -> Optional[Tuple[str, str]]:
    """The send/receive pair that answered last time, if both are known.

    A full sweep tries every output port at up to a second each; on a host
    with two dozen ports that is tens of seconds. Trying the remembered pair
    first turns the common case into one round trip.

    Only when *both* ends are known. A half-remembered pair is not a pair:
    this tool opens one port and reads the other, and an output-only or
    input-only memory is no connection rather than half of one.
    """
    return settings.load_ports(path)


def save_last_ports(
    send_port: str, recv_port: str, path: str = DEFAULT_CONFIG_PATH
) -> None:
    """Remember both ports in one write to the file."""
    settings.save_ports(send_port, recv_port, path)


def load_exclusive_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    """The exclusive channel last used, 0-15, or None.

    The bool check is not decoration. ``isinstance(True, int)`` is true and
    ``0 <= True <= 15`` is true, so a hand-edited ``exclusive_channel =
    true`` came back as ``True`` -- and a bool is 1 on the wire, which is
    channel 1, silently, on a machine whose whole identity is which channel
    it answers to.
    """
    value = settings.read(path)[0].get("exclusive_channel")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_EXCLUSIVE_CHANNEL else None


def save_exclusive_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    settings.update(path, exclusive_channel=int(channel))


def load_boards(path: str = DEFAULT_CONFIG_PATH) -> Set[str]:
    """Expansion boards declared fitted in the settings cache.

    ``is True`` rather than truthiness: TOML's ``ib304f_fitted = 1`` is a
    perfectly valid file and means nothing to this program, so it declares
    nothing rather than declaring the board.
    """
    data = settings.read(path)[0]
    return {
        name
        for name, key in _BOARDS.items()
        if data.get(key) is True
    }


def save_boards(boards: Any, path: str = DEFAULT_CONFIG_PATH) -> None:
    """Declare which expansion boards are fitted.

    Every board is written, fitted or not, because a save is read-modify-write
    and a board that is not named would keep whatever it said before. That is
    how ``save_boards(set())`` means "un-declare both" rather than "leave
    alone".
    """
    fitted = {str(board).upper() for board in boards}
    settings.update(path, **{key: name in fitted for name, key in _BOARDS.items()})