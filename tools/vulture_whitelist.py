# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# vulture whitelist -- the dead-code findings that exist today and are NOT
# dead. Two entries. That is a short enough list to read end to end, which is
# the point: a whitelist entry is a claim, and a claim you cannot explain is a
# suppression wearing a comment.
#
# NOT whitelisted, on purpose: the Textual `action_*` / `on_*` / reactive
# accessors, roughly 40 of them. vulture cannot see a framework dispatch, and
# `--min-confidence 80` is what keeps them off the report at all -- at 60 the
# report is dominated by them and a real finding does not stand out. If the
# floor is ever lowered, those need `--ignore-decorators`, not 40 lines here.
#
# WHY tests/ IS NOT SCANNED (see the `audit` target in the Makefile):
#   1. vulture's `unreachable_code` findings carry NO NAME, so they cannot be
#      whitelisted by any means -- verified in vulture 2.14 core.py:166,
#      where `get_whitelist_string` returns a bare comment for that type
#      instead of a name. tests/test_app.py:294 has exactly one
#      ("unsatisfiable 'ternary' condition"), and with it in scope vulture
#      can never be green, so `make check` could never pass.
#   2. Four more findings there are the framework-dispatch false positive
#      that `--min-confidence 80` exists to suppress: pytest fixture
#      parameters (`unsettled`) look like unused arguments.
# Scanning tests/ would therefore mean either a permanently red check or a
# blanket `--ignore-names` that also hides real dead code. Excluding the
# directory is the honest trade. Dead code in the SHIPPED package and in the
# bench tooling -- where it actually costs -- is covered.

# --- probes/calibrate.py:141, probes/jcap.py:173 -----------------------------
# JACK client callbacks. The signature is fixed by the jack-python C API:
# every callback receives (status, frames, period_time) regardless of which
# two of those three the body uses. `nframes` and `delay` are the unused
# middle arguments. Renaming them `_nframes` would silence vulture by
# accident and cost the one thing that matters here -- the reader comparing
# these against the C header.
nframes
delay
