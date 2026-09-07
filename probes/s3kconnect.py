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
"""Connect to the sampler WITHOUT a cold port sweep.

s3ked's DEFAULT_CONFIG_PATH is the relative "config.toml", so the cached
port pair only resolves when the process runs from the repo root. Every probe
script here runs from its own directory, so every run missed the cache and did
a full autodetect sweep instead: 40.0 s against 1.0 s cached, measured, and the
sweep is what spawns the transient ALSA sequencer clients that crowded the
bench and made a sibling session's device detection fail intermittently.

python-rtmidi's close_port() does not free the backend client -- s3ked already
handles that with _delete_quiet -- but not creating them is cheaper than
reclaiming them.
"""
import sys
sys.path.insert(0, "/home/lentferj/git-repos/s3ked")
from s3k import bridge as _b

CONFIG = "/home/lentferj/git-repos/s3ked/config.toml"

def connect(channels=(0,), **kw):
    return _b.S3kBridge.autodetect(channels=channels, config_path=CONFIG, **kw)
