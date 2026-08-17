"""Descriptive reports over the derived index.

Every figure here is a count or an order statistic. Nothing is modelled, and
nothing is extrapolated, because the corpus does not support it: thirty-odd
test-level failures across a portfolio is a list, not a distribution, and
presenting it as a rate would imply precision the data does not have. Where a
number is too small to carry an inference, the report says so in the output
rather than leaving the reader to work it out.

Two ideas are kept deliberately apart:

* **Same-input disagreement** - identical commit, identical code, different
  outcome. This is the only thing that proves flakiness, and today it exists
  only in PublicAP's build matrix.
* **Volatility** - how often a test's outcome changes over time. Useful, but it
  cannot distinguish a flaky test from a test that broke and was fixed, so it
  is labelled as a signal rather than a verdict.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Final

from collector.config import load_sources
from collector.index import DEFAULT_INDEX_PATH
from collector.render import render_page

LOGGER = logging.getLogger(__name__)

#: Minimum observations before a duration summary is worth printing.
MIN_DURATION_SAMPLES: Final[int] = 8

#: How many rows each ranked section prints.
TOP_N: Final[int] = 10

#: Statuses that count as a test having gone wrong.
BAD_STATUSES: Final[tuple[str, ...]] = ("failed", "broken")

#: Linked in the generated page so a reader can check figures against the record.
SOURCE_URL: Final[str] = "https://github.com/apolskiy/PortfolioTestInsights"

#: Parameters describing *where* a test ran rather than *what it ran against*.
#: These come from the artifact name - a build matrix leg or a browser engine -
#: and are the axes a same-input comparison varies deliberately. Every other
#: parameter is part of the test case and must be held fixed, or a parametrized
#: test that passes for Brazil and fails for Germany looks like disagreement
#: when it is simply two different inputs.
ENVIRONMENT_PARAMS: Final[frozenset[str]] = frozenset({"os", "python", "browser"})


def _case_key(params_json: str) -> str:
    """Reduce a parameter set to the part that identifies the test case.

    Args:
        params_json: The row's serialized parameters.

    Returns:
        A stable string over the non-environment parameters, so two rows share
        it exactly when they exercised the same case.
    """
    try:
        params = json.loads(params_json or "{}")
    except json.JSONDecodeError:
        return params_json or ""
    kept = {key: value for key, value in params.items() if key not in ENVIRONMENT_PARAMS}
    return json.dumps(kept, sort_keys=True)


def _percentile(values: list[float], fraction: float) -> float:
    """Return a percentile by nearest-rank, without interpolating.

    Interpolation invents a value that was never measured. For durations drawn
    from a few dozen runs, reporting a real observation is more honest.

    Args:
        values: Samples, not required to be sorted.
        fraction: Percentile as a fraction, for example 0.95.

    Returns:
        The sample at the nearest rank.
    """
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[rank]


#: Printed where a test has no assigned ID, which is every row recorded before
#: the scheme existed.
NO_ID: Final[str] = "-"


def repo_codes() -> dict[str, str]:
    """Map each repository to the short code its assigned test IDs already use.

    Spelling `VM-Deployment-and-Configuration` in every row of every table costs
    a third of the width and tells the reader nothing they do not learn once.
    The suites already carry a short code per repository - the prefix of their
    assigned IDs - so reusing it keeps the tables narrow and makes the column
    agree with the IDs beside it instead of being a second naming scheme.

    Returns:
        Owner-qualified repository name to short code. Repositories missing from
        the configuration fall back to their bare name.
    """
    return {source.repo: source.prefix for source in load_sources()}


def _code(repo: str, codes: dict[str, str]) -> str:
    """Render one repository cell.

    Args:
        repo: Owner-qualified repository name.
        codes: Lookup from :func:`repo_codes`.

    Returns:
        The short code, or the bare repository name when none is configured.
    """
    return codes.get(repo, repo.split("/")[-1])


def _short(identity: str) -> str:
    """Trim a repo-qualified identity down to something a table can hold.

    Args:
        identity: Either an assigned ID or a ``repo::module::name`` uid.

    Returns:
        A display form: assigned IDs pass through, uids lose their repo and
        module.
    """
    if "::" not in identity:
        return identity
    return identity.rsplit("::", 1)[-1]


def test_labels(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    """Map each identity to the ID and the test name a reader needs to see.

    An assigned ID is the right key and the wrong label. ``PAWA_10020`` is
    stable across renames, which is exactly why it carries no meaning - so a
    report that printed only the ID would send every reader to the source to
    find out which test it is. Under a test-management system the ID would
    resolve to a title; there is no such system here, and the report should not
    assume one.

    Both are therefore published side by side: the ID to cite and track, the
    name to read.

    Args:
        connection: Open index connection.

    Returns:
        ``(repo, identity)`` to ``(test id or '-', test name)``.
    """
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    query = """
        SELECT repo, identity, MAX(test_id), MAX(test_name)
        FROM results GROUP BY repo, identity
    """
    for repo, identity, test_id, test_name in connection.execute(query):
        labels[(repo, identity)] = (test_id or NO_ID, test_name or _short(identity))
    return labels


def _cells(repo: str, identity: str, labels: dict[tuple[str, str], tuple[str, str]]) -> str:
    """Render the two identity columns for one row.

    Args:
        repo: Owning repository.
        identity: The row's grouping identity.
        labels: Lookup from :func:`test_labels`.

    Returns:
        Two pipe-separated table cells: assigned ID, then test name.
    """
    test_id, test_name = labels.get((repo, identity), (NO_ID, _short(identity)))
    return f"{test_id} | {test_name}"


def repositories(connection: sqlite3.Connection) -> list[str]:
    """Publish the code-to-repository mapping as a legend, once.

    Every table below cites a repository by its short code, so the expansion
    belongs somewhere a reader meets before the first of them - and somewhere
    that is a lookup rather than a measurement. Folding it into the inventory
    table would widen a statistics table to carry a two-column fact, and bury
    the one row a reader is scanning for behind four numeric columns.

    Args:
        connection: Open index connection, used only to list the repositories
            the record actually holds - a configured source that has never been
            collected does not belong in a legend for this report.

    Returns:
        Report lines.
    """
    codes = repo_codes()
    lines = ["## Repositories", ""]
    lines.append(
        "Each code is the prefix of that suite's assigned test IDs, so a code in any "
        "table agrees with the IDs printed beside it. The range shows which IDs that "
        "suite has actually published so far - a test recorded before the scheme "
        "existed carries none, and appears in the tables with `-` in its ID column."
    )
    lines.append("")
    lines.append("| Code | Repository | Tests | Assigned IDs in use |")
    lines.append("|---|---|---:|---|")
    # Collapse to one row per test first. A test's assigned ID lives on the rows
    # recorded since the scheme existed; its older rows still carry NULL, so
    # counting NULLs directly would report every test as unassigned.
    query = """
        SELECT repo, COUNT(*), MIN(assigned), MAX(assigned), SUM(assigned IS NULL)
        FROM (
            SELECT repo, identity, MAX(test_id) AS assigned
            FROM results GROUP BY repo, identity
        )
        GROUP BY repo ORDER BY repo
    """
    for repo, tests, lowest, highest, unassigned in connection.execute(query):
        if lowest and highest:
            span = lowest if lowest == highest else f"{lowest} .. {highest}"
            if unassigned:
                span += f" (+{unassigned} without one)"
        else:
            span = "none published yet"
        link = f"[{repo}](https://github.com/{repo})"
        lines.append(f"| {_code(repo, codes)} | {link} | {tests} | {span} |")
    return lines + [""]


def inventory(connection: sqlite3.Connection) -> list[str]:
    """Summarize what the record holds, per repository.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Inventory", ""]
    codes = repo_codes()
    lines.append("| Repo | Runs | Tests | Observations | Failures | With trace |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    query = """
        SELECT repo,
               COUNT(DISTINCT run_id),
               COUNT(DISTINCT identity),
               SUM(source_format <> 'derived'),
               SUM(status IN ('failed','broken')),
               SUM(has_trace)
        FROM results GROUP BY repo ORDER BY repo
    """
    for repo, runs, tests, observed, failures, traced in connection.execute(query):
        lines.append(
            f"| {_code(repo, codes)} | {runs} | {tests} | "
            f"{observed} | {failures} | {traced} |"
        )
    return lines + [""]


def same_input_disagreement(connection: sqlite3.Connection) -> list[str]:
    """Find tests that disagreed with themselves on identical input.

    A build matrix runs the same commit several ways in one workflow run, so a
    test that passes on one leg and fails on another has genuinely disagreed
    with itself under identical code. That is the only evidence of flakiness the
    portfolio currently produces, because no suite reruns a failure.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Same-input disagreement", ""]
    query = """
        SELECT repo, run_id, identity, params, status FROM results
        WHERE source_format <> 'derived' ORDER BY repo, run_id
    """
    grouped: dict[tuple[str, int, str, str], set[str]] = {}
    for repo, run_id, identity, params, status in connection.execute(query):
        grouped.setdefault((repo, run_id, identity, _case_key(params)), set()).add(status)

    rows = [
        (repo, run_id, identity, len(statuses), ",".join(sorted(statuses)))
        for (repo, run_id, identity, _case), statuses in sorted(grouped.items())
        if len(statuses) > 1
    ]
    if not rows:
        lines.append(
            "None. Every test that ran more than once within a single workflow run - "
            "which today means PublicAP's four-leg matrix, the only place the same "
            "commit is exercised more than once - agreed with itself every time."
        )
        lines.append("")
        lines.append(
            "This is the strongest statement the data currently supports, and it is a "
            "narrow one. No suite reruns a failure, so a test that fails once and "
            "passes on retry would never be observed doing so. Absence of "
            "disagreement here is not evidence that no test is flaky; it is evidence "
            "that none disagreed across operating system and Python version."
        )
        return lines + [""]

    labels = test_labels(connection)
    codes = repo_codes()
    lines.append("| Repo | Run | Test ID | Test | Statuses |")
    lines.append("|---|---:|---|---|---|")
    for repo, run_id, identity, _variants, statuses in rows:
        lines.append(
            f"| {_code(repo, codes)} | {run_id} | {_cells(repo, identity, labels)} | "
            f"{statuses} |"
        )
    return lines + [""]


def volatility(connection: sqlite3.Connection) -> list[str]:
    """Rank tests by how often their outcome changed between consecutive runs.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Outcome volatility", ""]
    query = """
        SELECT r.repo, r.identity, ru.started_at, r.status
        FROM results r JOIN runs ru ON ru.repo = r.repo AND ru.run_id = r.run_id
        WHERE r.source_format <> 'derived'
        ORDER BY r.repo, r.identity, ru.started_at
    """
    history: dict[tuple[str, str], list[str]] = {}
    for repo, identity, _when, status in connection.execute(query):
        history.setdefault((repo, identity), []).append(status)

    ranked = []
    for (repo, identity), statuses in history.items():
        if len(statuses) < 2:
            continue
        flips = sum(1 for before, after in zip(statuses, statuses[1:]) if before != after)
        if flips:
            ranked.append((flips, len(statuses), repo, identity))
    ranked.sort(reverse=True)

    if not ranked:
        lines.append("No test changed outcome between consecutive runs.")
        return lines + [""]

    lines.append(
        "How often a test's outcome changed from one run to the next. This is a "
        "signal, not a verdict: a test that broke and was fixed flips exactly as "
        "much as a test that is genuinely unstable, and only the same-input section "
        "above can tell them apart."
    )
    lines.append("")
    labels = test_labels(connection)
    codes = repo_codes()
    lines.append("| Repo | Test ID | Test | Flips | Observations |")
    lines.append("|---|---|---|---:|---:|")
    for flips, observations, repo, identity in ranked[:TOP_N]:
        lines.append(
            f"| {_code(repo, codes)} | {_cells(repo, identity, labels)} | {flips} | "
            f"{observations} |"
        )
    return lines + [""]


def duration_drift(connection: sqlite3.Connection) -> list[str]:
    """Report the slowest tests and how far their tail sits above their median.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Duration", ""]
    query = """
        SELECT repo, identity, duration_ms FROM results
        WHERE duration_ms IS NOT NULL AND source_format <> 'derived' AND status = 'passed'
    """
    samples: dict[tuple[str, str], list[float]] = {}
    for repo, identity, duration in connection.execute(query):
        samples.setdefault((repo, identity), []).append(float(duration))

    ranked = []
    for (repo, identity), values in samples.items():
        if len(values) < MIN_DURATION_SAMPLES:
            continue
        median = statistics.median(values)
        tail = _percentile(values, 0.95)
        ratio = tail / median if median else 0.0
        ranked.append((ratio, median, tail, len(values), repo, identity))
    ranked.sort(reverse=True)

    if not ranked:
        lines.append(f"No test has the {MIN_DURATION_SAMPLES} passing observations required.")
        return lines + [""]

    lines.append(
        f"Passing runs only, for tests with at least {MIN_DURATION_SAMPLES} of them - a "
        "failed test's duration measures where it gave up, not what it costs. Ranked "
        "by how far the 95th percentile sits above the median, which finds tests that "
        "are usually fast and occasionally are not."
    )
    lines.append("")
    labels = test_labels(connection)
    codes = repo_codes()
    lines.append("| Repo | Test ID | Test | Median | p95 | p95/median | Runs |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for ratio, median, tail, count, repo, identity in ranked[:TOP_N]:
        lines.append(
            f"| {_code(repo, codes)} | {_cells(repo, identity, labels)} | {median:.0f} ms | "
            f"{tail:.0f} ms | {ratio:.1f}x | {count} |"
        )
    return lines + [""]


def failure_inventory(connection: sqlite3.Connection) -> list[str]:
    """List every recorded failure, since there are few enough to list.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Failures", ""]
    placeholders = ", ".join("?" for _ in BAD_STATUSES)
    query = f"""
        SELECT r.repo, r.identity, r.status, COUNT(*) AS hits, SUM(r.has_trace)
        FROM results r WHERE r.status IN ({placeholders})
        GROUP BY r.repo, r.identity, r.status ORDER BY hits DESC, r.repo
    """
    rows = list(connection.execute(query, BAD_STATUSES))
    total = sum(row[3] for row in rows)
    if not rows:
        lines.append("No failures recorded.")
        return lines + [""]

    lines.append(
        f"**{total} failing observations** across {len(rows)} distinct test/status "
        "pairs. Listed in full rather than charted: a few dozen events is a list, and "
        "plotting it would suggest a trend the data cannot support."
    )
    lines.append("")
    labels = test_labels(connection)
    codes = repo_codes()
    lines.append("| Repo | Test ID | Test | Status | Count | With trace |")
    lines.append("|---|---|---|---|---:|---:|")
    for repo, identity, status, hits, traced in rows:
        lines.append(
            f"| {_code(repo, codes)} | {_cells(repo, identity, labels)} | {status} | "
            f"{hits} | {traced} |"
        )
    return lines + [""]


def coverage_gaps(connection: sqlite3.Connection) -> list[str]:
    """Report what the record cannot answer, and why.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## What this record cannot tell you", ""]
    lines.append("| Repo | Observations | With steps | With assigned ID | Formats |")
    lines.append("|---|---:|---:|---:|---|")
    query = """
        SELECT repo, COUNT(*), SUM(has_steps), SUM(test_id IS NOT NULL),
               GROUP_CONCAT(DISTINCT source_format)
        FROM results WHERE source_format <> 'derived' GROUP BY repo ORDER BY repo
    """
    for repo, observed, stepped, identified, formats in connection.execute(query):
        lines.append(
            f"| {repo.split('/')[-1]} | {observed} | {stepped} | {identified} | {formats} |"
        )
    lines.append("")
    lines.append(
        "Assigned IDs are zero everywhere because every row here predates them. The "
        "scheme is live in all four suites now, so rows gathered from the next run "
        "onward will carry one; these never can, which is why reports key on "
        "`COALESCE(test_id, test_uid)`."
    )
    lines.append("")
    lines.append(
        "Step coverage is uneven by format, not by choice: JUnit cannot express steps "
        "at all, and Allure records them only where a suite used `allure.step`. A "
        "step-level statistic computed over the whole corpus would silently describe "
        "the subset that has them."
    )

    not_run = connection.execute(
        "SELECT COUNT(*) FROM results WHERE status = 'not_run'"
    ).fetchone()[0]
    lines.append("")
    lines.append(
        f"**{not_run} absence(s) recorded as `not_run`.** A test missing from a run "
        "inside its own observed lifetime - it existed before, it exists after, and "
        "that run did not report it. Absences outside that window are births and "
        "deaths rather than skipped work, and are deliberately not synthesized."
    )
    return lines + [""]


def anomalies(connection: sqlite3.Connection) -> list[str]:
    """Report artifacts that existed but yielded no results.

    Args:
        connection: Open index connection.

    Returns:
        Report lines.
    """
    lines = ["## Artifacts that yielded nothing", ""]
    rows = list(
        connection.execute(
            "SELECT repo, run_id, artifact_name, reason, created_at, run_conclusion,"
            " detail FROM anomalies ORDER BY created_at"
        )
    )
    if not rows:
        lines.append("None recorded.")
        return lines + [""]

    lines.append("| Repo | Run | Artifact | Reason | Created | Run outcome |")
    lines.append("|---|---:|---|---|---|---|")
    for repo, run_id, name, reason, created, conclusion, _detail in rows:
        lines.append(
            f"| {repo.split('/')[-1]} | {run_id} | {name} | {reason} | "
            f"{(created or '')[:10]} | {conclusion} |"
        )
    lines.append("")
    lines.append(
        "An expired artifact is routine. An artifact that exists while containing no "
        "results is not: the upload step ran, so the job believed it had something to "
        "publish. Each row records the pattern that missed and a sample of what the "
        "archive actually held, because a wrong glob and an empty report look "
        "identical from the outside and want opposite fixes."
    )
    return lines + [""]


def build_report(index_path: Path) -> str:
    """Render the full report.

    Args:
        index_path: Location of the derived index.

    Returns:
        The report as Markdown.
    """
    connection = sqlite3.connect(index_path)
    try:
        sections: list[list[str]] = [
            ["# Portfolio Test Insights", ""],
            repositories(connection),
            inventory(connection),
            same_input_disagreement(connection),
            failure_inventory(connection),
            volatility(connection),
            duration_drift(connection),
            coverage_gaps(connection),
            anomalies(connection),
        ]
    finally:
        connection.close()
    return "\n".join(line for section in sections for line in section)


def main(argv: list[str] | None = None) -> int:
    """Render the report from the command line.

    Args:
        argv: Argument vector, defaulting to ``sys.argv``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m collector.reports",
        description="Render descriptive reliability reports from the derived index.",
    )
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--out", type=Path, default=None, help="Write Markdown here.")
    parser.add_argument(
        "--html", type=Path, default=None, help="Write a self-contained HTML page here."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.index.exists():
        LOGGER.error("No index at %s. Run `make db` first.", args.index)
        return 1

    report = build_report(args.index)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n", encoding="utf-8")
        LOGGER.info("Wrote %s", args.out)
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_page(report, SOURCE_URL), encoding="utf-8")
        LOGGER.info("Wrote %s", args.html)
    if not args.out and not args.html:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
