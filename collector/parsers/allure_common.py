"""Helpers shared by the two Allure parsers.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains ``parse_documents``, the reading loop both Allure parsers
call; ``build_result``, which assembles a :class:`ParsedResult` from one
document; the readers for what the two schemas share (``read_labels``,
``read_test_id``, ``read_parameters``, ``flatten_steps``,
``step_duration_ms``); and the ``FormatFields`` and ``FormatReader`` types that
define what each format-specific module must supply.

``allure_raw`` and ``allure_report`` each pass their members and a
``FormatReader`` in, and the results go back to ingestion through
:data:`collector.parsers.PARSERS`. A document dropped here never becomes a row;
an artifact whose every document is dropped becomes a ``no_results_parsed``
anomaly.

**Identity comes from ``fullName``.** A document without one is skipped. The
module and test name are split at ``#``, the parameter id is stripped from the
test name, or failing that from ``name``, and ``name`` itself is kept only as
the display name.

**The format reader supplies the rest.** Duration, failure message, trace and
the raw step array are the only fields the two schemas disagree about, and each
parser resolves them before ``build_result`` runs.

Specified by ``DESIGN.md`` sections 4.1, 4.2, 6 and 7.
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

#: Label under which a suite publishes its assigned test ID (``DESIGN.md``
#: section 7); absent from every artifact produced before the scheme existed.
TEST_ID_LABEL: Final[str] = "test_id"

#: Allure's own identifier label, accepted as an alternative spelling.
ALLURE_ID_LABEL: Final[str] = "AS_ID"


def read_labels(payload: dict[str, Any]) -> dict[str, str]:
    """Collect an Allure ``labels`` array into a mapping.

    Args:
        payload (dict[str, Any]): A raw result or report test-case object.

    Returns:
        dict[str, str]: Label name to value, skipping entries without a name or
        value. Where a label repeats, as Allure permits for ``tag``, the last
        one wins, which is enough for the grouping dimensions reports use
        (epic, feature, story, severity).
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

    ``test_id`` is checked first, then ``AS_ID``. Design: ``DESIGN.md``
    section 7.

    Args:
        labels (dict[str, str]): Labels as returned by :func:`read_labels`.

    Returns:
        str | None: The first stripped value matching ``TEST_ID_PATTERN``, or
        None. A label present but malformed yields None, not a stored value.
    """
    for key in (TEST_ID_LABEL, ALLURE_ID_LABEL):
        candidate = labels.get(key)
        if candidate and TEST_ID_PATTERN.match(candidate.strip()):
            return candidate.strip()
    return None


def read_parameters(payload: dict[str, Any]) -> dict[str, str]:
    """Collect an Allure ``parameters`` array into a mapping.

    Args:
        payload (dict[str, Any]): A raw result or report test-case object.

    Returns:
        dict[str, str]: Parameter name to value, with the repr-style quoting
        Allure applies to pytest values removed, so ``'chromium'`` is stored as
        ``chromium``.
    """
    parameters: dict[str, str] = {}
    for parameter in payload.get("parameters") or []:
        name = parameter.get("name")
        value = parameter.get("value")
        if name and value is not None:
            parameters[str(name)] = str(value).strip("'\"")
    return parameters


#: FORMAT FIELDS a format-specific reader returns for one document: duration,
#: failure message, stack trace, and the raw step array, in that order. These
#: are the only things the raw and report schemas disagree about.
FormatFields = tuple[int | None, str | None, str | None, list[dict[str, Any]] | None]
FormatReader = Callable[[dict[str, Any]], FormatFields]


def parse_documents(
    members: dict[str, bytes],
    base_params: dict[str, str],
    read_format: FormatReader,
    path_suffix: str | None = None,
) -> list[ParsedResult]:
    """Decode and normalize every Allure document in an extracted artifact.

    Design: ``DESIGN.md`` section 4.2.

    Args:
        members (dict[str, bytes]): Archive member path to file contents.
        base_params (dict[str, str]): Parameters derived from the artifact name.
        read_format (FormatReader): Reads the format-specific fields from one
            document.
        path_suffix (str | None): When given, only members whose path ends with
            it are read.

    Returns:
        list[ParsedResult]: One result per readable document, in member order.
        A member that is not UTF-8 JSON, is not an object, or carries no
        ``fullName`` is skipped without aborting the artifact.
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

    Parameters are the artifact's, overlaid by the document's ``parameters``;
    a bracketed parameter id from ``fullName``, or failing that from ``name``,
    is added as ``id`` unless the parameters already hold one.

    Args:
        payload (dict[str, Any]): A raw result or report test-case object.
        base_params (dict[str, str]): Parameters derived from the artifact name.
        fields (FormatFields): Duration, message, trace and step array, already
            resolved from whichever schema produced this document.

    Returns:
        ParsedResult: The normalized result. ``display_name`` is ``name``, or
        the base test name when ``name`` is absent.
    """
    duration_ms, message, trace, raw_steps = fields
    module, test_name = split_full_name(str(payload.get("fullName") or ""))
    base_name, parameter_id = strip_parameters(test_name)

    labels = read_labels(payload)
    params = dict(base_params)
    params.update(read_parameters(payload))

    # Added 2026-08-16. A parametrized case may state its parameters
    # structurally, in the bracket suffix of the full name, or only in the
    # display name. Prefer them in that order so a suite that sets none of the
    # first two still yields a usable row.
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

    Each step is followed by its own sub-steps, which carry its ordinal as
    their ``parent_ordinal``. Design: ``DESIGN.md`` section 6.

    Args:
        raw_steps (list[dict[str, Any]] | None): The ``steps`` array, or None
            when the test recorded none.
        parent_ordinal (int | None): Ordinal of the enclosing step, None at the
            top level.
        counter (list[int] | None): Single-element list used as a shared
            mutable ordinal counter across the recursion. Callers pass None.

    Returns:
        list[ParsedStep]: Steps in depth-first order, ordinals counting from
        zero.
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
        step (dict[str, Any]): One entry from a ``steps`` array.

    Returns:
        int | None: Duration in milliseconds from a ``time`` block (report
        schema) or a top-level ``start``/``stop`` pair (raw schema), or None
        when the step recorded no usable timing.
    """
    time_block = step.get("time")
    if isinstance(time_block, dict):
        return _span_ms(time_block.get("start"), time_block.get("stop"), time_block.get("duration"))
    return _span_ms(step.get("start"), step.get("stop"), None)


def _span_ms(start: Any, stop: Any, duration: Any) -> int | None:
    """Compute a millisecond duration from a given value or a start/stop pair.

    Args:
        start (Any): Epoch milliseconds, or None.
        stop (Any): Epoch milliseconds, or None.
        duration (Any): A duration already in milliseconds, or None.

    Returns:
        int | None: ``duration`` when it is numeric, else ``stop - start``
        when both are numeric, else None. A stop before its start yields None,
        never a negative number (``DESIGN.md`` section 6).
    """
    if isinstance(duration, (int, float)):
        return int(duration)
    if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
        span = int(stop) - int(start)
        return span if span >= 0 else None
    return None
