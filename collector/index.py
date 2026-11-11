"""Build the queryable SQLite index from the append-only NDJSON record.

Created: 2026-08-16
Last updated: 2026-11-11

This file contains the command line ``python -m collector.index`` (``main``);
``build``, which rebuilds the index; ``read_partitions``, which reads one kind
of partition; ``synthesize_not_run``, which infers absence rows; the private
insert functions, one per table; and ``SCHEMA``, which defines the ``runs``,
``results``, ``steps`` and ``anomalies`` tables.

It runs after ingestion and before the reports (``make db``), reads nothing but
``data/``, and replaces ``build/results.sqlite`` from scratch every time. Every
report reads only this index, so a figure that is wrong here is wrong on the
published page; an assigned ID duplicated in the latest run stops the build
before any report can run.

**Absence is inferred here, not recorded.** A test missing from a run inside its
own observed lifetime gets a ``not_run`` row with ``source_format='derived'``,
and only runs that reported at least one result are candidates. These rows
exist only in the index; the NDJSON record keeps what the artifacts said.

**Identity is materialized as a column.** An assigned ID observed on any row
for a ``test_uid`` becomes that test's ``identity`` on every row, including
rows recorded before the ID existed; a test with no ID keeps its ``test_uid``.
Two ``test_uid`` values claiming one ID in different runs are a rename and
share the ID's history. Two claiming it in the same run are a duplicate: the
older is kept apart under its own ``test_uid`` and logged, and a duplicate
still present in the latest run raises ``ValueError``.

Specified by ``DESIGN.md`` sections 5, 6 and 7.
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

from collector.store import ANOMALIES_SUFFIX, DEFAULT_DATA_DIR, RESULTS_SUFFIX, RUNS_SUFFIX

LOGGER = logging.getLogger(__name__)

#: Location of the derived index. Gitignored - see readme section 4.
DEFAULT_INDEX_PATH: Final[Path] = Path("build") / "results.sqlite"

#: Status assigned to a test that did not appear in a run it was expected in.
NOT_RUN: Final[str] = "not_run"

#: Marks rows this module inferred rather than read from an artifact.
DERIVED_FORMAT: Final[str] = "derived"

#: The index's tables and indexes, created on an empty database each build.
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

    A file is read only when its name ends with the wanted suffix, so a new
    kind of partition is never read as results. Design: ``DESIGN.md``
    section 5.

    Args:
        data_dir (Path): Root of the durable record.
        suffix (str | None): Partition suffix to read, or None for result
            partitions.

    Returns:
        list[dict[str, Any]]: Every non-blank row across all repositories and
        months, in sorted path order.

    Raises:
        json.JSONDecodeError: If a line is not valid JSON.
    """
    wanted = suffix or RESULTS_SUFFIX
    rows: list[dict[str, Any]] = []
    for path in sorted(data_dir.rglob("*.ndjson")):
        if not path.name.endswith(wanted):
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

    A row is created for a run of the test's repository when that run reported
    at least one result, did not report this test, and started between the
    test's first and last observed run, inclusive. Outside that lifetime nothing
    is synthesized. Design: ``DESIGN.md`` section 6.

    Args:
        results (list[dict[str, Any]]): Every observed result row.
        runs (list[dict[str, Any]]): Every run row, used for chronological
            ordering.

    Returns:
        list[dict[str, Any]]: Synthesized rows carrying ``status='not_run'``
        and ``source_format='derived'``.
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
        started (dict[tuple[str, int], str]): Run start time by repository and
            run id.
        runs_with_results (dict[str, set[int]]): Runs that reported at least
            one result, per repository; only these are candidates for absence.
        seen (set[tuple[str, int, str]]): Every repository, run and test
            actually observed together.
        template (dict[str, dict[str, Any]]): The first observed row per test,
            which carries stable fields such as module and assigned ID onto a
            synthesized row.
        appearances (dict[tuple[str, str], list[str]]): Run start times at
            which each test was observed, which bound its lifetime.
    """

    started: dict[tuple[str, int], str]
    runs_with_results: dict[str, set[int]]
    seen: set[tuple[str, int, str]]
    template: dict[str, dict[str, Any]]
    appearances: dict[tuple[str, str], list[str]]


def _observations(results: list[dict[str, Any]], runs: list[dict[str, Any]]) -> _Observations:
    """Build the lookup tables absence detection needs, in one pass.

    Args:
        results (list[dict[str, Any]]): Every observed result row.
        runs (list[dict[str, Any]]): Every run row.

    Returns:
        _Observations: The populated lookup tables. A run with no start time
        counts as starting at the empty string.
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
        source (dict[str, Any]): A representative observed row for this test,
            for its stable descriptive fields.
        repo (str): Repository the absence belongs to.
        run_id (int): Run the test did not appear in.
        test_uid (str): The absent test.

    Returns:
        dict[str, Any]: A result row carrying ``not_run`` and ``derived``, with
        attempt 1, empty parameters, ``params_hash`` ``not_run``, no duration,
        message, trace, labels or steps.
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
        data_dir (Path): Root of the durable record.
        index_path (Path): Where to write the SQLite file. Replaced if it
            exists, and its directory created if it does not.

    Returns:
        dict[str, int]: Row counts for ``runs``, ``observed``, ``not_run``,
        ``steps`` and ``anomalies``, for the caller to report.

    Raises:
        ValueError: If two tests carry one assigned ID in the latest run
            either appeared in.
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
    """Insert run rows, the last one winning on a repeated key.

    Args:
        connection (sqlite3.Connection): Open database connection.
        runs (list[dict[str, Any]]): Run rows from the record.

    Returns:
        None
    """
    connection.executemany(
        "INSERT OR REPLACE INTO runs VALUES (:repo, :run_id, :attempt, :workflow, :event,"
        " :head_sha, :status, :conclusion, :started_at, :updated_at, :sut_version)",
        runs,
    )


def _insert_results(connection: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    """Insert result rows, computing the reporting identity for each.

    ``identity`` is the assigned ID observed anywhere for the row's
    ``test_uid``, or the ``test_uid`` itself when none was or when the test
    duplicated another's ID in the same run. It is not
    ``COALESCE(test_id, test_uid)``, which would split a test's history at the
    moment IDs arrived. Parameters and labels are stored as key-sorted JSON,
    and the two flags as 0 or 1. Design: ``DESIGN.md`` section 7.

    Args:
        connection (sqlite3.Connection): Open database connection.
        results (list[dict[str, Any]]): Observed and synthesized result rows.

    Returns:
        None

    Raises:
        ValueError: If two tests carry one assigned ID in the latest run
            either appeared in.
    """
    assigned = _assigned_identities(results)
    payload = [
        {
            **row,
            "identity": assigned.get(row["test_uid"], row["test_uid"]),
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


def _assigned_identities(results: list[dict[str, Any]]) -> dict[str, str]:
    """Map every test that has ever published an assigned ID to that ID.

    Only observed rows are read; synthesized rows copy their ID from an
    observed one. Claimants of one ID are taken newest first, by the latest
    run each appeared in. A claimant that never ran alongside an earlier-taken
    one is a rename and joins the ID's history. A claimant that ran alongside
    one in the same run is a duplicate: it keeps its own ``test_uid`` as its
    identity and is logged as a warning. Design: ``DESIGN.md`` section 7.

    Args:
        results (list[dict[str, Any]]): Observed and synthesized result rows.

    Returns:
        dict[str, str]: ``test_uid`` to assigned ID, for the tests that carry
        one and are not a duplicate of it.

    Raises:
        ValueError: If a duplicate shares the latest run in which the ID's
            newest claimant appeared, so it is still present in the suite; the
            message lists every such duplicate.
    """
    by_uid: dict[str, str] = {}
    claims: dict[str, dict[str, set[tuple[str, int, int]]]] = defaultdict(lambda: defaultdict(set))
    for row in results:
        test_id = row.get("test_id")
        if not test_id or row.get("source_format") == DERIVED_FORMAT:
            continue
        by_uid[row["test_uid"]] = test_id
        claims[test_id][row["test_uid"]].add((row["repo"], row["run_id"], row["attempt"]))

    current: list[str] = []
    for test_id, runs_by_uid in sorted(claims.items()):
        for duplicate, holder, shared in _duplicates(runs_by_uid):
            if by_uid.get(duplicate) == test_id:
                del by_uid[duplicate]
            detail = f"{test_id}: {duplicate} ran with {holder} in {len(shared)} run(s)"
            if max(runs_by_uid[holder], key=lambda run: run[1]) in shared:
                current.append(detail)
            else:
                LOGGER.warning("Historical duplicate ID, kept apart: %s", detail)

    if current:
        raise ValueError(
            "Assigned test IDs must be unique per test. Duplicates in the latest run: "
            + "; ".join(current)
        )
    return by_uid


def _duplicates(
    runs_by_uid: dict[str, set[tuple[str, int, int]]],
) -> list[tuple[str, str, set[tuple[str, int, int]]]]:
    """Find the claimants of one ID that ran alongside another claimant.

    Claimants are taken newest first, by the latest run each appeared in, ties
    broken by ``test_uid``. Each is compared with the claimants already kept:
    no shared run means a rename, which is kept; a shared run means a
    duplicate of the first kept claimant it shares one with.

    Args:
        runs_by_uid (dict[str, set[tuple[str, int, int]]]): For one assigned
            ID, the ``(repo, run_id, attempt)`` runs each claimant appeared in.

    Returns:
        list[tuple[str, str, set[tuple[str, int, int]]]]: One
        ``(duplicate, holder, shared runs)`` per duplicate, empty when every
        claimant is a rename of the others.
    """
    order = sorted(
        runs_by_uid,
        key=lambda uid: (max(run_id for _, run_id, _ in runs_by_uid[uid]), uid),
        reverse=True,
    )
    kept: list[str] = []
    found: list[tuple[str, str, set[tuple[str, int, int]]]] = []
    for uid in order:
        holder = next((other for other in kept if runs_by_uid[uid] & runs_by_uid[other]), None)
        if holder is None:
            kept.append(uid)
        else:
            found.append((uid, holder, runs_by_uid[uid] & runs_by_uid[holder]))
    return found


def _insert_steps(connection: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    """Flatten and insert recorded steps.

    Args:
        connection (sqlite3.Connection): Open database connection.
        results (list[dict[str, Any]]): Observed result rows, whose ``steps``
            arrays are unpacked one row per step.

    Returns:
        None
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
    """Insert anomaly rows, with ``detail`` stored as key-sorted JSON.

    Args:
        connection (sqlite3.Connection): Open database connection.
        anomalies (list[dict[str, Any]]): Anomaly rows from the record.

    Returns:
        None
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
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv``.

    Returns:
        int: Process exit code: 0 once the index is built, 1 when the data
        directory does not exist.

    Raises:
        ValueError: If two tests carry one assigned ID in the latest run
            either appeared in.
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
