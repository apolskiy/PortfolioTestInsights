"""Parsers for the three per-test formats the portfolio publishes.

Each module exposes the same ``parse(members, base_params) -> list[ParsedResult]``
signature, so ingestion selects one by name from the source configuration and
never branches on format itself. Adding a fourth format is a new module plus a
config value.
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
