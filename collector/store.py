"""The durable record: append-only NDJSON, partitioned by repository and month.

Committing a SQLite file would be the obvious choice and is the wrong one. A
binary rewritten daily stores a full new copy in git history on every commit and
cannot be reviewed in a diff. Appends, by contrast, produce small deltas and a
readable review, and a bad ingestion is visible rather than opaque.

The index that reports query is derived from these files and is gitignored. This
module therefore never deletes and never rewrites: it appends rows it has not
seen, and nothing else.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final

LOGGER = logging.getLogger(__name__)

#: Root of the durable record, relative to the repository root.
DEFAULT_DATA_DIR: Final[Path] = Path("data")

#: Partition file suffixes. Runs and results are kept apart because they have
#: different cardinality and are joined at query time, not at write time.
RESULTS_SUFFIX: Final[str] = "results.ndjson"
RUNS_SUFFIX: Final[str] = "runs.ndjson"

#: Artifacts that matched configuration but yielded no results are recorded
#: here. An artifact that exists while containing no test results is evidence
#: that a run's reporting was misconfigured - the suite uploaded something, so
#: the upload step worked, but nothing was in it. That is a finding about the
#: source repository, and a finding kept only in a log line is a finding lost.
ANOMALIES_SUFFIX: Final[str] = "anomalies.ndjson"

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
            data_dir: Root of the record. Defaults to ``data/``.
        """
        self._root = data_dir or DEFAULT_DATA_DIR
        self._result_keys: set[tuple[Any, ...]] = set()
        self._run_keys: set[tuple[Any, ...]] = set()
        self._anomaly_keys: set[tuple[Any, ...]] = set()
        self._load_existing_keys()

    @property
    def result_count(self) -> int:
        """Return how many distinct result rows the record already holds.

        Returns:
            Count of unique result keys loaded from disk plus those appended
            during this session.
        """
        return len(self._result_keys)

    def has_result(self, row: dict[str, Any]) -> bool:
        """Report whether a result row is already recorded.

        Args:
            row: A result row as produced by ``ParsedResult.to_row``.

        Returns:
            True when an identical key is already present.
        """
        return _result_key(row) in self._result_keys

    def append_results(self, repo_slug: str, month: str, rows: list[dict[str, Any]]) -> int:
        """Append result rows that are not already recorded.

        Args:
            repo_slug: Short repository name used as the partition directory.
            month: Partition month as ``YYYY-MM``.
            rows: Result rows to write.

        Returns:
            How many rows were actually written. Rows already present are
            skipped, so re-running an ingestion is safe and cheap.
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
            repo_slug: Short repository name used as the partition directory.
            month: Partition month as ``YYYY-MM``.
            rows: Run rows to write.

        Returns:
            How many rows were actually written.
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
            repo_slug: Short repository name used as the partition directory.
            month: Partition month as ``YYYY-MM``.
            rows: Anomaly rows to write.

        Returns:
            How many rows were actually written.
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
            repo_slug: Short repository name.
            month: Partition month as ``YYYY-MM``.
            suffix: Either the results or the runs suffix.

        Returns:
            The path, whose parent directory may not exist yet.
        """
        return self._root / repo_slug / f"{month}.{suffix}"

    @staticmethod
    def _append(path: Path, rows: list[dict[str, Any]]) -> None:
        """Append rows to one partition file, creating it if needed.

        Args:
            path: Partition file.
            rows: Rows to write, one JSON object per line.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
                handle.write("\n")

    def _load_existing_keys(self) -> None:
        """Read every partition once so that duplicate detection is exact.

        A watermark alone would not be enough: a previous run may have been
        interrupted partway through a repository, leaving some of a run's rows
        written and some not.
        """
        if not self._root.exists():
            return
        for path in sorted(self._root.rglob("*.ndjson")):
            if path.name.endswith(RUNS_SUFFIX):
                target, key_of = self._run_keys, _run_key
            elif path.name.endswith(ANOMALIES_SUFFIX):
                target, key_of = self._anomaly_keys, _anomaly_key
            else:
                target, key_of = self._result_keys, _result_key
            for row in _read_rows(path):
                target.add(key_of(row))


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Read one NDJSON partition.

    Args:
        path: Partition file.

    Returns:
        Every decodable row. A malformed line is skipped with a warning rather
        than aborting startup, because the alternative is that one bad line
        makes the whole history unreadable.
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
        row: A result row.

    Returns:
        The tuple of identifying fields.
    """
    return tuple(row.get(field) for field in _RESULT_KEY_FIELDS)


def _run_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for a run row.

    Args:
        row: A run row.

    Returns:
        The tuple of identifying fields.
    """
    return (row.get("repo"), row.get("run_id"), row.get("attempt"))


def _anomaly_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the uniqueness key for an anomaly row.

    Args:
        row: An anomaly row.

    Returns:
        The tuple of identifying fields. The reason is part of the key so that
        one artifact can record more than one kind of problem.
    """
    return (row.get("repo"), row.get("artifact_id"), row.get("reason"))
