"""Record types shared by the parsers, the store, and the ingest orchestration.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains the shared status vocabulary (``PASSED``, ``FAILED``,
``BROKEN``, ``SKIPPED``, ``UNKNOWN``) and ``TEST_ID_PATTERN``; the name helpers
``normalize_status``, ``split_full_name``, ``strip_parameters`` and
``params_hash``; and the three frozen records :class:`ParsedStep`,
:class:`ParsedResult` and :class:`RunRecord`.

The parsers run first and build ``ParsedResult`` and ``ParsedStep`` values from
whichever format an artifact carries. Ingestion then builds a ``RunRecord`` per
Actions run and renders both through ``to_row`` into the NDJSON record, which
the index and every report read. A field a record does not carry is a column no
later step can fill.

**Identity is built from the module path and function name only.**
``test_uid`` is ``{repo}::{module}::{test_name}``; parameters live in
``params`` and are hashed into the row key by ``params_hash``, and the display
name, which may be an ``@allure.title``, takes no part.

**Status has one vocabulary.** ``normalize_status`` maps every source spelling
onto the five constants, and anything unrecognized becomes ``unknown``.
``not_run`` is not produced here; the index synthesizes it.

Specified by ``DESIGN.md`` sections 6 and 7.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

#: STATUS VOCABULARY every parser normalizes onto. ``not_run`` is never produced
#: by a parser; the derived layer synthesizes it for a test absent from a run it
#: was expected to appear in (``DESIGN.md`` section 6).
PASSED: Final[str] = "passed"
FAILED: Final[str] = "failed"
BROKEN: Final[str] = "broken"
SKIPPED: Final[str] = "skipped"
UNKNOWN: Final[str] = "unknown"

#: Maps the vocabularies of the three source formats onto the values above.
_STATUS_ALIASES: Final[dict[str, str]] = {
    "passed": PASSED,
    "pass": PASSED,
    "success": PASSED,
    "failed": FAILED,
    "failure": FAILED,
    "broken": BROKEN,
    "error": BROKEN,
    "skipped": SKIPPED,
    "skip": SKIPPED,
    "unknown": UNKNOWN,
}

#: Splits an Allure ``fullName`` (``module.path#test_name``) into its two halves.
_FULL_NAME_SEPARATOR: Final[str] = "#"

#: Captures a trailing pytest parameter id: ``test_x[germany]`` -> ``germany``.
_PARAM_SUFFIX: Final[re.Pattern[str]] = re.compile(r"^(?P<base>.+?)\[(?P<params>.*)\]$")

#: ASSIGNED TEST ID SHAPE, per ``DESIGN.md`` section 7: three or four capitals,
#: an underscore, five to eight digits, e.g. ``PAWA_10001``.
TEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3,4}_\d{5,8}$")


def normalize_status(raw: str | None) -> str:
    """Map a source-format status onto the shared vocabulary.

    Args:
        raw (str | None): Status string as the source format spelled it, or
            None.

    Returns:
        str: One of the module-level status constants, matched case- and
        whitespace-insensitively. None, an empty string, or an unrecognized
        spelling yields ``unknown``.
    """
    if not raw:
        return UNKNOWN
    return _STATUS_ALIASES.get(raw.strip().lower(), UNKNOWN)


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split an Allure ``fullName`` into its module path and test name.

    Args:
        full_name (str): For example
            ``tests.test_config.TestValidation#test_duplicate``.

    Returns:
        tuple[str, str]: A ``(module, test_name)`` pair, split at the first
        ``#``. Without a separator the whole string is the test name and the
        module is empty, never invented.
    """
    if _FULL_NAME_SEPARATOR in full_name:
        module, _, test_name = full_name.partition(_FULL_NAME_SEPARATOR)
        return module.strip(), test_name.strip()
    return "", full_name.strip()


def strip_parameters(name: str) -> tuple[str, str | None]:
    """Separate a parametrized test name from its bracketed parameter id.

    Args:
        name (str): For example ``test_country_by_name[germany]``.

    Returns:
        tuple[str, str | None]: A ``(base_name, parameter_id)`` pair, the id
        None when the name carries no trailing brackets. Every case of a
        parametrized test therefore shares one base name.
    """
    match = _PARAM_SUFFIX.match(name.strip())
    if match is None:
        return name.strip(), None
    return match.group("base"), match.group("params")


def params_hash(params: dict[str, str]) -> str:
    """Return a stable digest of a parameter set, used as part of the row key.

    Args:
        params (dict[str, str]): Parameter name to value mapping.

    Returns:
        str: The first 12 hex characters of a SHA-1 over the key-sorted JSON,
        so the same parameters in a different order hash the same.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


@dataclass(frozen=True)
class ParsedStep:
    """One ``allure.step`` recorded inside a test result.

    Attributes:
        ordinal (int): Position among the steps of this test, depth-first,
            starting at zero.
        name (str): The step's declared name.
        status (str): Normalized status.
        duration_ms (int | None): Wall time, or None when the format did not
            record it.
        parent_ordinal (int | None): Ordinal of the enclosing step, or None at
            the top level.
    """

    ordinal: int
    name: str
    status: str
    duration_ms: int | None = None
    parent_ordinal: int | None = None


# Added 2026-11-10, when ``steps_omitted`` became the thirteenth field.
# A plain record of one result's fields, one per column of the row it becomes.
# Splitting it to satisfy an attribute count would scatter one row across
# several objects without adding any behaviour.
@dataclass(frozen=True)
class ParsedResult:  # pylint: disable=too-many-instance-attributes
    """One test outcome, normalized away from whichever format produced it.

    Attributes:
        module (str): Dotted module path, possibly including a class.
        test_name (str): Test function name with parameters removed.
        params (dict[str, str]): Parameters, merged from the artifact name and
            the test itself.
        status (str): Normalized status.
        display_name (str): Human-facing name, which may be an
            ``allure.title`` and is never used as identity.
        duration_ms (int | None): Wall time, or None when unavailable.
        message (str | None): Failure text, or None.
        trace (str | None): The stack trace of a non-passing result, verbatim,
            or None.
        labels (dict[str, str]): Allure labels such as epic, feature, story,
            severity; empty for JUnit.
        history_id (str | None): The source format's own identity hash, kept
            for reference only.
        test_id (str | None): Assigned stable ID such as ``PAWA_10001`` when
            the test carries one; None for every artifact produced before the
            scheme existed, which is all backfilled history.
        steps (tuple[ParsedStep, ...]): Recorded steps, empty when the format
            carries none or the policy dropped them.
        steps_omitted (bool): True when the source recorded steps and the
            collector dropped them by policy: a passing or skipped result from
            an artifact whose step policy is ``failures`` (``DESIGN.md``
            section 8).
    """

    module: str
    test_name: str
    params: dict[str, str]
    status: str
    display_name: str
    duration_ms: int | None = None
    message: str | None = None
    trace: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    history_id: str | None = None
    test_id: str | None = None
    steps: tuple[ParsedStep, ...] = ()
    steps_omitted: bool = False

    def test_uid(self, repo: str) -> str:
        """Build the cross-run identity for this result.

        Args:
            repo (str): Owner-qualified repository name.

        Returns:
            str: ``{repo}::{module}::{test_name}``. Parameters and the display
            name take no part, so every case of a parametrized test shares one
            identity and a retitled test keeps it.
        """
        return f"{repo}::{self.module}::{self.test_name}"

    def to_row(self, repo: str, run_id: int, attempt: int, source_format: str) -> dict[str, Any]:
        """Render this result as the JSON object written to the durable log.

        Args:
            repo (str): Owner-qualified repository name.
            run_id (int): Actions run this result came from.
            attempt (int): Run attempt number.
            source_format (str): The parser that produced it, so a gap in the
                data can be attributed to the format rather than the test.

        Returns:
            dict[str, Any]: A JSON-serializable dict, one line of NDJSON.
            ``has_trace`` is true when a trace is present, and ``has_steps`` is
            true when steps are present or were omitted by policy.
        """
        return {
            "repo": repo,
            "run_id": run_id,
            "attempt": attempt,
            "test_uid": self.test_uid(repo),
            "test_id": self.test_id,
            "module": self.module,
            "test_name": self.test_name,
            "display_name": self.display_name,
            "params": self.params,
            "params_hash": params_hash(self.params),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "trace": self.trace,
            "has_trace": bool(self.trace),
            "labels": self.labels,
            "history_id": self.history_id,
            "source_format": source_format,
            "has_steps": bool(self.steps) or self.steps_omitted,
            "steps_omitted": self.steps_omitted,
            "steps": [
                {
                    "ordinal": step.ordinal,
                    "name": step.name,
                    "status": step.status,
                    "duration_ms": step.duration_ms,
                    "parent_ordinal": step.parent_ordinal,
                }
                for step in self.steps
            ],
        }


@dataclass(frozen=True)
class RunRecord:
    """One Actions workflow run, with the fields a report needs to group by.

    Attributes:
        repo (str): Owner-qualified repository name.
        run_id (int): Actions run id.
        attempt (int): Run attempt number.
        workflow (str): Workflow name. Not used as identity, since one
            repository has run under two workflow names (``DESIGN.md``
            section 8).
        event (str): Trigger event.
        head_sha (str): The test code's commit. For dispatch-driven suites this
            is not the version of the system under test (``DESIGN.md``
            section 7).
        status (str): Actions status.
        conclusion (str | None): Actions conclusion, None while a run is in
            flight.
        started_at (str): UTC ISO-8601 timestamp.
        updated_at (str): UTC ISO-8601 timestamp.
        sut_version (str | None): Version of the system actually under test
            when it can be resolved; otherwise None, never ``head_sha``.
    """

    repo: str
    run_id: int
    attempt: int
    workflow: str
    event: str
    head_sha: str
    status: str
    conclusion: str | None
    started_at: str
    updated_at: str
    sut_version: str | None = None

    def to_row(self) -> dict[str, Any]:
        """Render this run as the JSON object written to the durable log.

        Returns:
            dict[str, Any]: A JSON-serializable dict, one line of NDJSON.
        """
        return {
            "repo": self.repo,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "workflow": self.workflow,
            "event": self.event,
            "head_sha": self.head_sha,
            "status": self.status,
            "conclusion": self.conclusion,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "sut_version": self.sut_version,
        }
