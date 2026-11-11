"""Registry of the parsers for the three per-test formats the portfolio publishes.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains ``PARSERS``, which maps each parser name accepted in
``config/sources.yaml`` to its module's ``parse`` entry point: ``allure_raw``,
``allure_report`` and ``junit``.

Ingestion extracts an artifact's matching members and calls the parser its
configuration names; the ``ParsedResult`` list it gets back is shaped by the
source's policies and appended to the record. A parser that returns nothing
for an artifact makes ingestion record a ``no_results_parsed`` anomaly.

**One signature for every format.** Each module exposes
``parse(members, base_params) -> list[ParsedResult]``, so ingestion selects a
parser by name and never branches on format itself. Adding a fourth format is a
new module, an entry here, and a name in ``config.VALID_PARSERS``.

Specified by ``DESIGN.md`` sections 4 and 5.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from collector.models import ParsedResult
from collector.parsers import allure_raw, allure_report, junit

#: Parser name from ``config/sources.yaml`` to its entry point.
PARSERS: Final[dict[str, Callable[[dict[str, bytes], dict[str, str]], list[ParsedResult]]]] = {
    "allure_raw": allure_raw.parse,
    "allure_report": allure_report.parse,
    "junit": junit.parse,
}
