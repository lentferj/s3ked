# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.

"""Guards on the pre-release commercial-name scan.

The scan's whole job is to be trusted unattended the night before a push, so
the property that matters is not that it finds names -- it is that it
*cannot report clean unsoundly*.  On 2026-09-23 two sessions ran the two-line
grep version and all three of its failure modes printed a clean-looking
result: a file scope that skipped six files, a `grep -c ... || echo 0` that
turned every term into a zero-count "hit", and a peer's pattern that returned
816 then 11 then 3 hits on one unchanged tree.

So each test here is one of those failure modes, plus the two rules from
CLAUDE.md that a naive grep gets wrong: a scrubbed filename does not scrub
the bytes underneath it, and a name that never reached a file can still be
sitting in a commit message.

Every name below is invented.  That is not incidental -- a fixture of real
library names would be the exact thing the scanner exists to keep out of the
tree, which is also why the scanner refuses a --terms file that git tracks.
"""

import importlib.util
import os
import re
import subprocess
import sys

RELSCAN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "probes", "relscan.py")

# Imported by path: probes/ is bench tooling and is not a package.  The
# boundary test below must call the REAL compile_term -- rebuilding the
# pattern here would check the tool against a model of itself, which is the
# one thing a regression test must never do.
_spec = importlib.util.spec_from_file_location("relscan", RELSCAN)
relscan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relscan)

# Invented.  Generic, and deliberately not near any real instrument trademark.
NAMES = ["Zarquon Bass", "Fnord Pad"]

# The word-boundary fixture, shared by the scan test and the test-of-the-test
# below SO THAT IT CANNOT BE COLLAPSED in one place and left pinned in the
# other.  One string per edge: each is excluded by exactly one anchor.
LEAD_ONLY = "supervel samp follows"        # only the boundary BEFORE excludes
TRAIL_ONLY = "the vel samplevolume field"  # only the boundary AFTER  excludes
BOTH_AT_ONCE = "a level samplevolume field"  # excluded twice over -- inert


def make_repo(tmp_path, files, message="initial"):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    for name, blob in files.items():
        path = repo / name
        path.write_bytes(blob if isinstance(blob, bytes) else blob.encode())
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)
    return repo, env


def run(repo, env, terms_path, *extra):
    return subprocess.run([sys.executable, RELSCAN, "--terms", str(terms_path),
                           "--control", "keygroup", *extra],
                          cwd=repo, env=env, capture_output=True, text=True)


def write_terms(tmp_path, names=NAMES):
    # OUTSIDE the repo, so git cannot track it -- which is the point.
    path = tmp_path / "terms.txt"
    path.write_text("# invented\n" + "\n".join(names) + "\n", encoding="utf-8")
    return path


def test_a_clean_tree_is_clean(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "a keygroup and nothing else\n"})
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean" in r.stdout


def test_a_name_in_a_file_is_found_and_printed(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "keygroup 3 uses Zarquon Bass\n"})
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 1
    # Printing the hit is the leg that separates a real name from an artefact
    # of the pattern.  A bare count cannot be read.
    assert "Zarquon Bass" in r.stdout
    assert "a.md:1" in r.stdout


def test_a_name_only_in_a_commit_message_is_found(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "keygroup\n"},
                          message="import Fnord Pad")
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 1
    assert "commit messages" in r.stdout


def test_a_scrubbed_filename_does_not_scrub_the_payload(tmp_path):
    # CLAUDE.md: captured binaries embed names in their bytes, so renaming the
    # file is not enough.  Scanning decoded text would miss this.
    blob = b"\x00\x01keygroup\x00\xff" + "Zarquon Bass".encode() + b"\x00\xfe"
    repo, env = make_repo(tmp_path, {"dump001.bin": blob})
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 1
    assert "dump001.bin" in r.stdout


def test_hits_print_on_a_console_that_cannot_encode_them(tmp_path):
    """The Windows CI failure of 2026-09-26, reproduced in 0.9 s on Linux.

    A hit line is decoded binary and carries U+FFFD.  With a cp1252 stdout
    `print` raised UnicodeEncodeError after the header and before the first
    hit, and an uncaught exception exits 1 -- which is this tool's "hits
    found, read them below".  The count said one hit, the instruction said
    read it, and nothing was there to read.

    Nine tests ran the scanner and every one of them inherited a UTF-8
    stdout, so none could see it.  The encoding of the terminal is an input.
    """
    blob = b"\x00\x01keygroup\x00\xff" + "Zarquon Bass".encode() + b"\x00\xfe"
    repo, env = make_repo(tmp_path, {"dump001.bin": blob})
    env = dict(env, PYTHONIOENCODING="cp1252")
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "dump001.bin" in r.stdout
    assert "Zarquon Bass" in r.stdout


def test_an_unexpected_failure_exits_2_and_never_1(tmp_path):
    """1 is a RESULT here, so nothing that failed may wear it.

    --rev with a revision git cannot resolve raises CalledProcessError out of
    the middle of the scan.  Before the guard that exited 1, indistinguishable
    from a clean run that found one name.
    """
    repo, env = make_repo(tmp_path, {"a.md": "a keygroup and nothing else\n"})
    r = run(repo, env, write_terms(tmp_path), "--rev", "no/such/revision")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "clean" not in r.stdout


def test_a_term_inside_a_longer_word_does_not_match(tmp_path):
    """The peer's 816-then-11-then-3 failure: 'VEL SAMP' matching
    'leVEL SAMPlevolume'.  A hit that is not a word is an artefact."""
    # Each edge needs its OWN string, or the test passes with that edge
    # deleted.  Mutation-tested on 2026-09-24, which is how two earlier
    # versions of this fixture were caught being inert:
    #   " vel samplevolume"  boundary before is fine, only the one AFTER
    #                        excludes it  -> isolates the trailing \b
    #   "supervel samp "     boundary after is fine, only the one BEFORE
    #                        excludes it  -> isolates the leading \b
    # The first attempt wrote "levelsamplevolume" with no space, which a
    # two-word term cannot match on any setting, so it tested nothing at all.
    repo, env = make_repo(tmp_path, {
        "a.md": "keygroup %s %s zarquonbassline\n" % (LEAD_ONLY, TRAIL_ONLY)})
    terms = write_terms(tmp_path, ["vel samp", "Zarquon Bass"])
    r = run(repo, env, terms)
    assert r.returncode == 0, r.stdout


def test_the_word_boundary_fixture_isolates_one_edge_each():
    """A test of the test above, kept because the obvious fixture is inert.

    The natural thing to write is ONE string containing the term inside a
    longer word -- `level samplevolume` for `vel samp`.  It is excluded by
    BOTH anchors at once, so the test stays green with either one deleted and
    proves nothing about the pair.  That is not hypothetical: it is what was
    written here first, and mutation-testing on 2026-09-24 is the only reason
    it did not ship.  The peer independently wrote the same inert fixture.

    So the fixture needs one string per edge, and this pins that property --
    otherwise someone simplifies the two strings back into one and the guard
    silently stops guarding.
    """
    term = relscan.compile_term("vel samp")
    lead_only, trail_only, both_at_once = (
        LEAD_ONLY.encode(), TRAIL_ONLY.encode(), BOTH_AT_ONCE.encode())

    # Each fixture string must be excluded by the real pattern...
    assert not term.search(lead_only)
    assert not term.search(trail_only)
    assert not term.search(both_at_once)

    # ...but by DIFFERENT anchors, which is what one string cannot show.
    lead = re.compile(rb"\bvel\ samp")
    trail = re.compile(rb"vel\ samp\b")
    assert not lead.search(lead_only) and trail.search(lead_only)
    assert lead.search(trail_only) and not trail.search(trail_only)
    # The trap string is caught by either anchor alone -- hence inert.
    assert not lead.search(both_at_once) and not trail.search(both_at_once)


def test_a_control_that_cannot_hit_is_unsound_not_clean(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "keygroup\n"})
    r = subprocess.run([sys.executable, RELSCAN, "--terms", str(write_terms(tmp_path)),
                        "--control", "Xyzzynought"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 2
    assert "UNSOUND" in r.stdout
    assert "clean" not in r.stdout


def test_a_file_that_cannot_be_opened_is_unsound_not_clean(tmp_path):
    """A scan that reads nothing reads nothing clean.  Counting the files it
    opened against the files git lists is what catches it."""
    repo, env = make_repo(tmp_path, {"a.md": "keygroup\n", "b.md": "keygroup\n"})
    os.unlink(repo / "b.md")          # still tracked, no longer readable
    r = run(repo, env, write_terms(tmp_path))
    assert r.returncode == 2
    assert "UNSOUND" in r.stdout
    assert "clean" not in r.stdout


def test_it_refuses_a_terms_file_the_repository_tracks(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "keygroup\n", "names.txt": "Fnord Pad\n"})
    r = run(repo, env, repo / "names.txt")
    assert r.returncode == 2
    assert "tracked by git" in r.stderr


def test_it_refuses_an_empty_terms_file(tmp_path):
    repo, env = make_repo(tmp_path, {"a.md": "keygroup\n"})
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing but a comment\n", encoding="utf-8")
    r = run(repo, env, empty)
    assert r.returncode == 2
