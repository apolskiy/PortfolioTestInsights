"""Parser for raw Allure results (``*-result.json``).

Created: 2026-08-16
Last updated: 2026-11-10

This file contains ``parse``, the entry point registered as ``allure_raw``, and
the private readers for the fields this schema places differently from the
generated report: duration, failure message, trace and steps.

Ingestion calls ``parse`` with the members of an artifact written by
``allure-pytest`` directly: VM-Deployment-and-Configuration's
``allure-results``, PlaywrightAPWebsiteAutomation's ``allure-results-*``, and
the ``mqc-reports-*`` artifacts of AP-Harness-QC and AP-Model-QC.
The shared loop in :mod:`collector.parsers.allure_common` turns each document
into a result; a document this file cannot time or diagnose still becomes a
row, with those fields None.

**Only ``*-result.json`` members are read.** The ``*-container.json`` fixture
records beside them are skipped by suffix before any parse is attempted.

**Timing is a ``start``/``stop`` pair** in epoch milliseconds, steps sit at the
top level, and both the failure message and the full pytest trace come from
``statusDetails``. ``name`` may be an ``@allure.title``, so it is a display
label and never an identity.

Specified by ``DESIGN.md`` sections 4.1 and 7.
"""

from __future__ import annotations

from typing import Any

from collector.models import ParsedResult
from collector.parsers.allure_common import FormatFields, parse_documents

#: Member suffix this parser reads; ``*-container.json`` fixture records sit in
#: the same directory and are filtered out by it (``DESIGN.md`` section 4.1).
FILENAME_SUFFIX = "-result.json"


def parse(members: dict[str, bytes], base_params: dict[str, str]) -> list[ParsedResult]:
    """Parse every raw Allure result in an extracted artifact.

    Args:
        members (dict[str, bytes]): Archive member path to file contents.
        base_params (dict[str, str]): Parameters derived from the artifact name,
            merged into every result so a matrix leg is carried on each row.

    Returns:
        list[ParsedResult]: One result per readable ``*-result.json`` member.
    """
    return parse_documents(members, base_params, _read_format, path_suffix=FILENAME_SUFFIX)


def _read_format(payload: dict[str, Any]) -> FormatFields:
    """Read the fields that distinguish the raw schema from the report schema.

    Args:
        payload (dict[str, Any]): The decoded ``*-result.json`` object.

    Returns:
        FormatFields: Duration, failure message, stack trace and the top-level
        step array, in that order.
    """
    message, trace = _failure_detail(payload)
    return _duration_ms(payload), message, trace, payload.get("steps")


def _duration_ms(payload: dict[str, Any]) -> int | None:
    """Derive a duration from the format's start/stop pair.

    Args:
        payload (dict[str, Any]): The decoded result object.

    Returns:
        int | None: ``stop - start`` in milliseconds, or None when either bound
        is missing or not numeric, or the pair is inverted (``DESIGN.md``
        section 6).
    """
    start = payload.get("start")
    stop = payload.get("stop")
    if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
        span = int(stop) - int(start)
        return span if span >= 0 else None
    return None


def _failure_detail(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Read the failure message and stack trace from ``statusDetails``.

    The raw schema keeps both in one block, the richest failure diagnostic any
    of the three formats publishes: ``trace`` is the full pytest traceback as
    the run saw it.

    Args:
        payload (dict[str, Any]): The decoded result object.

    Returns:
        tuple[str | None, str | None]: A ``(message, trace)`` pair, each None
        when empty or absent, and both None when ``statusDetails`` is not an
        object.
    """
    status_details = payload.get("statusDetails")
    if not isinstance(status_details, dict):
        return None, None
    message = status_details.get("message")
    trace = status_details.get("trace")
    return (str(message) if message else None, str(trace) if trace else None)
