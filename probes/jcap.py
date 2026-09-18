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

THREE DEFECTS FIXED 2026-09-18, after a wedged server cost the bench an evening.

**The constructor leaked an activated client.** ``activate()`` ran before
``connect()`` with nothing between them catching a failure, so a connect that
raised -- a missing source port, a busy server -- left the client *registered
and activated* with the half-built object discarded and no reference left to
close it. To jackd a leaked client and a live one are the same thing: it keeps
trying to write to the socket. Every teardown path is now guaranteed.

**There was no xrun detection.** A dropout silently shortens a capture, and
every number measured through this class assumes the frames are contiguous.
They are counted now and :meth:`stop_channels` reports them.

**The queue was unbounded.** A capture nobody drains grew until memory ran out,
and the RT callback kept enqueueing regardless. It is bounded by a stated
duration, and overflow is recorded rather than silently dropped.

WHY THIS CLASS HIDES, from three siblings who each found it in their own rig
on the same evening:

* **An asymmetric teardown is harder to spot than none at all** (eosed). The
  module they share registers an ``atexit`` hook for the MIDI port and none for
  the recorder, so MIDI was released cleanly every single time while the audio
  client leaked. *Nobody goes looking for a missing hook next to one that is
  plainly present and working.*
* **The error you will actually meet IS an ``Exception``** (k2kremote). Their
  connect path raises ``JackErrorCode``, which ``except Exception`` catches
  perfectly -- so the guard looks correct forever and fails only on the
  timeout or the Ctrl-C, which is the case that matters.
* **A shell ``timeout`` is not a deadline, it is the kill that leaks.** Fifteen
  scripts and about forty captures ran under one that evening; the in-process
  teardown never ran once.

AND THERE IS NO RECOVERY ONCE A CLIENT IS ORPHANED -- only a server restart.
Worth stating because it is the obvious thing to reach for and it cannot be
built. eosed wrote an orphan-recovery path, described it as working, and
withdrew it; both halves are refuted by the binding itself, with no server
needed to see it:

* ``jack.Client(name)`` defaults to ``use_exact_name=False``, and the docstring
  says the server "will modify this name to create a unique variant, if
  needed". Reconnecting by an orphan's name therefore registers ``name-01``,
  closes *that*, and leaves the orphan untouched -- raising nothing, returning
  normally, looking like it worked.
* ``close(ignore_errors=True)`` and ``deactivate(ignore_errors=True)`` take no
  argument naming another client, and the API has no call that closes a foreign
  registration at all.

So the teardown in this file is the only defence there is: if it does not run,
nothing later can clean up after it. That is the whole reason it catches
``BaseException``.
"""
import queue, threading, warnings
import numpy as np
import jack

#: Longest capture the queue will hold before it starts refusing frames, in
#: seconds. Generous -- the longest probe in this project holds a note 45 s --
#: but finite, so a run nobody drains fails loudly instead of eating the box.
MAX_CAPTURE_SECONDS = 300.0


class CaptureError(RuntimeError):
    """Raised when a capture cannot be trusted, rather than returned quietly."""


class Capture:
    def __init__(self, sources=("system:capture_13", "system:capture_14"),
                 name="s3ked-probe", max_seconds=MAX_CAPTURE_SECONDS):
        self.client = None
        self.ports = []
        self.xruns = 0
        self.overflows = 0
        self._closed = False
        try:
            self.client = jack.Client(name, no_start_server=True)
            self.ports = [self.client.inports.register(f"in_{i+1}")
                          for i in range(len(sources))]
            self.samplerate = self.client.samplerate
            # Bound the queue in FRAMES-worth of blocks, not in blocks, so the
            # limit means the same thing at any buffer size.
            per_block = max(1, self.client.blocksize)
            self._maxblocks = max(1, int(max_seconds * self.samplerate / per_block))
            self._q = queue.Queue(maxsize=self._maxblocks)
            self._on = threading.Event()

            @self.client.set_process_callback
            def _process(frames):
                if self._on.is_set():
                    try:
                        self._q.put_nowait([p.get_array().copy() for p in self.ports])
                    except queue.Full:
                        # Never block the RT thread. Count it; the capture is
                        # no longer contiguous and stop_channels() will say so.
                        self.overflows += 1

            @self.client.set_xrun_callback
            def _xrun(delay):
                self.xruns += 1

            self.client.activate()
            for src, port in zip(sources, self.ports):
                self.client.connect(src, port)
        except BaseException:
            # BaseException, not Exception: a KeyboardInterrupt or a SystemExit
            # during construction leaked exactly as loudly as a ConnectionError,
            # and a killed probe is how this was found.
            self.close()
            raise

    # -- context manager, so the teardown cannot be forgotten ---------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def start(self):
        while not self._q.empty():
            self._q.get_nowait()
        self.xruns = 0
        self.overflows = 0
        self._on.set()

    def _drain(self):
        self._on.clear()
        chans = None
        while not self._q.empty():
            blk = self._q.get_nowait()
            if chans is None:
                chans = [[] for _ in blk]
            for i, a in enumerate(blk):
                chans[i].append(a)
        return chans

    def _check(self):
        """Warn once, loudly, if the capture is not contiguous."""
        if self.xruns or self.overflows:
            warnings.warn(
                f"capture is NOT contiguous: {self.xruns} xrun(s), "
                f"{self.overflows} dropped block(s) -- any timing measured "
                f"from it is wrong by an unknown amount",
                stacklevel=3)

    def stop(self):
        chans = self._drain()
        self._check()
        if not chans:
            return np.zeros(0)
        mono = np.mean([np.concatenate(c) for c in chans], axis=0)
        return mono * 32768.0          # match the int16 scale used elsewhere

    def stop_channels(self):
        """Like stop(), but WITHOUT summing -- one array per input port.

        A mono sum is exactly where a hard-panned L/R pair disappears, so a
        stereo test must not go through stop().
        """
        chans = self._drain()
        self._check()
        if not chans:
            return []
        return [np.concatenate(c) * 32768.0 for c in chans]

    def close(self):
        """Idempotent, and safe to call on a half-built client."""
        if self._closed:
            return
        self._closed = True
        if self.client is None:
            return
        try:
            self.client.deactivate()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        self.client = None
