# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# Development checks. See the README's "Development checks" section for the
# workflow and for what each tool has been allowed to ignore.
#
# `make check` is the gate. Everything it runs is a tool configured in
# pyproject.toml (ruff, mypy, coverage) or invoked here with its scope named
# explicitly (vulture, deptry, detect-secrets). Nothing in this file fixes
# source. If a check fails, the fix is a decision, and the decision is yours.

PY      := .venv/bin/python
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/python -m pytest
VULTURE := .venv/bin/vulture
DEPTRY  := .venv/bin/deptry
PIP_AUDIT := .venv/bin/pip-audit
DSECRETS := .venv/bin/detect-secrets-hook

# Source trees, named. `deptry .` would walk .venv/ and report on virtualenv's
# own bundled wheels; vulture and deptry take the same list for the same
# reason. Naming them is the difference between checking this project and
# checking the tools that check it.
SRC := s3k s3ked probes tools tests

# The caches, the venv, and the build outputs. detect-secrets cannot read this
# out of pyproject.toml (see the note there), so it is repeated here where it
# demonstrably works.
DS_EXCLUDE := (^|/)(\.venv|build|dist|\.git|s3ked\.egg-info|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)/

# deptry does not read `per_rule_ignores` from pyproject.toml either. Verified
# in a scratch project: the suppression listed there was reported anyway, and
# the same suppression on the command line was honoured. One suppression is
# live -- `jack`, a rig-local capture backend used by two bench probes.
#
# It is NOT a packaging gap and NOT an end-user concern: nothing in s3k/ or
# s3ked/ imports it, the wheel contains neither it nor probes/, and
# tests/test_jcap.py installs a FAKE `jack` before importing the probe, so no
# CI job and no `make test` run can be broken by its absence. It is
# deliberately undeclared and must not become a core dependency. pyproject.toml
# has the full reasoning.
DEPTRY_IGNORES := DEP001=jack,DEP003=jack

# Which files the FORMATTER is applied to / checked on.
#
# `ruff format` would rewrite 57 of the 59 tracked files (5561 lines added,
# 4115 removed). It is therefore not enforced tree-wide: `make lint` and
# `make check` do not run it, and pre-commit only formats the files a commit
# actually touches. FORMAT_SCOPE is what "touches" means for a local run --
# everything differing from FORMAT_BASE.
FORMAT_BASE ?= $(shell git rev-parse --verify --quiet origin/main 2>/dev/null || \
                        git rev-parse --verify --quiet main 2>/dev/null || echo HEAD)
FORMAT_SCOPE ?= $(shell git diff --name-only --diff-filter=ACMR $(FORMAT_BASE) -- '*.py' 2>/dev/null)

.DEFAULT_GOAL := help
.PHONY: help setup lint format format-check typecheck test audit check clean \
        lint-fix secrets-scan

help:
	@echo "make setup          install the dev and checks extras into .venv"
	@echo "make lint           ruff check  (enforced)"
	@echo "make lint-fix       ruff check --fix, then re-check"
	@echo "make format         ruff format over FORMAT_SCOPE"
	@echo "make format-check   ruff format --check over FORMAT_SCOPE (reported, not gated)"
	@echo "make typecheck      mypy  (enforced)"
	@echo "make test           pytest with coverage, term-missing"
	@echo "make audit          pip-audit, vulture, deptry, detect-secrets  (enforced)"
	@echo "make check          lint, typecheck, audit, test -- the gate"
	@echo ""
	@echo "FORMAT_BASE  = $(FORMAT_BASE)"
	@echo "FORMAT_SCOPE = $(words $(FORMAT_SCOPE)) file(s)"

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,checks]"
	@command -v shellcheck >/dev/null 2>&1 || \
	  echo "note: shellcheck not on PATH; no shell scripts here, so nothing needs it"
	@command -v shfmt      >/dev/null 2>&1 || \
	  echo "note: shfmt not on PATH; no shell scripts here, so nothing needs it"

# --- ruff, lint -----------------------------------------------------------------
# Configured in pyproject.toml. ENFORCED on the whole tree, which is only
# possible because 1855 pre-existing findings are baselined rule-by-rule
# there, each with a count and a reason. A new finding in any baselined rule
# still fails here.
lint:
	$(RUFF) check .

lint-fix:
	$(RUFF) check . --fix
	$(RUFF) check .

# --- ruff, format --------------------------------------------------------------
# Applies to FORMAT_SCOPE only. See the note at the top of this file.
format:
	@if [ -z "$(FORMAT_SCOPE)" ]; then echo "format: nothing differs from $(FORMAT_BASE)"; else \
	  echo "format: $(words $(FORMAT_SCOPE)) file(s)"; $(RUFF) format $(FORMAT_SCOPE); fi

# Reported, NOT part of `check`. See README.
format-check:
	@if [ -z "$(FORMAT_SCOPE)" ]; then echo "format-check: nothing differs from $(FORMAT_BASE)"; else \
	  $(RUFF) format --check $(FORMAT_SCOPE) || \
	  echo "format-check: files above differ from ruff format. NOT a failure --"; \
	  echo "            the 57 pre-existing files are not formatted either."; \
	  echo "            'make format' applies it to these."; fi

# --- mypy ----------------------------------------------------------------------
# Configured in pyproject.toml. ENFORCED. 59 pre-existing findings are
# baselined as [[tool.mypy.overrides]] -- per module AND per error code, so a
# new error of a kind that already exists in that file is still caught.
typecheck:
	$(MYPY)

# --- pytest + coverage ----------------------------------------------------------
# No coverage threshold, deliberately: a threshold nobody agreed to gets
# raised to whatever today's number happens to be, which measures nothing.
# See pyproject.toml's [tool.coverage.*].
test:
	$(PYTEST) --cov

# --- audit ----------------------------------------------------------------------
# Four tools, run separately so a failure says which one failed.
audit: pip-audit vulture deptry secrets-scan

# Audits THIS PROJECT's dependency closure, not the ambient environment.
# This venv is --system-site-packages, so a bare `pip-audit` reports Debian's
# python3-webob and python3-zipp -- real advisories, but nothing to do with
# s3ked. Passing `.` makes pip-audit resolve the project's own declared
# dependencies: 10 packages, the two runtime deps plus the `checks` extra.
#
# vinsynlib is filtered out of the list handed to pip-audit. It is a sibling
# checkout rather than a package on an index (see [tool.uv.sources] in
# pyproject.toml), so pip cannot resolve it and pip-audit would fail
# *resolving* -- reporting nothing about anything, and failing the gate for a
# reason that is not a vulnerability. What it is instead is this family's own
# source, already reviewed in the repository it lives in, and at this layer it
# declares no third-party dependencies of its own.
#
# Auditing what is declared rather than what happens to be installed is the
# point of passing `.` in the first place, so the filter keeps that property:
# every other declared dependency is still audited, and a NEW dependency
# added later is audited without anyone editing this file.
AUDIT_REQS := .audit-runtime-req.txt
pip-audit:
	@echo "== pip-audit (project dependencies)"
	@$(PY) -c "import pathlib, tomllib; \
	    data = tomllib.load(open('pyproject.toml', 'rb')); \
	    pathlib.Path('$(AUDIT_REQS)').write_text(\
	        ''.join(r + chr(10) for r in data['project']['dependencies'] \
	        if not r.startswith('vinsynlib')))"
	@$(PIP_AUDIT) --progress-spinner off -r $(AUDIT_REQS) || \
	    (rm -f $(AUDIT_REQS); exit 1)
	@rm -f $(AUDIT_REQS)

# s3k s3ked probes tools -- tests is NOT scanned, and the whitelist says why:
# vulture's unreachable_code findings carry no name and so cannot be
# whitelisted at all, so one finding in tests/test_app.py would make vulture
# permanently unable to pass.
vulture:
	@echo "== vulture (dead code, min-confidence 80)"
	$(VULTURE) --min-confidence 80 s3k s3ked probes tools tools/vulture_whitelist.py

# tests IS in scope here, deliberately: excluding it is how a missing
# test-only dependency hides.
deptry:
	@echo "== deptry (unused / missing / transitive dependencies)"
	$(DEPTRY) --per-rule-ignores "$(DEPTRY_IGNORES)" $(SRC)

secrets-scan:
	@echo "== detect-secrets"
	$(DSECRETS) --baseline .secrets.baseline --exclude-files '$(DS_EXCLUDE)' $$(git ls-files)

# --- the gate -------------------------------------------------------------------
# Deliberately does NOT include format-check. That is the one deviation from
# "runs everything": the formatter cannot be enforced against a tree that has
# never been formatted, and this branch is not allowed to reformat it. Every
# other check here fails on a new finding.
check: lint typecheck audit test
	@echo ""
	@echo "check: lint, typecheck, audit and test all passed."
	@echo "       formatter status was reported by 'make format-check' and is not gated."

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache coverage.xml htmlcov .coverage
	rm -f $(AUDIT_REQS)
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +
