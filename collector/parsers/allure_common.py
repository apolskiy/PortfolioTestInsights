"""Helpers shared by the two Allure parsers.

Allure's raw results and its generated report describe the same run with two
different schemas - timing, step location and the meaning of ``name`` all
differ. What they agree on is the shape of labels, parameters and steps, so
those readers live here and the format-specific modules handle only what is
genuinely format-specific.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Final

from collector.models import (
    ParsedResult,
    ParsedStep,
    TEST_ID_PATTERN,
    normalize_status,
    split_full_name,
    strip_parameters,
)

#: Label under which a suite publishes its assigned test ID. See DESIGN.md
#: section 7; absent from every artifact produced before the scheme existed.
TEST_ID_LABEL: Final[str] = "test_id"

#: Allure's own identifier label, accepted as an alternative spelling.
ALLURE_ID_LABEL: Final[str] = "AS_ID"


def read_labels(payload: dict[str, Any]) -> dict[str, str]:
    """Collect an Allure ``labels`` array into a mapping.

    Args:
        payload: A raw result or report test-case object.

    Returns:
        Label name to value. Where a label repeats - Allure permits several
        ``tag`` entries - the last one wins, which is enough for the grouping
        dimensions reports use (epic, feature, story, severity).
    """
    labels: dict[str, str] = {}
    for label in payload.get("labels") or []:
        name = label.get("name")
        value = label.get("value")
        if name and value is not None:
            labels[str(name)] = str(value)
    return labels


def read_test_id(labels: dict[str, str]) -> str | None:
    """Extract an assigned test ID from a label mapping.

    Args:
        labels: Labels as returned by :func:`read_labels`.

    Returns:
        The ID when one is present and well-formed, otherwise None. A label that
        is present but malformed returns None rather than being stored, so a
        typo shows up as a missing ID instead of becoming a second identity for
        the same test.
    """
    for key in (TEST_ID_LABEL, ALLURE_ID_LABEL):
        candidate = labels.get(key)
        if candidate and TEST_ID_PATTERN.match(candidate.strip()):
            return candidate.strip()
    return None


def read_parameters(payload: dict[str, Any]) -> dict[str, str]:
    """Collect an Allure ``parameters`` array into a mapping.

    Args:
        payload: A raw result or report test-case object.

    Returns:
        Parameter name to value, with the repr-style quoting Allure applies to
        pytest values removed so that ``'chromium'`` is stored as ``chromium``.
    """
    parameters: dict[str, str] = {}
    for parameter in payload.get("parameters") or []:
        name = parameter.get("name")
        value = parameter.get("value")
        if name and value is not None:
            parameters[str(name)] = str(value).strip("'\"")
    return parameters


#: What a format-specific reader must return for one document: the duration,
#: the failure message, and the step array, in that order. These are the only
#: three things the raw and report schemas genuinely disagree about.
FormatFields = tuple[int | None, str | None, str | None, list[dict[str, Any]] | None]
FormatReader = Callable[[dict[str, Any]], FormatFields]


def parse_documents(
    members: dict[str, bytes],
    base_params: dict[str, str],
    read_format: FormatReader,
    path_suffix: str | None = None,
) -> list[ParsedResult]:
    """Decode and normalize every Allure document in an extracted artifact.

    Both Allure parsers do the same three things - filter members, decode JSON,
    build a result - and differ only in how they locate timing, message and
    steps. That difference is passed in as ``read_format`` so the loop exists
    once; two copies of it would be two places for a filtering rule to drift.

    Args:
        members: Archive member path to file contents.
        base_params: Parameters derived from the artifact name.
        read_format: Reads the format-specific fields from one document.
        path_suffix: When given, only members whose path ends with it are read.

    Returns:
        One ParsedResult per readable document. A member that is not valid JSON,
        or that carries no ``fullName`` to derive identity from, is skipped
        rather than aborting the artifact: one corrupt file in a run of hundreds
        should cost one result, not the run.
    """
    results: list[ParsedResult] = []
    for path, content in members.items():
        if path_suffix is not None and not path.endswith(path_suffix):
            continue
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not payload.get("fullName"):
            continue
        results.append(build_result(payload, base_params, read_format(payload)))
    return results


def build_result(
    payload: dict[str, Any],
    base_params: dict[str, str],
    fields: FormatFields,
) -> ParsedResult:
    """Assemble a ParsedResult from the parts both Allure schemas agree on.

    The two formats disagree about timing, step location, and where a failure
    message and stack trace live, so those four are resolved by the caller and
    passed in. Everything else - identity, labels, parameters, the assigned test
    ID - is read the same way from either schema, and is read here so that the
    two parsers cannot drift apart in how they do it.

    Args:
        payload: A raw result or report test-case object.
        base_params: Parameters derived from the artifact name.
        fields: Duration, message, trace and step array, already resolved from
            whichever schema produced this document.

    Returns:
        The normalized result.
    """
    duration_ms, message, trace, raw_steps = fields
    module, test_name = split_full_name(str(payload.get("fullName") or ""))
    base_name, parameter_id = strip_parameters(test_name)

    labels = read_labels(payload)
    params = dict(base_params)
    params.update(read_parameters(payload))

    # A parametrized case may state its parameters structurally, in the bracket
    # suffix of the full name, or only in the display name. Prefer them in that
    # order so a suite that sets none of the first two still yields a usable row.
    if parameter_id is None:
        _, parameter_id = strip_parameters(str(payload.get("name") or ""))
    if parameter_id is not None and "id" not in params:
        params["id"] = parameter_id

    return ParsedResult(
        module=module,
        test_name=base_name,
        params=params,
        status=normalize_status(payload.get("status")),
        display_name=str(payload.get("name") or base_name),
        duration_ms=duration_ms,
        message=message,
        trace=trace,
        labels=labels,
        history_id=payload.get("historyId"),
        test_id=read_test_id(labels),
        steps=tuple(flatten_steps(raw_steps)),
    )


def flatten_steps(
    raw_steps: list[dict[str, Any]] | None,
    parent_ordinal: int | None = None,
    counter: list[int] | None = None,
) -> list[ParsedStep]:
    """Flatten Allure's nested step tree into an ordered list.

    Nesting is preserved through ``parent_ordinal`` rather than by keeping the
    tree, because the questions worth asking of this data - which step most
    often precedes a failure - are aggregations over a flat table.

    Args:
        raw_steps: The ``steps`` array, or None when the test recorded none.
        parent_ordinal: Ordinal of the enclosing step, None at the top level.
        counter: Single-element list used as a shared mutable ordinal counter
            across the recursion. Callers pass None.

    Returns:
        Steps in depth-first order, each carrying its own ordinal.
    """
    if counter is None:
        counter = [0]

    flattened: list[ParsedStep] = []
    for step in raw_steps or []:
        ordinal = counter[0]
        counter[0] += 1
        flattened.append(
            ParsedStep(
                ordinal=ordinal,
                name=str(step.get("name") or ""),
                status=normalize_status(step.get("status")),
                duration_ms=step_duration_ms(step),
                parent_ordinal=parent_ordinal,
            )
        )
        flattened.extend(flatten_steps(step.get("steps"), ordinal, counter))
    return flattened


def step_duration_ms(step: dict[str, Any]) -> int | None:
    """Read a step's duration from whichever of the two shapes it carries.

    Args:
        step: One entry from a ``steps`` array.

    Returns:
        Duration in milliseconds, or None when the step recorded no timing.
    """
    time_block = step.get("time")
    if isinstance(time_block, dict):
        return _span_ms(time_block.get("start"), time_block.get("stop"), time_block.get("duration"))
    return _span_ms(step.get("start"), step.get("stop"), None)


def _span_ms(start: Any, stop: Any, duration: Any) -> int | None:
    """Compute a millisecond duration from a start/stop pair or a given value.

    Args:
        start: Epoch milliseconds, or None.
        stop: Epoch milliseconds, or None.
        duration: A duration already in milliseconds, or None.

    Returns:
        The duration, or None when neither form is usable. A stop before its
        start yields None rather than a negative number, since a negative
        duration is a data fault and averaging it would hide the fault.
    """
    if isinstance(duration, (int, float)):
        return int(duration)
    if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
        span = int(stop) - int(start)
        return span if span >= 0 else None
    return None
