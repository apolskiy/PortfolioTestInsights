"""Descriptive reports over the derived index.

Created: 2026-08-16
Last updated: 2026-11-12

This file contains the command line ``python -m collector.reports`` (``main``);
``build_report``, which assembles the full Markdown report; one function per
report section (``repositories``, ``inventory``, ``same_input_disagreement``,
``failure_inventory``, ``volatility``, ``duration_drift``, ``error_budget``,
``coverage_gaps``, ``anomalies``); and the helpers that name a test and a
repository in a table (``test_labels``, ``repo_codes``).

It runs last (``make report``, ``make site``): it reads only
``build/results.sqlite``, which ``make db`` built from ``data/``, plus
``config/sources.yaml`` for repository codes and notes and the error-budget
policy. The
Markdown it returns is written to ``--out`` or handed to
:func:`collector.render.render_page` for ``--html``; nothing here writes to the
record or gates any build.

**Counts and order statistics only.** Every figure is a count, a median or a
nearest-rank percentile; nothing is modelled or extrapolated, and where a
section has nothing to show it prints a sentence saying so and why.

**Same-input disagreement and volatility are separate sections.** The first
groups observed rows by run, test and test case, varying only the environment
parameters in ``ENVIRONMENT_PARAMS``, and lists any group with more than one
status. The second counts outcome changes between consecutive runs and is
labelled a signal, not a verdict.

**Every per-test table prints the repository code, the assigned ID and the test
name.** The code comes from :func:`repo_codes`, the ID and name from
:func:`test_labels`, and a test without an ID prints ``NO_ID``.

Specified by ``DESIGN.md`` section 10.
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

from collector.config import SloPolicy, load_slo, load_sources
from collector.index import DEFAULT_INDEX_PATH
from collector.render import render_page

LOGGER = logging.getLogger(__name__)

#: Minimum passing observations before a test's duration summary is printed.
MIN_DURATION_SAMPLES: Final[int] = 8

#: How many rows each ranked section prints.
TOP_N: Final[int] = 10

#: Statuses that count as a test having gone wrong.
BAD_STATUSES: Final[tuple[str, ...]] = ("failed", "broken")

#: Linked in the generated page so a reader can check figures against the record.
SOURCE_URL: Final[str] = "https://github.com/apolskiy/PortfolioTestInsights"

#: ENVIRONMENT PARAMETERS, per ``DESIGN.md`` section 10: those describing
#: *where* a test ran rather than *what it ran against*. They come from the
#: artifact name, a build matrix leg or a browser engine, and are the only axes
#: a same-input comparison lets vary; every other parameter is held fixed.
ENVIRONMENT_PARAMS: Final[frozenset[str]] = frozenset({"os", "python", "browser"})


def _case_key(params_json: str) -> str:
    """Reduce a parameter set to the part that identifies the test case.

    Args:
        params_json (str): The row's serialized parameters.

    Returns:
        str: Key-sorted JSON over the parameters not in
        ``ENVIRONMENT_PARAMS``, so two rows share it exactly when they
        exercised the same case. Text that is not valid JSON is returned as
        it is.
    """
    try:
        params = json.loads(params_json or "{}")
    except json.JSONDecodeError:
        return params_json or ""
    kept = {key: value for key, value in params.items() if key not in ENVIRONMENT_PARAMS}
    return json.dumps(kept, sort_keys=True)


def _percentile(values: list[float], fraction: float) -> float:
    """Return a percentile by nearest rank, without interpolating.

    Design: ``DESIGN.md`` section 10.

    Args:
        values (list[float]): Samples, not required to be sorted; at least one.
        fraction (float): Percentile as a fraction, for example 0.95.

    Returns:
        float: The sample at rank ``round(fraction * n)``, clamped to the first
        and last sample, so the result is always a measured value.
    """
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[rank]


#: Printed where a test has no assigned ID, which is every row recorded before
#: the scheme existed.
NO_ID: Final[str] = "-"


def repo_codes() -> dict[str, str]:
    """Map each repository to the short code the tables print for it.

    Design: ``DESIGN.md`` section 10.2.

    Returns:
        dict[str, str]: Owner-qualified repository name to
        :attr:`SourceSpec.display_code`, for every configured source.
        Repositories missing from the configuration are absent, and
        :func:`_code` falls back to their bare name.

    Raises:
        ConfigError: If the source configuration is malformed.
    """
    return {source.repo: source.display_code for source in load_sources()}


def _code(repo: str, codes: dict[str, str]) -> str:
    """Render one repository cell.

    Args:
        repo (str): Owner-qualified repository name.
        codes (dict[str, str]): Lookup from :func:`repo_codes`.

    Returns:
        str: The short code, or the bare repository name when none is
        configured.
    """
    return codes.get(repo, repo.split("/")[-1])


def _short(identity: str) -> str:
    """Trim a repo-qualified identity down to something a table can hold.

    Args:
        identity (str): Either an assigned ID or a ``repo::module::name`` uid.

    Returns:
        str: Assigned IDs unchanged; for a uid, the part after its last ``::``.
    """
    if "::" not in identity:
        return identity
    return identity.rsplit("::", 1)[-1]


def test_labels(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    """Map each identity to the ID and the test name a reader needs to see.

    Design: ``DESIGN.md`` section 10.2.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        dict[tuple[str, str], tuple[str, str]]: ``(repo, identity)`` to
        ``(test id or NO_ID, test name)``. The test name falls back to the
        trimmed identity when no row carries one.
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
        repo (str): Owning repository.
        identity (str): The row's grouping identity.
        labels (dict[tuple[str, str], tuple[str, str]]): Lookup from
            :func:`test_labels`.

    Returns:
        str: Two pipe-separated table cells: assigned ID, then test name.
    """
    test_id, test_name = labels.get((repo, identity), (NO_ID, _short(identity)))
    return f"{test_id} | {test_name}"


def repositories(connection: sqlite3.Connection) -> list[str]:
    """Publish the code-to-repository mapping as a legend, once.

    One row per repository the index holds, with its code, a link, its test
    count, and the range of assigned IDs in use plus how many tests carry none.
    A configured source never collected is not listed. Under the table, one
    bullet per listed repository whose source carries a ``note``. Design:
    ``DESIGN.md`` section 10.2.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.

    Raises:
        ConfigError: If the source configuration is malformed.
    """
    sources = load_sources()
    codes = {source.repo: source.display_code for source in sources}
    notes = {source.repo: source.note for source in sources if source.note}
    listed: list[str] = []
    lines = ["## Repositories", ""]
    lines.append(
        "Each code is the prefix of that suite's assigned test IDs, so a code in any "
        "table agrees with the IDs printed beside it - except where two suites share "
        "a prefix, which are given distinct codes instead. The range shows which IDs that "
        "suite has actually published so far - a test recorded before the scheme "
        "existed carries none, and appears in the tables with `-` in its ID column."
    )
    lines.append("")
    lines.append("| Code | Repository | Tests | Assigned IDs in use |")
    lines.append("|---|---|---:|---|")
    # Added 2026-08-17. Collapse to one row per test first: a test's assigned
    # ID lives on its rows recorded since the scheme existed while its older
    # rows still carry NULL, so counting NULLs directly would report every test
    # as unassigned.
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
        listed.append(repo)

    noted = [repo for repo in listed if repo in notes]
    if noted:
        lines.append("")
        lines.extend(f"- **{_code(repo, codes)}.** {notes[repo]}" for repo in noted)
    return lines + [""]


def inventory(connection: sqlite3.Connection) -> list[str]:
    """Summarize what the record holds, per repository.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines: one table row per repository with its runs,
        distinct tests, observed (non-derived) rows, failed or broken rows,
        and rows carrying a trace.
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

    Observed rows are grouped by repository, run, identity and test case
    (:func:`_case_key`); a group holding more than one status is a
    disagreement. When none exists the section says so and states how narrow
    that evidence is. Design: ``DESIGN.md`` section 10.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.
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

    Observed rows are ordered by run start time per test; a flip is a status
    different from the one before it. The ``TOP_N`` tests with the most flips
    are listed, ties broken by observation count.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.
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


def error_budget(connection: sqlite3.Connection) -> list[str]:
    """Report how much of each test's duration error budget has been spent.

    The section states the policy loaded from ``config/sources.yaml``, then
    lists the ``TOP_N`` tests that breached at least once, worst first, each
    marked ``spent`` when its breach fraction exceeds the budget. With no test
    judgeable, or none breaching, it says which. It reports and never gates.
    Design: ``DESIGN.md`` section 10.1.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.

    Raises:
        ConfigError: If the source configuration is malformed.
    """
    policy = load_slo()
    codes = repo_codes()
    labels = test_labels(connection)
    lines = ["## Duration error budget", ""]
    lines.append(
        f"A run breaches when it exceeds **{policy.tolerance:g}x** that test's baseline "
        f"median. The budget allows **{policy.budget:.0%}** of the trailing "
        f"**{policy.window_runs}** runs to breach before it is spent. Tests with fewer "
        f"than {policy.min_observations} observations in the window, or a baseline median "
        f"under {policy.floor_ms:g} ms, are omitted - a multiple of a millisecond is "
        "measurement noise rather than degradation."
    )
    lines.append("")

    rows = _budget_rows(connection, policy)
    if not rows:
        lines.append("No test has enough observations in the window to support a verdict.")
        return lines + [""]

    # Added 2026-08-17. Only tests that actually breached are listed; a table of
    # identical zeroes is not a finding (DESIGN.md section 10.1).
    breaching = [row for row in rows if row[1]]
    spent = [row for row in rows if row[0] > policy.budget]

    if not breaching:
        lines.append(
            f"**No test breached its objective in the trailing window.** "
            f"{len(rows)} tests carried enough observations to judge; the rest were "
            "omitted by the thresholds above rather than passing quietly."
        )
        return lines + [""]

    lines.append("| Repo | Test ID | Test | Window | Breaches | Budget used | Verdict |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for consumed, breaches, seen, repo, identity in breaching[:TOP_N]:
        verdict = "**spent**" if consumed > policy.budget else "within budget"
        lines.append(
            f"| {_code(repo, codes)} | {_cells(repo, identity, labels)} | {seen} | "
            f"{breaches} | {consumed:.0%} | {verdict} |"
        )
    lines.append("")
    lines.append(
        f"**{len(spent)} of {len(rows)}** tests with a verdict have spent their budget; "
        f"{len(breaching)} breached at least once."
    )
    return lines + [""]


def _budget_rows(
    connection: sqlite3.Connection, policy: SloPolicy
) -> list[tuple[float, int, int, str, str]]:
    """Compute budget consumption per test over the trailing window.

    Only observed, passing rows with a duration count. The window is the last
    ``window_runs`` of them by run start time; the baseline is the median over
    all of them; a run breaches when it exceeds ``tolerance`` times the
    baseline. A test is skipped when its window holds fewer than
    ``min_observations`` runs or its baseline is under ``floor_ms``.

    Args:
        connection (sqlite3.Connection): Open index connection.
        policy (SloPolicy): The error-budget policy.

    Returns:
        list[tuple[float, int, int, str, str]]: ``(consumed, breaches,
        observations, repo, identity)`` sorted worst first, where ``consumed``
        is breaches over window size.
    """
    query = """
        SELECT r.repo, r.identity, ru.started_at, r.duration_ms
        FROM results r JOIN runs ru ON ru.repo = r.repo AND ru.run_id = r.run_id
        WHERE r.duration_ms IS NOT NULL AND r.source_format <> 'derived'
          AND r.status = 'passed'
        ORDER BY ru.started_at
    """
    history: dict[tuple[str, str], list[float]] = {}
    for repo, identity, _when, duration in connection.execute(query):
        history.setdefault((repo, identity), []).append(float(duration))

    rows: list[tuple[float, int, int, str, str]] = []
    for (repo, identity), durations in history.items():
        window = durations[-policy.window_runs :]
        if len(window) < policy.min_observations:
            continue
        baseline = statistics.median(durations)
        if baseline < policy.floor_ms:
            continue
        limit = baseline * policy.tolerance
        breaches = sum(1 for value in window if value > limit)
        rows.append((breaches / len(window), breaches, len(window), repo, identity))
    rows.sort(reverse=True)
    return rows


def duration_drift(connection: sqlite3.Connection) -> list[str]:
    """Report the slowest tests and how far their tail sits above their median.

    Only observed, passing rows with a duration count, and only tests with at
    least ``MIN_DURATION_SAMPLES`` of them. The ``TOP_N`` tests are ranked by
    p95 over median, a zero median ranking as 0.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.
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
    """List every recorded failure in full, grouped by test and status.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines: the total of failed and broken rows, then one
        table row per test and status, most frequent first, with how many of
        them carry a trace.
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

    Per repository: observed rows, rows with steps, rows with an assigned ID,
    and the source formats seen; then the reasons for each gap and the count
    of synthesized ``not_run`` rows.

    Args:
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines.
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
        "The assigned-ID column counts rows carrying one, not tests that have one. "
        "Every suite publishes IDs, but every row backfilled before 2026-08-16 "
        "predates the scheme and can never gain one, since the artifacts are frozen "
        "and some have expired. Identity is therefore not `COALESCE(test_id, "
        "test_uid)` - that would key earlier rows by uid and later rows by ID, "
        "splitting one long history into two short ones at the changeover. An ID "
        "observed anywhere for a test is applied to every row for that test instead."
    )
    lines.append("")
    lines.append(
        "Step coverage is uneven by format: JUnit cannot express steps at all, and "
        "Allure records them only where a suite used `allure.step`. One gap is by "
        "choice: for the Multi-LLM Evaluation suites, a passing result's steps are "
        "dropped and only failures keep theirs, because those suites record every "
        "step of every test and the steps were most of the record's size. Such rows "
        "still count under With steps, since the source recorded them, and carry "
        "`steps_omitted`. A step-level statistic computed over the whole corpus would "
        "silently describe the subset that has them."
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
        connection (sqlite3.Connection): Open index connection.

    Returns:
        list[str]: Report lines: one table row per anomaly, oldest first, with
        its run, artifact, reason, creation date and run outcome, or a single
        line when none is recorded.
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
        index_path (Path): Location of the derived index.

    Returns:
        str: The report as Markdown: a title, then the repositories legend,
        inventory, same-input disagreement, failures, volatility, duration,
        error budget, coverage gaps and anomalies sections, in that order.

    Raises:
        ConfigError: If the source configuration is malformed.
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
            error_budget(connection),
            coverage_gaps(connection),
            anomalies(connection),
        ]
    finally:
        connection.close()
    return "\n".join(line for section in sections for line in section)


def main(argv: list[str] | None = None) -> int:
    """Render the report from the command line.

    Writes Markdown to ``--out``, an HTML page to ``--html``, or, with
    neither, prints the Markdown.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv``.

    Returns:
        int: Process exit code: 0 once the report is rendered, 1 when the index
        does not exist.
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
