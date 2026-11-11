"""The durable record: append-only NDJSON, partitioned by repository and month.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains :class:`NdjsonStore`, which appends result, run, anomaly
and artifact-ledger rows to ``data/{repo}/{YYYY-MM}.{kind}.ndjson`` and answers
whether a row or an artifact is already recorded; the four partition suffixes;
and the private key functions that define each kind's identity.

Ingestion opens one store per collection, asks it which artifacts the ledger
already holds, and appends what each new artifact yields. The index later reads
the same files to rebuild ``build/results.sqlite``, so a row written here is a
row every report will count, and a row never written is one no report can.

**Append only.** The store never deletes and never rewrites: it appends rows
whose key it has not seen, and nothing else. Results are keyed on
``(repo, run_id, attempt, test_uid, params_hash)``, runs on
``(repo, run_id, attempt)``, anomalies on ``(repo, artifact_id, reason)`` and
ledger rows on ``(repo, artifact_id)``.

**Every key on disk is loaded at construction.** Each partition is routed to its
kind by suffix, an unrecognized ``.ndjson`` file is skipped with a warning, and a
malformed line is skipped with a warning, so duplicate detection is exact and a
re-run appends nothing it already holds.

Specified by ``DESIGN.md`` sections 5, 8 and 9.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final

LOGGER = logging.getLogger(__name__)

#: Root of the durable record, relative to the repository root.
DEFAULT_DATA_DIR: Final[Path] = Path("data")

#: PARTITION SUFFIXES for results and runs. They are kept apart because they
#: have different cardinality and are joined at query time, not at write time.
RESULTS_SUFFIX: Final[str] = "results.ndjson"
RUNS_SUFFIX: Final[str] = "runs.ndjson"

#: ANOMALIES: artifacts that matched configuration but yielded no results, one
#: row per artifact and reason (``DESIGN.md`` section 9).
ANOMALIES_SUFFIX: Final[str] = "anomalies.ndjson"

#: Added 2026-11-10, with the Multi-LLM Evaluation sources.
#: ARTIFACTS LEDGER: artifacts already read, one row per artifact, so a later
#: collection skips them without downloading (``DESIGN.md`` section 8).
ARTIFACTS_SUFFIX: Final[str] = "artifacts.ndjson"

#: Fields whose combination uniquely identifies a result row.
_RESULT_KEY_FIELDS: Final[tuple[str, ...]] = (
    "repo",
    "run_id",
    "attempt",
    "test_uid",
    "params_hash",
)


class NdjsonStore:
    """Append-only writer and key index over the partitioned NDJSON record."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Load the keys already on disk so that appends stay idempotent.

        Args:
            data_dir (Path | None): Root of the record. Defaults to ``data/``.

        Returns:
            None
        """
        self._root = data_dir or DEFAULT_DATA_DIR
        self._result_keys: set[tuple[Any, ...]] = set()
        self._run_keys: set[tuple[Any, ...]] = set()
        self._anomaly_keys: set[tuple[Any, ...]] = set()
        self._artifact_keys: set[tuple[Any, ...]] = set()
        self._load_existing_keys()

    def has_artifact(self, repo: str, artifact_id: Any) -> bool:
        """Report whether an artifact has already been read into the record.

        Args:
            repo (str): Owner-qualified repository name.
            artifact_id (Any): The artifact's numeric id.

        Returns:
            bool: True when the ledger lists it, so it need not be downloaded
            again.
        """
        return (repo, artifact_id) in self._artifact_keys

    def append_artifacts(self, repo_slug: str, month: str, rows: list[dict[str, Any]]) -> int:
        """Record artifacts as read, skipping any the ledger already holds.

        Args:
            repo_slug (str): Short repository name used as the partition
                directory.
            month (str): Partition month as ``YYYY-MM``.
            rows (list[dict[str, Any]]): Ledger rows, each carrying ``repo`` and
                ``artifact_id``.

        Returns:
            int: How many rows were actually written.
        """
        fresh = [row for row in rows if _artifact_key(row) not in self._artifact_keys]
        if not fresh:
            return 0
        self._append(self._partition(repo_slug, month, ARTIFACTS_SUFFIX), fresh)
        for row in fresh:
            self._artifact_keys.add(_artifact_key(row))
        return len(fresh)

    @property
    def result_count(self) -> int:
        """Return how many distinct result rows the record already holds.

        Returns:
            int: Count of unique result keys loaded from disk plus those
            appended during this session.
        """
        return len(self._result_keys)

    def has_result(self, row: dict[str, Any]) -> bool:
        """Report whether a result row is already recorded.

        Args:
            row (dict[str, Any]): A result row as produced by
                :meth:`ParsedResult.to_row`.

        Returns:
            bool: True when a row with the same key is already present.
        """
        return _result_key(row) in self._result_keys

    def append_results(self, repo_slug: str, month: str, rows: list[dict[str, Any]]) -> int:
        """Append result rows that are not already recorded.

        Args:
            repo_slug (str): Short repository name used as the partition
                directory.
            month (str): Partition month as ``YYYY-MM``.
            rows (list[dict[str, Any]]): Result rows to write.

        Returns:
            int: How many rows were actually written. Rows already present are
            skipped, so re-running an ingestion writes nothing twice.
        """
        fresh = [row for row in rows if _result_key(row) not in self._result_keys]
        if not fresh:
            return 0
        self._append(self._partition(repo_slug, month, RESULTS_SUFFIX), fresh)
        for row in fresh:
            self._result_keys.add(_result_key(row))
        return len(fresh)

    def append_runs(self, repo_slug: str, month: str, rows: list[dict[str, Any]]) -> int:
        """Append run rows that are not already recorded.

        Args:
            repo_slug (str): Short repository name used as the partition
                directory.
            month (str): Partition month as ``YYYY-MM``.
            rows (list[dict[str, Any]]): Run rows to write.

        Returns:
            int: How many rows were actually written.
        """
        fresh = [row for row in rows if _run_key(row) not in self._run_keys]
        if not fresh:
            return 0
        self._append(self._partition(repo_slug, month, RUNS_SUFFIX), fresh)
        for row in fresh:
            self._run_keys.add(_run_key(row))
        return len(fresh)

    def append_anomalies(self, repo_slug: str, month: str, rows: list[dict[str, Any]]) -> int:
        """Append artifact anomalies that are not already recorded.

        Args:
            repo_slug (str): Short repository name used as the partition
                directory.
            month (str): Partition month as ``YYYY-MM``.
            rows (list[dict[str, Any]]): Anomaly rows to write.

        Returns:
            int: How many rows were actually written.
        """
        fresh = [row for row in rows if _anomaly_key(row) not in self._anomaly_keys]
        if not fresh:
            return 0
        self._append(self._partition(repo_slug, month, ANOMALIES_SUFFIX), fresh)
        for row in fresh:
            self._anomaly_keys.add(_anomaly_key(row))
        return len(fresh)

    def _partition(self, repo_slug: str, month: str, suffix: str) -> Path:
        """Build the path of one partition file.

        Args:
            repo_slug (str): Short repository name.
            month (str): Partition month as ``YYYY-MM``.
            suffix (str): One of the four partition suffixes.

        Returns:
            Path: ``{root}/{repo_slug}/{month}.{suffix}``, whose parent
            directory may not exist yet.
        """
        return self._root / repo_slug / f"{month}.{suffix}"

    @staticmethod
    def _append(path: Path, rows: list[dict[str, Any]]) -> None:
        """Append rows to one partition file, creating it and its directory if needed.

        Args:
            path (Path): Partition file.
            rows (list[dict[str, Any]]): Rows to write, one key-sorted JSON
                object per LF-terminated line.

        Returns:
            None
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
                handle.write("\n")

    def _load_existing_keys(self) -> None:
        """Read every partition once so that duplicate detection is exact.

        Each ``.ndjson`` file under the root is routed to its kind by suffix; a
        file matching no known suffix is skipped with a warning. A missing root
        loads nothing.

        Returns:
            None
        """
        if not self._root.exists():
            return
        routes = (
            (RUNS_SUFFIX, self._run_keys, _run_key),
            (ANOMALIES_SUFFIX, self._anomaly_keys, _anomaly_key),
            (ARTIFACTS_SUFFIX, self._artifact_keys, _artifact_key),
            (RESULTS_SUFFIX, self._result_keys, _result_key),
        )
        for path in sorted(self._root.rglob("*.ndjson")):
            route = next((route for route in routes if path.name.endswith(route[0])), None)
            if route is None:
                LOGGER.warning("Ignoring %s: not a known partition kind", path)
                continue
            _, target, key_of = route
            for row in _read_rows(path):
                target.add(key_of(row))


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Read one NDJSON partition.

    Args:
        path (Path): Partition file.

    Returns:
        list[dict[str, Any]]: Every decodable row. Blank lines are ignored and a
        malformed line is skipped with a warning naming its line number.
    """
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                LOGGER.warning("Skipping malformed line %d in %s", number, path)
    return rows


def _result_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for a result row.

    Args:
        row (dict[str, Any]): A result row.

    Returns:
        tuple[Any, ...]: The values of ``_RESULT_KEY_FIELDS``, None where a
        field is absent.
    """
    return tuple(row.get(field) for field in _RESULT_KEY_FIELDS)


def _run_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for a run row.

    Args:
        row (dict[str, Any]): A run row.

    Returns:
        tuple[Any, ...]: The repository, run id and attempt.
    """
    return (row.get("repo"), row.get("run_id"), row.get("attempt"))


def _artifact_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for an artifact ledger row.

    Args:
        row (dict[str, Any]): A ledger row.

    Returns:
        tuple[Any, ...]: The repository and artifact id.
    """
    return (row.get("repo"), row.get("artifact_id"))


def _anomaly_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for an anomaly row.

    Args:
        row (dict[str, Any]): An anomaly row.

    Returns:
        tuple[Any, ...]: The repository, artifact id and reason, so one
        artifact can record more than one kind of problem.
    """
    return (row.get("repo"), row.get("artifact_id"), row.get("reason"))
