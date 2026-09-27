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
"""Pre-release scan for commercial library names -- built because the scan is
the component that breaks, not the repository.

CLAUDE.md forbids committing the name of any program, sample or bank from a
commercial library.  Checking that before a push is a two-line grep, and on
2026-09-23 two sessions ran that two-line grep and BOTH of them were wrong,
in three different ways, while every one of them printed a clean result:

  1. `grep` on this box is a shell function wrapping `ugrep --ignore-files`,
     so a recursive scan silently skips every gitignored path -- including
     CLAUDE.md itself.  Six files were never opened.  (Harmless here only
     because gitignored files cannot be pushed, so tracked-only was the
     right scope by accident rather than by choice.)
  2. The re-check used `grep -c ... || echo 0`.  grep exits 1 on zero
     matches, so BOTH sides fired and the count became "0\\n0"; every term
     printed as a hit with a count of zero.  Nonsense formatted as a finding,
     inside the check written to verify a check.
  3. A peer's scan reported 816 hits, then 11, then 3, across three runs of
     the same tree -- all artefacts of the pattern ("ROM)" split out of a
     CD-ROM mention; "VEL SAMP" matching "leVEL SAMPlevolume").  Its positive
     control passed every time, because the machinery was fine and the
     pattern was wrong.

Those are three separate failure modes and no single guard catches them, so
this refuses to report clean unless all three are excluded:

  * it COUNTS THE BYTES IT OPENED, which catches a scan that read nothing;
  * it asserts a POSITIVE CONTROL that must hit, which catches a pattern or
    a decoder that could never hit anything;
  * it PRINTS EVERY HIT in context, which catches a pattern that hits
    everything -- and is the leg neither session had.  For a release scan
    that leg is not the minor one: a false positive reads as "commercial
    names found the night before a push", and that does not fail safe.

An unsound scan -- including one that refuses to start -- exits 2,
never 0.

THE TERM LIST CANNOT LIVE IN THIS REPOSITORY.  A checked-in list of
commercial library names is itself a checked-in list of commercial library
names, so --terms takes a path and this refuses a path that git tracks.
Keep it beside the id->name map, outside the tree or gitignored.

Scans the bytes of tracked files (a scrubbed filename does not scrub a
payload) and the commit messages in a revision range.

  probes/relscan.py --terms ~/.local/share/s3ked/libnames.txt
  probes/relscan.py --terms FILE --rev origin/main..HEAD
"""

import argparse
import re
import subprocess
import sys
import traceback

# A term the tree MUST contain.  If this does not hit, the scan proved nothing
# about the terms that did not hit either.
DEFAULT_CONTROL = "keygroup"

MAX_CONTEXT = 120


def make_output_encoding_independent():
    """Print hits whatever the console's codepage happens to be.

    A hit line is decoded binary, so it carries U+FFFD, and a cp1252 stdout
    (Windows' default) raises UnicodeEncodeError on it.  That killed the
    process AFTER the five header lines and BEFORE a single hit line, on
    2026-09-26 CI -- the one outcome this design forbids, because an uncaught
    exception exits 1 and 1 is "hits found, go read them below".  A reader
    gets a count, an instruction to read the hits, and no hits.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):   # not a reconfigurable stream
            pass


def git(*args):
    return subprocess.run(("git",) + args, capture_output=True, check=True).stdout


def load_terms(path):
    """Read the term list, refusing to read one that git tracks."""
    tracked = subprocess.run(("git", "ls-files", "--error-unmatch", path),
                             capture_output=True)
    if tracked.returncode == 0:
        print("refusing: %s is tracked by git -- a committed list of "
              "commercial names is the thing this scan exists to prevent.  "
              "Keep it outside the tree." % path, file=sys.stderr)
        sys.exit(2)
    with open(path, "rb") as fh:
        raw = fh.read()
    terms = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def compile_term(term):
    """Word-boundary where the edges are word characters, so a term cannot
    match inside a longer word (the "leVEL SAMPlevolume" artefact)."""
    pat = re.escape(term)
    if re.match(r"\w", term):
        pat = r"\b" + pat
    if re.search(r"\w$", term):
        pat = pat + r"\b"
    return re.compile(pat.encode("utf-8"), re.IGNORECASE)


def scan(blob, origin, patterns, hits):
    for line_no, line in enumerate(blob.splitlines(), 1):
        for term, pat in patterns:
            if pat.search(line):
                text = line.decode("utf-8", "replace")
                if len(text) > MAX_CONTEXT:
                    text = text[:MAX_CONTEXT] + "..."
                hits.append((term, origin, line_no, text))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--terms", required=True,
                    help="file of names to scan for, one per line, NOT tracked")
    ap.add_argument("--control", default=DEFAULT_CONTROL,
                    help="a term the tree must contain (default: %(default)s)")
    ap.add_argument("--rev", default="HEAD",
                    help="commit range whose messages to scan (default: all)")
    args = ap.parse_args()

    make_output_encoding_independent()
    terms = load_terms(args.terms)
    if not terms:
        print("refusing: %s holds no terms" % args.terms, file=sys.stderr)
        return 2
    patterns = [(t, compile_term(t)) for t in terms]
    control = (args.control, compile_term(args.control))

    files = [f for f in git("ls-files", "-z").split(b"\0") if f]
    hits, control_hits = [], []
    opened = read = 0
    for name in files:
        try:
            with open(name, "rb") as fh:
                blob = fh.read()
        except OSError as exc:            # a tracked path can be absent
            print("UNREAD %s: %s" % (name.decode(errors="replace"), exc))
            continue
        opened += 1
        read += len(blob)
        origin = name.decode("utf-8", "replace")
        scan(blob, origin, patterns, hits)
        scan(blob, origin, [control], control_hits)

    msgs = git("log", "--format=%B", args.rev)
    scan(msgs, "commit messages (%s)" % args.rev, patterns, hits)
    scan(msgs, "commit messages (%s)" % args.rev, [control], control_hits)

    print("terms      %5d from %s" % (len(terms), args.terms))
    print("files      %5d opened of %d tracked, %d bytes"
          % (opened, len(files), read))
    print("messages   %5d bytes over %s" % (len(msgs), args.rev))
    print("control    %5d hits for %r" % (len(control_hits), args.control))
    print("hits       %5d" % len(hits))

    unsound = []
    if opened == 0:
        unsound.append("opened no files")
    if opened != len(files):
        unsound.append("opened %d of %d tracked files" % (opened, len(files)))
    if not control_hits:
        unsound.append("control %r never matched -- the pattern or the "
                       "decoder cannot hit anything" % args.control)
    if unsound:
        print("\nUNSOUND: " + "; ".join(unsound))
        print("This run proves nothing about the terms that did not hit.")
        return 2

    if hits:
        # Print EVERY hit.  Reading them is the only thing that separates a
        # real name from an artefact of the pattern, and a release scan that
        # cries wolf the night before a push is worse than no scan.
        print("\nRead every line below before concluding anything:")
        for term, origin, line_no, text in hits:
            print("  %s:%d  [%s]  %s" % (origin, line_no, term, text))
        return 1

    print("\nclean")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        # NOT 1.  1 is this tool's "hits found, read them below", so a crash
        # exiting 1 wears the code of a result it never produced.  An
        # unexpected failure proved nothing, which is what 2 means here --
        # and an interrupted scan is unsound for the same reason.
        sys.exit(2)
