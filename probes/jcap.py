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
"""One persistent JACK client for the whole run.

jack_rec spawns a fresh client per capture and this server has been refusing new
clients all day; a single client registered once avoids the whole problem.
"""
import queue, threading
import numpy as np
import jack

class Capture:
    def __init__(self, sources=("system:capture_13","system:capture_14"),
                 name="s3ked-probe"):
        self.client = jack.Client(name, no_start_server=True)
        self.ports = [self.client.inports.register(f"in_{i+1}")
                      for i in range(len(sources))]
        self._q = queue.Queue()
        self._on = threading.Event()
        @self.client.set_process_callback
        def _process(frames):
            if self._on.is_set():
                self._q.put([p.get_array().copy() for p in self.ports])
        self.client.activate()
        for src, port in zip(sources, self.ports):
            self.client.connect(src, port)
        self.samplerate = self.client.samplerate

    def start(self):
        while not self._q.empty():
            self._q.get_nowait()
        self._on.set()

    def stop(self):
        self._on.clear()
        chans = None
        while not self._q.empty():
            blk = self._q.get_nowait()
            if chans is None:
                chans = [[] for _ in blk]
            for i, a in enumerate(blk):
                chans[i].append(a)
        if not chans:
            return np.zeros(0)
        mono = np.mean([np.concatenate(c) for c in chans], axis=0)
        return mono * 32768.0          # match the int16 scale used elsewhere

    def stop_channels(self):
        """Like stop(), but WITHOUT summing -- one array per input port.

        A mono sum is exactly where a hard-panned L/R pair disappears, so a
        stereo test must not go through stop().
        """
        self._on.clear()
        chans = None
        while not self._q.empty():
            blk = self._q.get_nowait()
            if chans is None:
                chans = [[] for _ in blk]
            for i, a in enumerate(blk):
                chans[i].append(a)
        if not chans:
            return []
        return [np.concatenate(c) * 32768.0 for c in chans]

    def close(self):
        try:
            self.client.deactivate(); self.client.close()
        except Exception:
            pass
