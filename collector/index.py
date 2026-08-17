"""Build the queryable SQLite index from the append-only NDJSON record.

The index is derived and disposable: it is rebuilt from scratch on every run and
is gitignored. That is what lets the inference in it - the synthesized
``not_run`` rows described below - be improved later without rewriting an
append-only history that is supposed to record only what the artifacts said.

Nothing here reads the network. Everything comes from ``data/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from collector.store import ANOMALIES_SUFFIX, DEFAULT_DATA_DIR, RUNS_SUFFIX

LOGGER = logging.getLogger(__name__)

#: Location of the derived index. Gitignored - see readme section 4.
DEFAULT_INDEX_PATH: Final[Path] = Path("build") / "results.sqlite"

#: Status assigned to a test that did not appear in a run it was expected in.
NOT_RUN: Final[str] = "not_run"

#: Marks rows this module inferred rather than read from an artifact.
DERIVED_FORMAT: Final[str] = "derived"

SCHEMA: Final[str] = """
CREATE TABLE runs (
    repo TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    workflow TEXT,
    event TEXT,
    head_sha TEXT,
    status TEXT,
    conclusion TEXT,
    started_at TEXT,
    updated_at TEXT,
    sut_version TEXT,
    PRIMARY KEY (repo, run_id, attempt)
);

CREATE TABLE results (
    repo TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    test_uid TEXT NOT NULL,
    test_id TEXT,
    identity TEXT NOT NULL,
    module TEXT,
    test_name TEXT,
    display_name TEXT,
    params TEXT,
    params_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    message TEXT,
    trace TEXT,
    has_trace INTEGER,
    labels TEXT,
    source_format TEXT,
    has_steps INTEGER,
    PRIMARY KEY (repo, run_id, attempt, test_uid, params_hash)
);

CREATE TABLE steps (
    repo TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    test_uid TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    name TEXT,
    status TEXT,
    duration_ms INTEGER,
    parent_ordinal INTEGER
);

CREATE TABLE anomalies (
    repo TEXT NOT NULL,
    artifact_id INTEGER,
    artifact_name TEXT,
    created_at TEXT,
    reason TEXT,
    detail TEXT,
    glob TEXT,
    parser TEXT,
    run_id INTEGER,
    run_conclusion TEXT
);

CREATE INDEX idx_results_identity ON results (repo, identity);
CREATE INDEX idx_results_run ON results (repo, run_id);
CREATE INDEX idx_steps_test ON steps (repo, test_uid);
"""


def read_partitions(data_dir: Path, suffix: str | None) -> list[dict[str, Any]]:
    """Read every NDJSON partition of one kind.

    Args:
        data_dir: Root of the durable record.
        suffix: Partition suffix to read, or None for result partitions, which
            are identified by carrying neither of the other two suffixes.

    Returns:
        Every decodable row across all repositories and months.
    """
    rows: list[dict[str, Any]] = []
    for path in sorted(data_dir.rglob("*.ndjson")):
        is_runs = path.name.endswith(RUNS_SUFFIX)
        is_anomalies = path.name.endswith(ANOMALIES_SUFFIX)
        if suffix == RUNS_SUFFIX and not is_runs:
            continue
        if suffix == ANOMALIES_SUFFIX and not is_anomalies:
            continue
        if suffix is None and (is_runs or is_anomalies):
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def synthesize_not_run(
    results: list[dict[str, Any]], runs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Infer rows for tests absent from runs they were expected to appear in.

    A test missing from a run is a fact worth recording, not an omission. These
    suites deselect by marker routinely - ``-m "not external"``, ``--env=weather``
    - so without this, "never failed" is indistinguishable from "has not run
    since May", and a rate computed over runs where the test happened to appear
    silently uses a different denominator per test.

    Synthesis is bounded by each test's **observed lifetime**: a row is created
    only for runs falling between that test's first and last appearance in its
    repository. Outside that window the test either did not exist yet or has
    been deleted, and filling those in would manufacture history rather than
    record its absence.

    Args:
        results: Every observed result row.
        runs: Every run row, used for chronological ordering.

    Returns:
        Synthesized rows carrying ``status='not_run'`` and
        ``source_format='derived'``.
    """
    observed = _observations(results, runs)
    synthesized: list[dict[str, Any]] = []
    for (repo, test_uid), stamps in observed.appearances.items():
        first, last = min(stamps), max(stamps)
        for run_id in observed.runs_with_results[repo]:
            when = observed.started.get((repo, run_id), "")
            if not first <= when <= last or (repo, run_id, test_uid) in observed.seen:
                continue
            synthesized.append(
                _not_run_row(observed.template[test_uid], repo, run_id, test_uid)
            )
    return synthesized


@dataclass(frozen=True)
class _Observations:
    """Lookup tables derived once from the record, for absence detection.

    Attributes:
        started: Run start time by repository and run id.
        runs_with_results: Runs that reported at least one result, per
            repository. A run whose artifact was empty proves nothing about
            which tests ran, so it is not a candidate for absence.
        seen: Every repository, run and test actually observed together.
        template: One representative row per test, used to carry stable fields
            such as module and assigned ID onto a synthesized row.
        appearances: Run start times at which each test was observed, which
            bound its lifetime.
    """

    started: dict[tuple[str, int], str]
    runs_with_results: dict[str, set[int]]
    seen: set[tuple[str, int, str]]
    template: dict[str, dict[str, Any]]
    appearances: dict[tuple[str, str], list[str]]


def _observations(results: list[dict[str, Any]], runs: list[dict[str, Any]]) -> _Observations:
    """Build the lookup tables absence detection needs, in one pass.

    Args:
        results: Every observed result row.
        runs: Every run row.

    Returns:
        The populated lookup tables.
    """
    started = {(run["repo"], run["run_id"]): run.get("started_at") or "" for run in runs}
    runs_with_results: dict[str, set[int]] = defaultdict(set)
    seen: set[tuple[str, int, str]] = set()
    template: dict[str, dict[str, Any]] = {}
    appearances: dict[tuple[str, str], list[str]] = defaultdict(list)

    for row in results:
        repo, run_id, test_uid = row["repo"], row["run_id"], row["test_uid"]
        runs_with_results[repo].add(run_id)
        seen.add((repo, run_id, test_uid))
        template.setdefault(test_uid, row)
        appearances[(repo, test_uid)].append(started.get((repo, run_id), ""))

    return _Observations(started, runs_with_results, seen, template, appearances)


def _not_run_row(
    source: dict[str, Any], repo: str, run_id: int, test_uid: str
) -> dict[str, Any]:
    """Build one synthesized absence row.

    Args:
        source: A representative observed row for this test, for its stable
            descriptive fields.
        repo: Repository the absence belongs to.
        run_id: Run the test did not appear in.
        test_uid: The absent test.

    Returns:
        A result row carrying ``not_run`` and ``derived``.
    """
    return {
        "repo": repo,
        "run_id": run_id,
        "attempt": 1,
        "test_uid": test_uid,
        "test_id": source.get("test_id"),
        "module": source.get("module"),
        "test_name": source.get("test_name"),
        "display_name": source.get("display_name"),
        "params": {},
        "params_hash": "not_run",
        "status": NOT_RUN,
        "duration_ms": None,
        "message": None,
        "trace": None,
        "has_trace": False,
        "labels": {},
        "source_format": DERIVED_FORMAT,
        "has_steps": False,
        "steps": [],
    }


def build(data_dir: Path, index_path: Path) -> dict[str, int]:
    """Rebuild the index from the durable record.

    Args:
        data_dir: Root of the durable record.
        index_path: Where to write the SQLite file. Replaced if it exists.

    Returns:
        Row counts per table, for the caller to report.
    """
    results = read_partitions(data_dir, None)
    runs = read_partitions(data_dir, RUNS_SUFFIX)
    anomalies = read_partitions(data_dir, ANOMALIES_SUFFIX)
    derived = synthesize_not_run(results, runs)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index_path.unlink()

    connection = sqlite3.connect(index_path)
    try:
        connection.executescript(SCHEMA)
        _insert_runs(connection, runs)
        _insert_results(connection, results + derived)
        _insert_steps(connection, results)
        _insert_anomalies(connection, anomalies)
        connection.commit()
    finally:
        connection.close()

    return {
        "runs": len(runs),
        "observed": len(results),
        "not_run": len(derived),
        "steps": sum(len(row.get("steps") or []) for row in results),
        "anomalies": len(anomalies),
    }


def _insert_runs(connection: sqlite3.Connection, runs: list[dict[str, Any]]) -> None:
    """Insert run rows.

    Args:
        connection: Open database connection.
        runs: Run rows from the record.
    """
    connection.executemany(
        "INSERT OR REPLACE INTO runs VALUES (:repo, :run_id, :attempt, :workflow, :event,"
        " :head_sha, :status, :conclusion, :started_at, :updated_at, :sut_version)",
        runs,
    )


def _insert_results(connection: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    """Insert result rows, computing the reporting identity for each.

    ``identity`` is ``COALESCE(test_id, test_uid)``, materialized as a column so
    every report groups the same way without repeating the expression. Rows
    backfilled before assigned IDs existed fall back to ``test_uid``, which is
    what stitches pre-ID history to post-ID history.

    Args:
        connection: Open database connection.
        results: Observed and synthesized result rows.
    """
    payload = [
        {
            **row,
            "identity": row.get("test_id") or row["test_uid"],
            "params": json.dumps(row.get("params") or {}, sort_keys=True),
            "labels": json.dumps(row.get("labels") or {}, sort_keys=True),
            "has_trace": int(bool(row.get("has_trace"))),
            "has_steps": int(bool(row.get("has_steps"))),
        }
        for row in results
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO results VALUES (:repo, :run_id, :attempt, :test_uid, :test_id,"
        " :identity, :module, :test_name, :display_name, :params, :params_hash, :status,"
        " :duration_ms, :message, :trace, :has_trace, :labels, :source_format, :has_steps)",
        payload,
    )


def _insert_steps(connection: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    """Flatten and insert recorded steps.

    Args:
        connection: Open database connection.
        results: Observed result rows, whose ``steps`` arrays are unpacked.
    """
    payload = [
        {
            "repo": row["repo"],
            "run_id": row["run_id"],
            "test_uid": row["test_uid"],
            "params_hash": row["params_hash"],
            **step,
        }
        for row in results
        for step in (row.get("steps") or [])
    ]
    connection.executemany(
        "INSERT INTO steps VALUES (:repo, :run_id, :test_uid, :params_hash, :ordinal, :name,"
        " :status, :duration_ms, :parent_ordinal)",
        payload,
    )


def _insert_anomalies(connection: sqlite3.Connection, anomalies: list[dict[str, Any]]) -> None:
    """Insert anomaly rows.

    Args:
        connection: Open database connection.
        anomalies: Anomaly rows from the record.
    """
    payload = [
        {**row, "detail": json.dumps(row.get("detail") or {}, sort_keys=True)}
        for row in anomalies
    ]
    connection.executemany(
        "INSERT INTO anomalies VALUES (:repo, :artifact_id, :artifact_name, :created_at,"
        " :reason, :detail, :glob, :parser, :run_id, :run_conclusion)",
        payload,
    )


def main(argv: list[str] | None = None) -> int:
    """Rebuild the index from the command line.

    Args:
        argv: Argument vector, defaulting to ``sys.argv``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m collector.index",
        description="Rebuild the derived SQLite index from the durable NDJSON record.",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.data_dir.exists():
        LOGGER.error("No record at %s. Run `make ingest` first.", args.data_dir)
        return 1

    counts = build(args.data_dir, args.index)
    LOGGER.info("Built %s", args.index)
    for table, count in counts.items():
        LOGGER.info("  %-10s %d", table, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
