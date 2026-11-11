"""Parser for Allure's generated report (``data/test-cases/*.json``).

Created: 2026-08-16
Last updated: 2026-11-10

This file contains ``parse``, the entry point registered as ``allure_report``;
``_is_retained_history``, which recognizes a document an earlier run left in
the report; and the private readers for the fields this schema places
differently from raw results.

Ingestion calls ``parse`` with the test-case documents of an artifact produced
by ``allure generate``: PlaywrightAPWebsiteAutomation's ``execution-reports``
and CountryWeather's ``qa-artifacts``, the only machine-readable per-test
record the largest suite publishes. Documents retained from earlier runs are
removed first, so they never become rows of this run; the rest go through the
shared loop in :mod:`collector.parsers.allure_common`.

**Retained history is skipped.** A document flagged ``retry: true`` was merged
from the report's ``history/`` directory and belongs to another run.

**Three fields sit elsewhere than in raw results.** Timing arrives as
``time{start, stop, duration}`` and the recorded duration is preferred; steps
are nested under ``testStage``; and ``statusMessage`` and ``statusTrace`` are
read from the test case, or from its ``testStage`` when the case carries none.

Specified by ``DESIGN.md`` sections 4 and 4.2.
"""

from __future__ import annotations

import json
from typing import Any, Final

from collector.models import ParsedResult
from collector.parsers.allure_common import FormatFields, parse_documents

#: Added 2026-08-16. RETAINED HISTORY, per ``DESIGN.md`` section 4.2:
#: ``allure generate`` merges a ``history/`` directory, so a generated report
#: holds this run's results and entries retained from earlier runs, the latter
#: flagged ``retry: true``. They are not retries within the run and not this
#: run's evidence, so a document whose ``retry`` is ``True`` is skipped. Raw
#: results carry no merged history, so only this parser needs the guard.
RETAINED_HISTORY_KEY: Final[str] = "retry"


def parse(members: dict[str, bytes], base_params: dict[str, str]) -> list[ParsedResult]:
    """Parse every report test-case document in an extracted artifact.

    Args:
        members (dict[str, bytes]): Archive member path to file contents.
        base_params (dict[str, str]): Parameters derived from the artifact name,
            merged into every result.

    Returns:
        list[ParsedResult]: One result per readable member that belongs to
        *this* run. Documents retained from earlier runs by Allure's history
        merge are excluded; see :data:`RETAINED_HISTORY_KEY`.
    """
    current = {
        path: payload
        for path, payload in members.items()
        if not _is_retained_history(payload)
    }
    return parse_documents(current, base_params, _read_format)


def _is_retained_history(content: bytes) -> bool:
    """Report whether a document is an earlier run's result kept by history merge.

    Args:
        content (bytes): Raw bytes of one test-case document.

    Returns:
        bool: True when the document is an object whose ``retry`` is ``True``.
        A document that will not decode returns False, leaving the decision to
        skip it with :func:`parse_documents`.
    """
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get(RETAINED_HISTORY_KEY) is True


def _read_format(payload: dict[str, Any]) -> FormatFields:
    """Read the fields that distinguish the report schema from the raw schema.

    Args:
        payload (dict[str, Any]): The decoded test-case document.

    Returns:
        FormatFields: Duration, failure message, stack trace and the
        ``testStage`` step array, in that order.
    """
    test_stage = payload.get("testStage")
    raw_steps = test_stage.get("steps") if isinstance(test_stage, dict) else None
    message = _detail(payload, test_stage, "statusMessage")
    trace = _detail(payload, test_stage, "statusTrace")
    return _duration_ms(payload), message, trace, raw_steps


def _duration_ms(payload: dict[str, Any]) -> int | None:
    """Read the duration from the report's ``time`` block.

    Args:
        payload (dict[str, Any]): The decoded test-case document.

    Returns:
        int | None: The recorded ``duration`` when numeric, else
        ``stop - start`` when both are numeric and not inverted, else None. A
        missing ``time`` block yields None.
    """
    time_block = payload.get("time")
    if not isinstance(time_block, dict):
        return None
    duration = time_block.get("duration")
    if isinstance(duration, (int, float)):
        return int(duration)
    start = time_block.get("start")
    stop = time_block.get("stop")
    if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
        span = int(stop) - int(start)
        return span if span >= 0 else None
    return None


def _detail(payload: dict[str, Any], test_stage: Any, key: str) -> str | None:
    """Read a failure detail, which the report may place in either of two spots.

    The generator hangs the message and trace off the test case or off its test
    stage, depending on where the failure was raised, so the case is read first
    and the stage second.

    Args:
        payload (dict[str, Any]): The decoded test-case document.
        test_stage (Any): The ``testStage`` block, when present.
        key (str): Either ``statusMessage`` or ``statusTrace``.

    Returns:
        str | None: The text, or None when neither location carries it.
    """
    detail = payload.get(key)
    if not detail and isinstance(test_stage, dict):
        detail = test_stage.get(key)
    return str(detail) if detail else None
