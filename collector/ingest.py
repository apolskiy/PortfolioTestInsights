"""Ingestion: pull artifacts from the Actions API and append them to the record.

The order of operations is dictated by one measurement. Artifact listings carry
a ``workflow_run`` block, so a single paginated listing per repository is enough
to associate every artifact with its run; fetching the run per artifact would
multiply the request count for nothing.

Two kinds of nothing are distinguished carefully. An **expired** artifact is
routine: for public repositories GitHub's 90-day retention is a maximum rather
than a default, so data loss is the steady state and must not fail a scheduled
job. An artifact that is **present but empty** is the opposite - the upload step
worked, so the run believed it had something to publish, and it did not. That is
evidence of a reporting misconfiguration in the source repository, and it is
recorded as data rather than left in a log line that scrolls away.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import logging
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from collector.config import ArtifactSpec, SourceSpec, load_sources
from collector.github import GitHubClient, GitHubError
from collector.models import RunRecord
from collector.parsers import PARSERS
from collector.store import NdjsonStore

LOGGER = logging.getLogger(__name__)

#: Why an artifact produced no results. Recorded on every anomaly row so that a
#: report can separate routine retention loss from a suite that is misreporting.
REASON_EXPIRED: Final[str] = "expired"
REASON_NO_RUN: Final[str] = "no_run_linked"
REASON_DOWNLOAD_FAILED: Final[str] = "download_failed"
REASON_NO_MEMBERS: Final[str] = "no_members_matched"
REASON_NO_RESULTS: Final[str] = "no_results_parsed"

#: Reasons that indicate a fault in the source repository rather than routine
#: retention. These are the ones worth acting on.
ACTIONABLE_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_NO_MEMBERS, REASON_NO_RESULTS, REASON_DOWNLOAD_FAILED, REASON_NO_RUN}
)


#: How many archive member paths to record when an artifact yields nothing.
#: Enough to recognize the layout, not so many that one bad artifact writes a
#: manifest of several hundred lines into the durable record.
MANIFEST_SAMPLE_SIZE: Final[int] = 12


@dataclass(frozen=True)
class AnomalyReport:
    """Why an artifact yielded no results, with the evidence to diagnose it.

    Attributes:
        reason: One of the module-level reason constants.
        detail: Free-form diagnostic context - the exception text for a failed
            download, or a sample of what the archive actually contained when
            the configured glob matched nothing. Without this, a wrong glob and
            an empty report are the same log line and want opposite fixes.
    """

    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestContext:
    """The collaborators every ingestion step needs, passed as one value.

    Grouping these rather than threading four parameters through every helper
    keeps the call sites readable and means adding a fifth collaborator later
    does not touch six signatures.

    Attributes:
        client: Authenticated API client.
        store: Destination record.
        summary: Counters updated in place as work proceeds.
        dry_run: When True, parse everything and write nothing.
    """

    client: GitHubClient
    store: NdjsonStore
    summary: IngestSummary
    dry_run: bool = False


@dataclass(frozen=True)
class ArtifactMatch:
    """One artifact, the spec that matched it, and the run that produced it.

    Attributes:
        artifact: Artifact object from the API.
        spec: The configured pattern it matched.
        params: Parameters extracted from the artifact name, such as a matrix leg.
        run: The run that produced it, or None when it cannot be resolved.
    """

    artifact: dict[str, Any]
    spec: ArtifactSpec
    params: dict[str, str]
    run: dict[str, Any] | None

    @property
    def name(self) -> str:
        """Return the artifact's name.

        Returns:
            The name as reported by the API, or an empty string.
        """
        return str(self.artifact.get("name") or "")

    @property
    def artifact_id(self) -> Any:
        """Return the artifact's numeric id.

        Returns:
            The id as reported by the API.
        """
        return self.artifact.get("id")


@dataclass
class IngestSummary:
    """Counters describing one ingestion, printed at the end of a run.

    Attributes:
        artifacts_seen: Artifacts matching a configured pattern.
        artifacts_parsed: Artifacts successfully downloaded and read.
        results_written: Result rows newly appended.
        runs_written: Run rows newly appended.
        results_skipped: Rows already present, and therefore not rewritten.
        anomalies: Count of artifacts that yielded nothing, by reason.
        by_repo: Result rows written, per repository.
    """

    artifacts_seen: int = 0
    artifacts_parsed: int = 0
    results_written: int = 0
    runs_written: int = 0
    results_skipped: int = 0
    anomalies: Counter = field(default_factory=Counter)
    by_repo: Counter = field(default_factory=Counter)

    def render(self) -> str:
        """Format the counters as a short report.

        Returns:
            A multi-line summary. Actionable anomalies are listed separately
            from expiry, because one is a problem and the other is the weather.
        """
        lines = [
            "",
            "Ingestion summary",
            "-----------------",
            f"  artifacts matched : {self.artifacts_seen}",
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
        context: Collaborators and run mode.
        source: Repository and artifact patterns from the configuration.
    """
    LOGGER.info("Reading %s", source.repo)
    runs_by_id = {run["id"]: run for run in context.client.list_runs(source.repo)}

    for artifact in context.client.list_artifacts(source.repo):
        matched = _match_artifact(source, artifact)
        if matched is None:
            continue
        spec, params = matched
        context.summary.artifacts_seen += 1
        match = ArtifactMatch(
            artifact=artifact,
            spec=spec,
            params=params,
            run=runs_by_id.get((artifact.get("workflow_run") or {}).get("id")),
        )

        anomaly = _read_artifact(context, source, match)
        if anomaly is not None:
            _record_anomaly(context, source, match, anomaly)


def _read_artifact(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch
) -> AnomalyReport | None:
    """Download, extract and parse one artifact, persisting whatever it yields.

    Args:
        context: Collaborators and run mode.
        source: Repository being ingested.
        match: The artifact, its spec, and the run that produced it.

    Returns:
        An AnomalyReport when the artifact yielded no results, otherwise None.
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

    results = PARSERS[match.spec.parser](members, match.params)
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


def _persist(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch, results: list[Any]
) -> None:
    """Write one artifact's rows into the record.

    Args:
        context: Collaborators and run mode.
        source: Repository being ingested.
        match: The artifact and the run that produced it.
        results: Parsed results from the artifact.
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


def _record_anomaly(
    context: IngestContext, source: SourceSpec, match: ArtifactMatch, anomaly: AnomalyReport
) -> None:
    """Record an artifact that matched configuration but produced no results.

    The glob and parser are recorded alongside the reason so that a later reader
    can tell a misconfigured collector from a misreporting suite - the two
    produce the same symptom and want opposite fixes.

    Args:
        context: Collaborators and run mode.
        source: Repository being ingested.
        match: The artifact, its spec, and the run that produced it.
        anomaly: The reason and its supporting diagnostic detail.
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
        source: Repository being ingested.
        run: The Actions run object.

    Returns:
        The normalized run record.
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
        run: The Actions run object.

    Returns:
        ``YYYY-MM`` taken from the run's start time.
    """
    return str(run.get("run_started_at") or run.get("created_at") or "")[:7]


def _match_artifact(
    source: SourceSpec, artifact: dict[str, Any]
) -> tuple[ArtifactSpec, dict[str, str]] | None:
    """Find the artifact spec matching an artifact, with its parameter groups.

    Args:
        source: Repository configuration.
        artifact: Artifact object from the API.

    Returns:
        A ``(spec, params)`` pair, or None when no configured pattern matches.
    """
    name = str(artifact.get("name") or "")
    for spec in source.artifacts:
        params = spec.match_params(name)
        if params is not None:
            return spec, params
    return None


def _extract(payload: bytes, glob: str) -> dict[str, bytes]:
    """Read the members of an artifact zip that a parser needs.

    Only matching members are read into memory. The archives are large mostly
    because of Allure's HTML assets, and none of that is worth decompressing.

    Args:
        payload: Raw zip bytes.
        glob: fnmatch pattern applied to member paths.

    Returns:
        Member path to contents. An unreadable archive yields an empty mapping
        rather than raising, so one bad artifact costs one artifact.
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
    """List every path inside an artifact zip.

    Used only when the configured glob matched nothing, to record what the
    archive did contain. A glob that is wrong and an archive that is empty
    produce the same symptom and want opposite fixes, and this is what tells
    them apart after the fact.

    Args:
        payload: Raw zip bytes.

    Returns:
        Sorted member paths, empty when the archive cannot be read.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return sorted(info.filename for info in archive.infolist() if not info.is_dir())
    except (zipfile.BadZipFile, OSError):
        return []


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface.

    Returns:
        The configured argument parser.
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
        argv: Argument vector, defaulting to ``sys.argv``.

    Returns:
        Process exit code: 0 on success, 1 when the API could not be reached.
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
