"""Parser for pytest's JUnit XML output.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains ``parse``, the entry point registered as ``junit``, and the
private readers that turn one ``<testcase>`` into a :class:`ParsedResult`: its
outcome and diagnostics, its duration, and its assigned test ID.

Ingestion calls ``parse`` with the XML members of PublicAP's
``emulator-reports-*`` and ``image-reports-*`` artifacts, the only per-test
format that suite publishes. The results carry no steps and no labels, and
``source_format`` on each row records that they came from this format, so a
report can tell "the suite recorded none" from "this format cannot express
any".

**One result per ``<testcase>``.** The module is ``classname``, the bracketed
parameter id of ``name`` becomes ``params['id']``, and the full ``name`` is the
display name. A document that is not UTF-8 or not well-formed XML is skipped.

**Outcome comes from the child elements.** ``<error>`` is ``broken``,
``<failure>`` is ``failed``, ``<skipped>`` is ``skipped``, checked in that
order; a case with none of them passed. The element's ``message`` attribute is
the message and its text is the trace.

**An assigned ID travels as a property.** pytest's ``record_property`` emits
``<property name="test_id" value="..."/>`` inside the ``<testcase>``; only a
value matching ``TEST_ID_PATTERN`` is kept.

Specified by ``DESIGN.md`` sections 4.3, 6 and 7.
"""

from __future__ import annotations

from typing import Final
from xml.etree import ElementTree

from collector.models import (
    BROKEN,
    FAILED,
    PASSED,
    SKIPPED,
    TEST_ID_PATTERN,
    ParsedResult,
    strip_parameters,
)

#: OUTCOME ELEMENTS, per ``DESIGN.md`` section 4.3: child elements that override
#: the default "passed" outcome, in the order they are checked. An ``error`` is
#: a fault in the harness rather than a failed assertion, so it maps to
#: ``broken``.
_OUTCOME_ELEMENTS: Final[tuple[tuple[str, str], ...]] = (
    ("error", BROKEN),
    ("failure", FAILED),
    ("skipped", SKIPPED),
)

#: Property name under which a suite publishes its assigned test ID.
TEST_ID_PROPERTY: Final[str] = "test_id"


def parse(members: dict[str, bytes], base_params: dict[str, str]) -> list[ParsedResult]:
    """Parse every JUnit XML document in an extracted artifact.

    Args:
        members (dict[str, bytes]): Archive member path to file contents.
        base_params (dict[str, str]): Parameters derived from the artifact name;
            for PublicAP, the operating system and Python version of the matrix
            leg, or the image.

    Returns:
        list[ParsedResult]: One result per ``<testcase>``, at any depth. A
        document that will not decode or parse is skipped without aborting the
        artifact.
    """
    results: list[ParsedResult] = []
    for content in members.values():
        try:
            root = ElementTree.fromstring(content.decode("utf-8"))
        except (UnicodeDecodeError, ElementTree.ParseError):
            continue
        for case in root.iter("testcase"):
            results.append(_build_result(case, base_params))
    return results


def _build_result(case: ElementTree.Element, base_params: dict[str, str]) -> ParsedResult:
    """Convert one ``<testcase>`` element into a ParsedResult.

    Args:
        case (ElementTree.Element): The XML element.
        base_params (dict[str, str]): Parameters derived from the artifact name.

    Returns:
        ParsedResult: The normalized result, with no labels, history id or
        steps.
    """
    raw_name = case.get("name", "")
    base_name, parameter_id = strip_parameters(raw_name)

    params = dict(base_params)
    if parameter_id is not None:
        params["id"] = parameter_id

    status, message, trace = _outcome(case)

    return ParsedResult(
        module=case.get("classname", ""),
        test_name=base_name,
        params=params,
        status=status,
        display_name=raw_name,
        duration_ms=_duration_ms(case),
        message=message,
        trace=trace,
        labels={},
        history_id=None,
        test_id=_test_id(case),
        steps=(),
    )


def _outcome(case: ElementTree.Element) -> tuple[str, str | None, str | None]:
    """Determine a test case's status, failure message and stack trace.

    The first child of ``_OUTCOME_ELEMENTS`` present decides; its ``message``
    attribute is the assertion summary and its text the traceback pytest
    captured. Design: ``DESIGN.md`` section 4.3.

    Args:
        case (ElementTree.Element): The XML element.

    Returns:
        tuple[str, str | None, str | None]: A ``(status, message, trace)``
        triple, message and trace stripped and None when empty. With no
        outcome element the case passed: ``(PASSED, None, None)``.
    """
    for tag, status in _OUTCOME_ELEMENTS:
        element = case.find(tag)
        if element is not None:
            message = (element.get("message") or "").strip()
            trace = (element.text or "").strip()
            return status, message or None, trace or None
    return PASSED, None, None


def _duration_ms(case: ElementTree.Element) -> int | None:
    """Convert the ``time`` attribute from seconds to milliseconds.

    Args:
        case (ElementTree.Element): The XML element.

    Returns:
        int | None: Milliseconds elapsed, truncated toward zero, or None when
        the attribute is missing or is not a number.
    """
    raw_time = case.get("time")
    if raw_time is None:
        return None
    try:
        return int(float(raw_time) * 1000)
    except ValueError:
        return None


def _test_id(case: ElementTree.Element) -> str | None:
    """Read an assigned test ID from the case's ``<properties>`` block.

    Args:
        case (ElementTree.Element): The XML element.

    Returns:
        str | None: The first ``test_id`` property whose stripped value matches
        ``TEST_ID_PATTERN``, otherwise None.
    """
    for prop in case.iter("property"):
        if prop.get("name") == TEST_ID_PROPERTY:
            value = (prop.get("value") or "").strip()
            if TEST_ID_PATTERN.match(value):
                return value
    return None
