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

"""Akai S1000/S3000-family MIDI System Exclusive: protocol and transport.

This package is the device domain and knows nothing about any user
interface. The split mirrors the sibling eosed project's ``eos`` / ``eosed``:

* :mod:`s3k.messages` -- the wire codec. Frame layout, nibbling, operation
  codes, one class per message.
* :mod:`s3k.params` -- what the bytes *mean*. Offsets, widths and ranges of
  every field in the Program, Keygroup and Sample headers.
* :mod:`s3k.bridge` -- how the bytes get there. MIDI transport, throttling,
  port discovery, and the high-level operations built on the other two.

This family exposes exactly one SysEx protocol and it is an editor/librarian
protocol -- there is no screen-mirror or front-panel channel to attach to.
See CLAUDE.md and docs/RESOLUTION_NOTES.md §1 for the survey behind that.
"""

__all__ = ["messages", "params", "bridge"]
