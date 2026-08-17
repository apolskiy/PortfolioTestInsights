"""Record types shared by the parsers, the store, and the ingest orchestration.

Every record is a frozen dataclass rather than a dict. The three source formats
disagree about almost everything - timing representation, where steps live, what
a name means - so the point at which a parser hands its output on is the point
where those differences must already be resolved. A typed record makes an
unresolved difference a construction error instead of a ``KeyError`` in a report
three steps later.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

#: Status values every parser must normalize onto. ``not_run`` is never produced
#: by a parser - it is synthesized in the derived layer for a test that is absent
#: from a run it was expected to appear in.
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

#: Shape of an assigned test ID, e.g. ``PAWA_10001``. See DESIGN.md section 7.
TEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3,4}_\d{5,8}$")


def normalize_status(raw: str | None) -> str:
    """Map a source-format status onto the shared vocabulary.

    Args:
        raw: Status string as the source format spelled it, or None.

    Returns:
        One of the module-level status constants. Anything unrecognized becomes
        ``unknown`` rather than being guessed at, so an unmapped vocabulary shows
        up in a report as a visible gap instead of a silent pass.
    """
    if not raw:
        return UNKNOWN
    return _STATUS_ALIASES.get(raw.strip().lower(), UNKNOWN)


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split an Allure ``fullName`` into its module path and test name.

    Args:
        full_name: For example ``tests.test_config.TestValidation#test_duplicate``.

    Returns:
        A ``(module, test_name)`` pair. When no separator is present the whole
        string is treated as the test name and the module is empty, which is
        preferable to inventing a module that the source never stated.
    """
    if _FULL_NAME_SEPARATOR in full_name:
        module, _, test_name = full_name.partition(_FULL_NAME_SEPARATOR)
        return module.strip(), test_name.strip()
    return "", full_name.strip()


def strip_parameters(name: str) -> tuple[str, str | None]:
    """Separate a parametrized test name from its bracketed parameter id.

    Args:
        name: For example ``test_country_by_name[germany]``.

    Returns:
        A ``(base_name, parameter_id)`` pair, with the id None when the name
        carries no brackets. Parameters are stored as data rather than left in
        the name so that every case of a parametrized test shares one identity.
    """
    match = _PARAM_SUFFIX.match(name.strip())
    if match is None:
        return name.strip(), None
    return match.group("base"), match.group("params")


def params_hash(params: dict[str, str]) -> str:
    """Return a stable digest of a parameter set, used as part of the row key.

    Args:
        params: Parameter name to value mapping.

    Returns:
        A short hex digest. Keys are sorted before hashing so that two runs
        producing the same parameters in a different order do not look like two
        different rows.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


@dataclass(frozen=True)
class ParsedStep:
    """One ``allure.step`` recorded inside a test result.

    Attributes:
        ordinal: Position among the steps of this test, starting at zero.
        name: The step's declared name.
        status: Normalized status.
        duration_ms: Wall time, or None when the format did not record it.
        parent_ordinal: Ordinal of the enclosing step, or None at the top level.
    """

    ordinal: int
    name: str
    status: str
    duration_ms: int | None = None
    parent_ordinal: int | None = None


@dataclass(frozen=True)
class ParsedResult:
    """One test outcome, normalized away from whichever format produced it.

    Attributes:
        module: Dotted module path, possibly including a class.
        test_name: Test function name with parameters removed.
        params: Parameters, merged from the artifact name and the test itself.
        status: Normalized status.
        display_name: Human-facing name, which may be an ``allure.title`` and
            is therefore never used as identity.
        duration_ms: Wall time, or None when unavailable.
        message: Failure text, or None.
        trace: Captured stack trace for a non-passing result, or None. This is
            the diagnostic payload: a message says a test failed, a trace says
            where. Kept verbatim rather than summarized, because the line that
            explains a flake is rarely the one a summarizer would keep.
        labels: Allure labels such as epic, feature, story, severity.
        history_id: The source format's own identity hash, kept for reference.
        test_id: Assigned stable ID such as ``PAWA_10001``, when the test
            carries one. Null for every artifact produced before the scheme
            existed, which is all backfilled history.
        steps: Recorded steps, empty when the format carries none.
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

    def test_uid(self, repo: str) -> str:
        """Build the cross-run identity for this result.

        Args:
            repo: Owner-qualified repository name.

        Returns:
            ``{repo}::{module}::{test_name}``, derived from the module path and
            function name only. Parameters are excluded so that every case of a
            parametrized test shares one identity, and the display name is
            excluded because it can change without the test changing.
        """
        return f"{repo}::{self.module}::{self.test_name}"

    def to_row(self, repo: str, run_id: int, attempt: int, source_format: str) -> dict[str, Any]:
        """Render this result as the JSON object written to the durable log.

        Args:
            repo: Owner-qualified repository name.
            run_id: Actions run this result came from.
            attempt: Run attempt number.
            source_format: Which parser produced it, so that a later gap in the
                data can be attributed to the format rather than to the test.

        Returns:
            A JSON-serializable dict, one line of NDJSON.
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
            "has_steps": bool(self.steps),
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
        repo: Owner-qualified repository name.
        run_id: Actions run id.
        attempt: Run attempt number.
        workflow: Workflow name. Not used as identity - see DESIGN.md section 8,
            where one repository has run under two workflow names.
        event: Trigger event.
        head_sha: The test code's commit. For dispatch-driven suites this is not
            the version of the system under test; see DESIGN.md section 7.
        status: Actions status.
        conclusion: Actions conclusion, None while a run is in flight.
        started_at: UTC ISO-8601 timestamp.
        updated_at: UTC ISO-8601 timestamp.
        sut_version: Version of the system actually under test, when it can be
            resolved. Deliberately null rather than falling back to head_sha.
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
            A JSON-serializable dict, one line of NDJSON.
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
