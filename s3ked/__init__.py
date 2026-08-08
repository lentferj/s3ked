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

"""Terminal editor for the Akai S1000/S3000 sampler family.

The user-interface domain: :mod:`s3ked.app` (Textual TUI, ``s3ked``),
:mod:`s3ked.cli` (argparse, ``s3kcli``), and :mod:`s3ked.demo` (a bridge
stand-in so both can run with no hardware and no MIDI ports open).

The protocol and transport live in the sibling :mod:`s3k` package.
"""

__version__ = "0.1.0"
