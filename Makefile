# The canonical invocations, shared by local runs and CI.
#
# Every command CI runs is a target here, so a pipeline run and a local run are
# the same command rather than two things that resemble each other.

PYTHON ?= python

# Every tracked Python file, enumerated from git rather than listed here: a
# hand-maintained list silently stops covering a directory the day someone
# forgets to add it.
# Deliberately '=' rather than ':=': evaluated when a recipe uses it, so
# `make ingest` and `make clean` do not shell out to git for nothing.
PY_FILES = $(shell git ls-files '*.py')

.PHONY: help lint test ingest ingest-dry clean

help:
	@echo "make lint        - pylint over every tracked .py file, gated at 10.00/10"
	@echo "make test        - unit tests against recorded fixtures, no network"
	@echo "make ingest      - pull new CI artifacts into the durable record"
	@echo "make ingest-dry  - parse everything, write nothing"
	@echo "make clean       - remove caches and build output"

lint:
	@echo "--- Running Pylint (gate: 10.00/10) ---"
# An empty file list makes pylint exit 0 with "No files to lint", which is a
# gate that passes by finding nothing - the same failure mode as an unparseable
# rcfile. Fail loudly instead. It happens for real: files are untracked until
# the first `git add`.
	@test -n "$(PY_FILES)" || { \
		echo "ERROR: git ls-files matched no Python files."; \
		echo "       Nothing would be linted, so this is a failure, not a pass."; \
		echo "       Run 'git add' first if the sources are still untracked."; \
		exit 1; \
	}
	@pylint --fail-under=10 $(PY_FILES)

test:
	@echo "--- Running unit tests (fixtures only, no network) ---"
	@$(PYTHON) -m pytest tests/ -v

ingest:
	@$(PYTHON) -m collector.ingest

ingest-dry:
	@$(PYTHON) -m collector.ingest --dry-run

clean:
	@rm -rf .pytest_cache build
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "Removed caches and build output. The data/ record is never touched."
