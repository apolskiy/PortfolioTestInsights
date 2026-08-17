"""Parser for pytest's JUnit XML output.

The only per-test format PublicAP publishes. It carries no steps and no Allure
labels, which is why ``source_format`` and ``has_steps`` exist on every row: a
report that finds no step data here must be able to tell "the suite recorded
none" from "this format cannot express any".

An assigned test ID can still travel in this format, but only through pytest's
``record_property`` fixture, which emits a ``<property>`` element inside the
``<testcase>``. See DESIGN.md section 7.
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

#: Child elements that override the default "passed" outcome, in the order they
#: are checked. An ``error`` is a fault in the harness rather than a failed
#: assertion, so it maps to ``broken`` rather than to ``failed``.
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
        members: Archive member path to file contents.
        base_params: Parameters derived from the artifact name - for PublicAP
            the operating system and Python version of the matrix leg.

    Returns:
        One ParsedResult per ``<testcase>``. A document that will not parse is
        skipped rather than aborting the artifact.
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
        case: The XML element.
        base_params: Parameters derived from the artifact name.

    Returns:
        The normalized result.
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

    JUnit splits the two diagnostics across an attribute and the element body:
    ``message`` holds the assertion summary, and the element text holds the
    traceback pytest captured. Both are kept, because the summary says what
    broke and only the body says where.

    Args:
        case: The XML element.

    Returns:
        A ``(status, message, trace)`` triple. JUnit states a non-passing
        outcome by adding a child element, so the absence of all of them is a
        pass.
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
        case: The XML element.

    Returns:
        Milliseconds elapsed, or None when the attribute is missing or is not a
        number.
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
        case: The XML element.

    Returns:
        The ID when a well-formed one is present, otherwise None.
    """
    for prop in case.iter("property"):
        if prop.get("name") == TEST_ID_PROPERTY:
            value = (prop.get("value") or "").strip()
            if TEST_ID_PATTERN.match(value):
                return value
    return None
