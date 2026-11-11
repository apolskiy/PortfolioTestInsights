"""Ingestion: pull artifacts from the Actions API and append them to the record.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains the command line ``python -m collector.ingest``
(``build_parser``, ``main``); ``ingest_repository``, which collects one
configured repository; the private steps it runs per artifact (select, read,
shape, persist, or record an anomaly); and the records those steps pass around:
:class:`IngestContext`, :class:`ArtifactMatch`, :class:`AnomalyReport` and
:class:`IngestSummary`. The ``REASON_*`` constants name why an artifact yielded
nothing.

It is the first stage and the only one that reads the network: the
configuration is loaded, each repository's runs and artifacts are listed once,
and every artifact read here becomes rows in ``data/``, which ``make db`` turns
into the index the reports query. A failed listing ends the collection with
exit code 1 and nothing further written; a failed download costs only that
artifact, which is recorded as an anomaly.

**Per repository, in order.** Runs and artifacts are listed once each; every
artifact is matched against the configured patterns, the first match supplying
its spec and its name parameters; ``keep`` policies select which matches to
read; matches the ledger already holds are counted and skipped without
download.

**Per artifact, one outcome.** An artifact either yields results, which are
shaped by the source's ``test_id_from_name`` and the artifact's ``steps``
policy, then appended with its run row and finally ledgered; or it yields an
anomaly naming the reason: ``expired``, ``no_run_linked``,
``download_failed``, ``no_members_matched`` or ``no_results_parsed``. Every
reason but ``expired`` is actionable and is flagged in the summary.

**Dry run writes nothing.** ``--dry-run`` downloads and parses everything and
counts what would be written; no result, run, anomaly or ledger row reaches
disk.

Specified by ``DESIGN.md`` sections 8 and 9.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import logging
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from collector.config import ArtifactSpec, SourceSpec, load_sources
from collector.github import GitHubClient, GitHubError
from collector.models import PASSED, SKIPPED, RunRecord
from collector.parsers import PARSERS
from collector.store import NdjsonStore

LOGGER = logging.getLogger(__name__)

#: ANOMALY REASONS: why an artifact produced no results. Recorded on every
#: anomaly row so a report can separate routine retention loss from a suite
#: that is misreporting.
REASON_EXPIRED: Final[str] = "expired"
REASON_NO_RUN: Final[str] = "no_run_linked"
REASON_DOWNLOAD_FAILED: Final[str] = "download_failed"
REASON_NO_MEMBERS: Final[str] = "no_members_matched"
REASON_NO_RESULTS: Final[str] = "no_results_parsed"

#: ACTIONABLE, per ``DESIGN.md`` section 9: reasons that indicate a fault in the
#: source repository or the configuration rather than routine retention.
ACTIONABLE_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_NO_MEMBERS, REASON_NO_RESULTS, REASON_DOWNLOAD_FAILED, REASON_NO_RUN}
)


#: BOUNDED, per ``DESIGN.md`` section 9: how many archive member paths an
#: anomaly row records when an artifact yields nothing. Enough to recognize the
#: layout, not a manifest of several hundred lines.
MANIFEST_SAMPLE_SIZE: Final[int] = 12


@dataclass(frozen=True)
class AnomalyReport:
    """Why an artifact yielded no results, with the evidence to diagnose it.

    Attributes:
        reason (str): One of the ``REASON_*`` constants.
        detail (dict[str, Any]): Diagnostic context: the exception text for a
            failed download, or the file count and a sample of up to
            ``MANIFEST_SAMPLE_SIZE`` paths when no member matched or none
            parsed. Empty for ``expired`` and ``no_run_linked``.
    """

    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestContext:
    """The collaborators every ingestion step needs, passed as one value.

    Design: ``DESIGN.md`` section 5.

    Attributes:
        client (GitHubClient): Authenticated API client.
        store (NdjsonStore): Destination record.
        summary (IngestSummary): Counters updated in place as work proceeds.
        dry_run (bool): When True, parse everything and write nothing.
    """

    client: GitHubClient
    store: NdjsonStore
    summary: IngestSummary
    dry_run: bool = False


@dataclass(frozen=True)
class ArtifactMatch:
    """One artifact, the spec that matched it, and the run that produced it.

    Attributes:
        artifact (dict[str, Any]): Artifact object from the API.
        spec (ArtifactSpec): The configured pattern it matched.
        params (dict[str, str]): Parameters extracted from the artifact name,
            such as a matrix leg.
        run (dict[str, Any] | None): The run that produced it, or None when the
            run listing does not hold it.
    """

    artifact: dict[str, Any]
    spec: ArtifactSpec
    params: dict[str, str]
    run: dict[str, Any] | None

    @property
    def name(self) -> str:
        """Return the artifact's name.

        Returns:
            str: The name as reported by the API, or an empty string.
        """
        return str(self.artifact.get("name") or "")

    @property
    def artifact_id(self) -> Any:
        """Return the artifact's numeric id.

        Returns:
            Any: The id as reported by the API.
        """
        return self.artifact.get("id")


@dataclass
class IngestSummary:
    """Counters describing one ingestion, printed at the end of a run.

    Attributes:
        artifacts_seen (int): Artifacts matching a configured pattern and
            selected by their ``keep`` policy.
        artifacts_already_read (int): Of those, artifacts the ledger already
            held, which were not downloaded.
        artifacts_parsed (int): Artifacts downloaded and read into results.
        results_written (int): Result rows newly appended, or that would be in
            a dry run.
        runs_written (int): Run rows newly appended.
        results_skipped (int): Rows already present, and therefore not
            rewritten.
        anomalies (Counter): Count of artifacts that yielded nothing, by
            reason.
        by_repo (Counter): Result rows written, per repository.
    """

    artifacts_seen: int = 0
    artifacts_already_read: int = 0
    artifacts_parsed: int = 0
    results_written: int = 0
    runs_written: int = 0
    results_skipped: int = 0
    anomalies: Counter = field(default_factory=Counter)
    by_repo: Counter = field(default_factory=Counter)

    def render(self) -> str:
        """Format the counters as a short report.

        Returns:
            str: A multi-line summary: the counters, results per repository,
            and, when any artifact yielded nothing, a count per reason with the
            actionable reasons flagged ``<-- investigate``.
        """
        lines = [
            "",
            "Ingestion summary",
            "-----------------",
            f"  artifacts matched : {self.artifacts_seen}",
            f"  already read      : {self.artifacts_already_read} (in the ledger, not downloaded)",
            f"  parsed            : {self.artifacts_parsed}",
            f"  runs written      : {self.runs_written}",
            f"  results written   : {self.results_written}",
            f"  results skipped   : {self.results_skipped} (already recorded)",
            "",
        ]
        for repo, count in sorted(self.by_repo.items()):
            lines.append(f"  {repo}: {count} results")

        if self.anomalies:
            lines.extend(["", "Artifacts that yielded no results", "-" * 33])
            for reason, count in sorted(self.anomalies.items()):
                flag = "  <-- investigate" if reason in ACTIONABLE_REASONS else ""
                lines.append(f"  {reason:<20} {count}{flag}")
        return "\n".join(lines)


def ingest_repository(context: IngestContext, source: SourceSpec) -> None:
    """Ingest every matching artifact for one repository.

    Args:
        context (IngestContext): Collaborators and run mode.
        source (SourceSpec): Repository and artifact patterns from the
            configuration.

    Returns:
        None: Rows are appended to ``context.store`` and counts to
        ``context.summary``.

    Raises:
        GitHubError: If the run or artifact listing cannot be fetched.
    """
    LOGGER.info("Reading %s", source.repo)
    runs_by_id = {run["id"]: run for run in context.client.list_runs(source.repo)}

    matches: list[ArtifactMatch] = []
    for artifact in context.client.list_artifacts(source.repo):
        matched = _match_artifact(source, artifact)
        if matched is None:
            continue
        spec, params = matched
        matches.append(
            ArtifactMatch(
                artifact=artifact,
                spec=spec,
                params=params,
                run=runs_by_id.get((artifact.get("workflow_run") or {}).get("id")),
            )
        )

    today = datetime.now(timezone.utc).date().isoformat()
    for match in _select(matches, today):
        context.summary.artifacts_seen += 1
        if context.store.has_artifact(source.repo, match.artifact_id):
            context.summary.artifacts_already_read += 1
            continue
        anomaly = _read_artifact(context, source, match)
        if anomaly is not None:
            _record_anomaly(context, source, match, anomaly)


def _select(matches: list[ArtifactMatch], today: str) -> list[ArtifactMatch]:
    """Apply each artifact spec's run-selection policy.

    A match under ``every`` is always kept. Under ``latest_per_day`` only the
    newest artifact per spec, parameter set and UTC creation day is kept, and
    only for days before ``today``; an artifact with no creation date is
    dropped. Design: ``DESIGN.md`` section 8.

    Args:
        matches (list[ArtifactMatch]): Every artifact matching a configured
            pattern, in listing order.
        today (str): Today's UTC date as ``YYYY-MM-DD``.

    Returns:
        list[ArtifactMatch]: The artifacts to read, in listing order.
    """
    latest: dict[tuple[Any, ...], ArtifactMatch] = {}
    chosen: set[int] = set()
    for match in matches:
        if match.spec.keep != "latest_per_day":
            chosen.add(id(match))
            continue
        created = str(match.artifact.get("created_at") or "")
        day = created[:10]
        if not day or day >= today:
            continue
        key = (match.spec.pattern.pattern, tuple(sorted(match.params.items())), day)
        current = latest.get(key)
        if current is None or created > str(current.artifact.get("created_at") or ""):
            latest[key] = match
    chosen.update(id(match) for match in latest.values())
    return [match for match in matches if id(match) in chosen]


def _read_artifact(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch
) -> AnomalyReport | None:
    """Download, extract and parse one artifact, persisting whatever it yields.

    The checks run in order: expired, no linked run, download failure, no
    member matching the glob, no parseable result. The first that applies is
    the artifact's anomaly, and nothing is downloaded for the first two.

    Args:
        context (IngestContext): Collaborators and run mode.
        source (SourceSpec): Repository being ingested.
        match (ArtifactMatch): The artifact, its spec, and the run that
            produced it.

    Returns:
        AnomalyReport | None: The anomaly when the artifact yielded no results;
        None when its results were persisted.
    """
    if match.artifact.get("expired"):
        LOGGER.info("  expired: %s (%s)", match.name, match.artifact.get("created_at"))
        return AnomalyReport(REASON_EXPIRED)

    if match.run is None:
        LOGGER.warning("  no run linked to artifact %s", match.artifact_id)
        return AnomalyReport(REASON_NO_RUN)

    try:
        payload = context.client.download_artifact(source.repo, match.artifact_id)
    except GitHubError as error:
        LOGGER.warning("  download failed for %s: %s", match.artifact_id, error)
        return AnomalyReport(REASON_DOWNLOAD_FAILED, {"error": str(error)})

    run_id = match.run["id"]
    members = _extract(payload, match.spec.glob)
    if not members:
        manifest = _manifest(payload)
        LOGGER.warning(
            "  no members matched '%s' in %s (run %s) - archive holds %d files: %s",
            match.spec.glob,
            match.name,
            run_id,
            len(manifest),
            ", ".join(manifest[:4]) or "(empty archive)",
        )
        return AnomalyReport(
            REASON_NO_MEMBERS,
            {"archive_files": len(manifest), "sample": manifest[:MANIFEST_SAMPLE_SIZE]},
        )

    results = _shape(PARSERS[match.spec.parser](members, match.params), source, match.spec)
    if not results:
        LOGGER.warning(
            "  %d members but no parseable results in %s (run %s)", len(members), match.name, run_id
        )
        return AnomalyReport(
            REASON_NO_RESULTS,
            {"matched_files": len(members), "sample": sorted(members)[:MANIFEST_SAMPLE_SIZE]},
        )

    context.summary.artifacts_parsed += 1
    LOGGER.info("  %s run %s: %d results (%s)", match.name, run_id, len(results), match.spec.parser)
    _persist(context, source, match, results)
    return None


def _shape(results: list[Any], source: SourceSpec, spec: ArtifactSpec) -> list[Any]:
    """Apply the source's and artifact's policies to freshly parsed results.

    - **An assigned ID recovered from the test name**, where the source declares
      ``test_id_from_name`` and the parser found no ID. An ID a parser already
      found is never overwritten.
    - **Steps dropped from passing and skipped results**, where the artifact's
      step policy is ``failures``. The result is marked ``steps_omitted``.

    Design: ``DESIGN.md`` section 8.

    Args:
        results (list[Any]): Parsed results from one artifact.
        source (SourceSpec): The repository they came from.
        spec (ArtifactSpec): The artifact pattern that matched.

    Returns:
        list[Any]: The results in the same order, each replaced by an adjusted
        copy where a policy applied.
    """
    shaped: list[Any] = []
    for result in results:
        changes: dict[str, Any] = {}
        if not result.test_id and source.test_id_from_name is not None:
            changes["test_id"] = source.derived_test_id(result.test_name)
        if spec.steps == "failures" and result.steps and result.status in (PASSED, SKIPPED):
            changes.update(steps=(), steps_omitted=True)
        shaped.append(replace(result, **changes) if changes else result)
    return shaped


def _persist(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch, results: list[Any]
) -> None:
    """Write one artifact's rows into the record.

    Appends the run row, then the result rows, then the ledger row, all to the
    month the run started in. In a dry run only the counters move.

    Args:
        context (IngestContext): Collaborators and run mode.
        source (SourceSpec): Repository being ingested.
        match (ArtifactMatch): The artifact and the run that produced it.
        results (list[Any]): Parsed results from the artifact.

    Returns:
        None
    """
    run = match.run or {}
    run_id = int(run["id"])
    attempt = int(run.get("run_attempt") or 1)
    summary = context.summary

    rows = [result.to_row(source.repo, run_id, attempt, match.spec.parser) for result in results]
    already = sum(1 for row in rows if context.store.has_result(row))
    summary.results_skipped += already

    if context.dry_run:
        summary.results_written += len(rows) - already
        summary.by_repo[source.repo] += len(rows) - already
        return

    month = _month_of(run)
    record = _run_record(source, run).to_row()
    summary.runs_written += context.store.append_runs(source.slug, month, [record])
    written = context.store.append_results(source.slug, month, rows)
    summary.results_written += written
    summary.by_repo[source.repo] += written
    # Added 2026-11-10 with the artifacts ledger.
    # Ledgered only after its rows are on disk, so an interrupted collection
    # reads the artifact again rather than skipping rows it never wrote.
    context.store.append_artifacts(
        source.slug,
        month,
        [
            {
                "repo": source.repo,
                "artifact_id": match.artifact_id,
                "name": match.name,
                "run_id": run_id,
                "created_at": match.artifact.get("created_at"),
                "results": len(rows),
            }
        ],
    )


def _record_anomaly(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch, anomaly: AnomalyReport
) -> None:
    """Record an artifact that matched configuration but produced no results.

    The row carries the reason and its detail, the glob and parser applied, and
    the run's id, conclusion and commit. It goes to the run's month, or the
    artifact's creation month when no run is linked, and a linked run's row is
    appended too. A dry run only counts the reason, and an anomaly never
    reaches the ledger, so the artifact is read again next time.

    Args:
        context (IngestContext): Collaborators and run mode.
        source (SourceSpec): Repository being ingested.
        match (ArtifactMatch): The artifact, its spec, and the run that
            produced it.
        anomaly (AnomalyReport): The reason and its supporting diagnostic
            detail.

    Returns:
        None
    """
    context.summary.anomalies[anomaly.reason] += 1
    if context.dry_run:
        return

    run = match.run or {}
    row = {
        "repo": source.repo,
        "artifact_id": match.artifact_id,
        "artifact_name": match.name,
        "created_at": match.artifact.get("created_at"),
        "reason": anomaly.reason,
        "detail": anomaly.detail,
        "glob": match.spec.glob,
        "parser": match.spec.parser,
        "run_id": run.get("id"),
        "run_conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
    }
    month = _month_of(run) if run else str(match.artifact.get("created_at") or "")[:7]
    context.store.append_anomalies(source.slug, month, [row])

    if match.run is not None:
        context.store.append_runs(source.slug, month, [_run_record(source, run).to_row()])


def _run_record(source: SourceSpec, run: dict[str, Any]) -> RunRecord:
    """Build the run record for one Actions run.

    Args:
        source (SourceSpec): Repository being ingested.
        run (dict[str, Any]): The Actions run object.

    Returns:
        RunRecord: The normalized record. ``attempt`` defaults to 1,
        ``started_at`` falls back to ``created_at``, and ``sut_version`` is
        left None.
    """
    return RunRecord(
        repo=source.repo,
        run_id=int(run["id"]),
        attempt=int(run.get("run_attempt") or 1),
        workflow=str(run.get("name") or ""),
        event=str(run.get("event") or ""),
        head_sha=str(run.get("head_sha") or ""),
        status=str(run.get("status") or ""),
        conclusion=run.get("conclusion"),
        started_at=str(run.get("run_started_at") or run.get("created_at") or ""),
        updated_at=str(run.get("updated_at") or ""),
    )


def _month_of(run: dict[str, Any]) -> str:
    """Return the partition month for a run.

    Args:
        run (dict[str, Any]): The Actions run object.

    Returns:
        str: ``YYYY-MM`` taken from ``run_started_at``, or ``created_at`` when
        that is absent.
    """
    return str(run.get("run_started_at") or run.get("created_at") or "")[:7]


def _match_artifact(
    source: SourceSpec, artifact: dict[str, Any]
) -> tuple[ArtifactSpec, dict[str, str]] | None:
    """Find the artifact spec matching an artifact, with its parameter groups.

    Args:
        source (SourceSpec): Repository configuration.
        artifact (dict[str, Any]): Artifact object from the API.

    Returns:
        tuple[ArtifactSpec, dict[str, str]] | None: The first ``(spec, params)``
        pair in configuration order, or None when no configured pattern
        matches.
    """
    name = str(artifact.get("name") or "")
    for spec in source.artifacts:
        params = spec.match_params(name)
        if params is not None:
            return spec, params
    return None


def _extract(payload: bytes, glob: str) -> dict[str, bytes]:
    """Read the members of an artifact zip that a parser needs.

    Only files whose path matches the glob are decompressed; directories and
    everything else are left in the archive.

    Args:
        payload (bytes): Raw zip bytes.
        glob (str): fnmatch pattern applied to member paths.

    Returns:
        dict[str, bytes]: Member path to contents. An unreadable archive is
        logged and yields an empty mapping rather than raising.
    """
    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir() or not fnmatch.fnmatch(info.filename, glob):
                    continue
                members[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, OSError) as error:
        LOGGER.warning("  unreadable archive: %s", error)
    return members


def _manifest(payload: bytes) -> list[str]:
    """List every file path inside an artifact zip.

    Called only when the configured glob matched nothing, to record what the
    archive did contain. Design: ``DESIGN.md`` section 9.

    Args:
        payload (bytes): Raw zip bytes.

    Returns:
        list[str]: Sorted member paths, directories excluded; empty when the
        archive cannot be read.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return sorted(info.filename for info in archive.infolist() if not info.is_dir())
    except (zipfile.BadZipFile, OSError):
        return []


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface.

    Returns:
        argparse.ArgumentParser: The parser for ``--repo``, ``--data-dir``,
        ``--config``, ``--dry-run`` and ``--verbose``.
    """
    parser = argparse.ArgumentParser(
        prog="python -m collector.ingest",
        description="Pull test results from portfolio CI artifacts into the durable record.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        help="Restrict to one repository; may be repeated. Matches on the short name.",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="Root of the durable record (default: data/)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Source configuration (default: config/sources.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse everything and report what would be written, without writing.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log every artifact considered.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run an ingestion from the command line.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv``.

    Returns:
        int: Process exit code: 0 on success; 1 when no configured repository
        matches ``--repo``, or the API could not be reached.

    Raises:
        ConfigError: If the source configuration is malformed.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = load_sources(args.config)
    if args.repo:
        wanted = {name.lower() for name in args.repo}
        sources = tuple(s for s in sources if s.slug in wanted or s.repo.lower() in wanted)
        if not sources:
            LOGGER.error("No configured repository matched %s", ", ".join(args.repo))
            return 1

    store = NdjsonStore(args.data_dir)
    summary = IngestSummary()

    try:
        context = IngestContext(
            client=GitHubClient(), store=store, summary=summary, dry_run=args.dry_run
        )
        for source in sources:
            ingest_repository(context, source)
    except GitHubError as error:
        LOGGER.error("%s", error)
        return 1

    print(summary.render())
    if args.dry_run:
        print("\n  (dry run - nothing was written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
