"""Parser for raw Allure results (``*-result.json``).

Produced by ``allure-pytest`` directly, and uploaded as-is by
VM-Deployment-and-Configuration and by PlaywrightAPWebsiteAutomation's
``allure-results-*`` artifacts.

Two properties of this format shape the reader. Timing is a ``start``/``stop``
epoch-millisecond pair rather than a duration, and steps sit at the top level
rather than under a test stage. The third property is a trap rather than a
shape: ``name`` may be an ``@allure.title``, so it is a display label and never
an identity - see DESIGN.md section 7.
"""

from __future__ import annotations

from typing import Any

from collector.models import ParsedResult
from collector.parsers.allure_common import FormatFields, parse_documents

#: Raw results sit alongside ``*-container.json`` fixture records in the same
#: directory, so members are filtered by suffix before any parse is attempted.
FILENAME_SUFFIX = "-result.json"


def parse(members: dict[str, bytes], base_params: dict[str, str]) -> list[ParsedResult]:
    """Parse every raw Allure result in an extracted artifact.

    Args:
        members: Archive member path to file contents.
        base_params: Parameters derived from the artifact name, merged into
            every result so that a matrix leg is carried on each row.

    Returns:
        One ParsedResult per readable member.
    """
    return parse_documents(members, base_params, _read_format, path_suffix=FILENAME_SUFFIX)


def _read_format(payload: dict[str, Any]) -> FormatFields:
    """Read the fields that distinguish the raw schema from the report schema.

    Args:
        payload: The decoded ``*-result.json`` object.

    Returns:
        Duration, failure message, stack trace and step array, in that order.
    """
    message, trace = _failure_detail(payload)
    return _duration_ms(payload), message, trace, payload.get("steps")


def _duration_ms(payload: dict[str, Any]) -> int | None:
    """Derive a duration from the format's start/stop pair.

    Args:
        payload: The decoded result object.

    Returns:
        Milliseconds elapsed, or None when either bound is missing or the pair
        is inverted. An inverted pair is a data fault, and returning None keeps
        it out of an average rather than hiding it inside one.
    """
    start = payload.get("start")
    stop = payload.get("stop")
    if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
        span = int(stop) - int(start)
        return span if span >= 0 else None
    return None


def _failure_detail(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Read the failure message and stack trace from ``statusDetails``.

    The raw schema keeps both in one block, which is the richest failure
    diagnostic any of the three formats publishes: ``trace`` is the full pytest
    traceback as the run saw it.

    Args:
        payload: The decoded result object.

    Returns:
        A ``(message, trace)`` pair, either element None when absent.
    """
    status_details = payload.get("statusDetails")
    if not isinstance(status_details, dict):
        return None, None
    message = status_details.get("message")
    trace = status_details.get("trace")
    return (str(message) if message else None, str(trace) if trace else None)
