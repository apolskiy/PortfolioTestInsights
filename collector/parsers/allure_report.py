"""Parser for Allure's generated report (``data/test-cases/*.json``).

Produced by ``allure generate`` and uploaded by PlaywrightAPWebsiteAutomation
and CountryWeather. This is the only machine-readable per-test record the
largest suite in the portfolio publishes, which is why the collector reads a
report format at all - see DESIGN.md section 4.

It differs from the raw schema in three ways: timing arrives as
``time{start, stop, duration}``, steps are nested under ``testStage``, and the
failure message is a top-level ``statusMessage``.
"""

from __future__ import annotations

from typing import Any

from collector.models import ParsedResult
from collector.parsers.allure_common import FormatFields, parse_documents


def parse(members: dict[str, bytes], base_params: dict[str, str]) -> list[ParsedResult]:
    """Parse every report test-case document in an extracted artifact.

    Args:
        members: Archive member path to file contents.
        base_params: Parameters derived from the artifact name, merged into
            every result.

    Returns:
        One ParsedResult per readable member.
    """
    return parse_documents(members, base_params, _read_format)


def _read_format(payload: dict[str, Any]) -> FormatFields:
    """Read the fields that distinguish the report schema from the raw schema.

    Args:
        payload: The decoded test-case document.

    Returns:
        Duration, failure message, stack trace and step array, in that order.
    """
    test_stage = payload.get("testStage")
    raw_steps = test_stage.get("steps") if isinstance(test_stage, dict) else None
    message = _detail(payload, test_stage, "statusMessage")
    trace = _detail(payload, test_stage, "statusTrace")
    return _duration_ms(payload), message, trace, raw_steps


def _duration_ms(payload: dict[str, Any]) -> int | None:
    """Read the duration from the report's ``time`` block.

    Args:
        payload: The decoded test-case document.

    Returns:
        Milliseconds elapsed, preferring the recorded duration over a
        start/stop subtraction, or None when the block is absent.
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

    The generator sometimes hangs the message and trace off the test case and
    sometimes off its test stage, depending on where the failure was raised, so
    both are checked before concluding the test carried none.

    Args:
        payload: The decoded test-case document.
        test_stage: The ``testStage`` block, when present.
        key: Either ``statusMessage`` or ``statusTrace``.

    Returns:
        The text, or None when neither location carries it.
    """
    detail = payload.get(key)
    if not detail and isinstance(test_stage, dict):
        detail = test_stage.get(key)
    return str(detail) if detail else None
