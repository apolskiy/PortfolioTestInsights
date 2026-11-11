"""Loads and validates ``config/sources.yaml``.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains the records the configuration becomes: :class:`SourceSpec`
for a repository, :class:`ArtifactSpec` for an artifact pattern within it, and
:class:`SloPolicy` for the duration error budget. ``load_sources`` and
``load_slo`` read them, the private ``_build_*`` functions validate each entry,
and :class:`ConfigError` is what a malformed entry raises. The accepted values
are the constants ``VALID_PARSERS``, ``STEP_POLICIES`` and ``KEEP_POLICIES``.

Ingestion calls ``load_sources`` before it makes a single request, so a
``ConfigError`` stops the run with nothing downloaded or written. The specs
then decide which artifacts are read, which parser reads them, which steps
survive, and which ID a test carries; the reports call ``load_sources`` again
for each repository's display code and ``load_slo`` for the budget policy.

**Validated once, at startup.** Every source needs ``repo``, ``prefix`` and a
non-empty ``artifacts`` list; every artifact needs ``name``, ``parser`` and
``glob``, a known parser, a ``name`` that compiles as a regex, and policies from
the accepted sets. A ``test_id_from_name`` pattern must compile and define a
``test_id`` group.

**Defaults keep earlier entries unchanged.** ``steps`` defaults to ``all``,
``keep`` to ``every``, and ``code`` to the prefix. A file with no ``slo`` block
yields the :class:`SloPolicy` defaults.

Specified by ``DESIGN.md`` sections 8 and 10.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

#: Parser names accepted in the config, matching the modules in collector.parsers.
VALID_PARSERS: Final[frozenset[str]] = frozenset({"allure_raw", "allure_report", "junit"})

#: Added 2026-11-10, for the Multi-LLM Evaluation sources.
#: STEP POLICIES an artifact may declare. ``all`` keeps every result's steps;
#: ``failures`` keeps them only where a result did not pass or skip
#: (``DESIGN.md`` section 8).
STEP_POLICIES: Final[frozenset[str]] = frozenset({"all", "failures"})

#: Added 2026-11-10, for the Multi-LLM Evaluation sources.
#: RUN-SELECTION POLICIES an artifact may declare. ``every`` reads every
#: matching artifact; ``latest_per_day`` reads only the newest per completed UTC
#: day and parameter set (``DESIGN.md`` section 8).
KEEP_POLICIES: Final[frozenset[str]] = frozenset({"every", "latest_per_day"})

#: Default location of the source configuration, relative to the repository root.
DEFAULT_CONFIG_PATH: Final[Path] = Path("config") / "sources.yaml"


class ConfigError(ValueError):
    """Raised when the source configuration is malformed.

    The message names the configuration file and the offending entry.
    """


@dataclass(frozen=True)
class SloPolicy:
    """The error-budget policy the report evaluates each test against.

    A run breaches when it exceeds ``tolerance`` times the test's own baseline
    median; the budget is spent when more than ``budget`` of the trailing
    window breached. Design: ``DESIGN.md`` section 10.1.

    Attributes:
        window_runs (int): Trailing runs considered.
        tolerance (float): Multiple of the baseline median above which a run
            breaches.
        budget (float): Fraction of the window allowed to breach before it is
            spent.
        min_observations (int): Runs in the window below which no verdict is
            published.
        floor_ms (float): Baseline median below which a test is excluded.
    """

    window_runs: int = 20
    tolerance: float = 3.0
    budget: float = 0.10
    min_observations: int = 10
    floor_ms: float = 100.0


def load_slo(path: Path | None = None) -> SloPolicy:
    """Read the error-budget policy, falling back to the declared defaults.

    Args:
        path (Path | None): Location of the YAML file. Defaults to
            ``config/sources.yaml``.

    Returns:
        SloPolicy: The policy. A missing ``slo`` block, or a key missing from
        it, takes the class default rather than raising.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    block = document.get("slo") or {}
    return SloPolicy(
        window_runs=int(block.get("window_runs", SloPolicy.window_runs)),
        tolerance=float(block.get("tolerance", SloPolicy.tolerance)),
        budget=float(block.get("budget", SloPolicy.budget)),
        min_observations=int(block.get("min_observations", SloPolicy.min_observations)),
        floor_ms=float(block.get("floor_ms", SloPolicy.floor_ms)),
    )


@dataclass(frozen=True)
class ArtifactSpec:
    """One artifact pattern within a repository.

    Attributes:
        pattern (re.Pattern[str]): Compiled regex matched against the artifact
            name. Named groups become parameter columns on every result parsed
            from it.
        parser (str): Which parser reads the extracted files, one of
            :data:`VALID_PARSERS`.
        glob (str): fnmatch pattern selecting members inside the artifact zip.
        steps (str): Step policy, one of :data:`STEP_POLICIES`.
        keep (str): Run-selection policy, one of :data:`KEEP_POLICIES`.
    """

    pattern: re.Pattern[str]
    parser: str
    glob: str
    steps: str = "all"
    keep: str = "every"

    def match_params(self, artifact_name: str) -> dict[str, str] | None:
        """Match an artifact name and extract its parameter groups.

        Args:
            artifact_name (str): Name as reported by the Actions API.

        Returns:
            dict[str, str] | None: The named groups that matched, as a parameter
            dict, when the name matches from its start; None when it does not.
            An empty dict is a match with no parameters, so only None means "no
            match".
        """
        # Added 2026-08-16. pylint cannot infer the compiled type through a
        # dataclass field and reports no-member here; the attribute is a
        # re.Pattern at run time, as every ingestion exercising this path shows.
        match = self.pattern.match(artifact_name)  # pylint: disable=no-member
        if match is None:
            return None
        return {key: value for key, value in match.groupdict().items() if value is not None}


#: Named group a ``test_id_from_name`` pattern must define.
TEST_ID_GROUP: Final[str] = "test_id"


@dataclass(frozen=True)
class SourceSpec:
    """One repository and the artifacts worth reading from it.

    Attributes:
        repo (str): Owner-qualified repository name.
        prefix (str): Assigned test-ID prefix for this repository, e.g.
            ``PAWA``.
        artifacts (tuple[ArtifactSpec, ...]): Artifact patterns to look for, in
            file order; the first that matches an artifact wins.
        code (str): Short code the report prints for this repository; empty
            means the prefix. Set where two repositories share a prefix
            (``DESIGN.md`` section 8).
        test_id_from_name (re.Pattern[str] | None): For a suite that writes its
            assigned ID into the test function's name, the regex with a
            ``test_id`` group that extracts it, matched against the start of
            the name. None for every suite that publishes its ID as a label or
            property.
    """

    repo: str
    prefix: str
    artifacts: tuple[ArtifactSpec, ...]
    code: str = ""
    test_id_from_name: re.Pattern[str] | None = None

    @property
    def display_code(self) -> str:
        """Return the code the report prints for this repository.

        Returns:
            str: The configured code, or the prefix when none is configured.
        """
        return self.code or self.prefix

    def derived_test_id(self, test_name: str) -> str | None:
        """Extract an assigned test ID from a test name, where this suite puts it there.

        Args:
            test_name (str): Test function name with parameters removed.

        Returns:
            str | None: The ``test_id`` group, or None when this source declares
            no pattern or the name does not match it. A non-matching name yields
            no ID rather than a guessed one, the same rule a malformed label
            follows.
        """
        if self.test_id_from_name is None:
            return None
        # Added 2026-11-10. Same dataclass-field inference gap as
        # ArtifactSpec.match_params.
        match = self.test_id_from_name.match(test_name)  # pylint: disable=no-member
        return match.group(TEST_ID_GROUP) if match else None

    @property
    def slug(self) -> str:
        """Return the filesystem-safe short name used for data partitions.

        Returns:
            str: The repository name without its owner, lower-cased.
        """
        return self.repo.split("/")[-1].lower()


def load_sources(path: Path | None = None) -> tuple[SourceSpec, ...]:
    """Read and validate the source configuration.

    Args:
        path (Path | None): Location of the YAML file. Defaults to
            ``config/sources.yaml``.

    Returns:
        tuple[SourceSpec, ...]: One spec per configured repository, in file
        order.

    Raises:
        ConfigError: If ``sources`` is missing or empty, an entry lacks a
            required key, names an unknown parser or policy, or carries a
            pattern that is not a valid regex.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}

    raw_sources = document.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError(f"{config_path}: 'sources' must be a non-empty list")

    return tuple(_build_source(entry, config_path) for entry in raw_sources)


def _build_source(entry: dict, config_path: Path) -> SourceSpec:
    """Validate one repository entry and build its SourceSpec.

    Args:
        entry (dict): One item from the ``sources`` list.
        config_path (Path): Used only to make error messages locatable.

    Returns:
        SourceSpec: The validated spec.

    Raises:
        ConfigError: If ``repo``, ``prefix`` or a non-empty ``artifacts`` list is
            missing, or an artifact or the ``test_id_from_name`` pattern is
            invalid.
    """
    repo = entry.get("repo")
    if not repo:
        raise ConfigError(f"{config_path}: a source entry is missing 'repo'")

    prefix = entry.get("prefix")
    if not prefix:
        raise ConfigError(f"{config_path}: source '{repo}' is missing 'prefix'")

    raw_artifacts = entry.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ConfigError(f"{config_path}: source '{repo}' has no artifacts")

    return SourceSpec(
        repo=repo,
        prefix=prefix,
        artifacts=tuple(_build_artifact(item, repo, config_path) for item in raw_artifacts),
        code=str(entry.get("code") or ""),
        test_id_from_name=_build_test_id_pattern(entry.get("test_id_from_name"), repo, config_path),
    )


def _build_test_id_pattern(
    raw_pattern: str | None, repo: str, config_path: Path
) -> re.Pattern[str] | None:
    """Validate a source's ``test_id_from_name`` pattern.

    Args:
        raw_pattern (str | None): The pattern as written in the config, or None.
        repo (str): Owning repository, used in error messages.
        config_path (Path): Used only to make error messages locatable.

    Returns:
        re.Pattern[str] | None: The compiled pattern, or None when the source
        declares none.

    Raises:
        ConfigError: If the pattern will not compile, or has no ``test_id``
            group and so could match a name without yielding an ID.
    """
    if not raw_pattern:
        return None
    try:
        pattern = re.compile(raw_pattern)
    except re.error as error:
        raise ConfigError(
            f"{config_path}: '{repo}' test_id_from_name '{raw_pattern}' is invalid: {error}"
        ) from error
    if TEST_ID_GROUP not in pattern.groupindex:
        raise ConfigError(
            f"{config_path}: '{repo}' test_id_from_name '{raw_pattern}' has no "
            f"'(?P<{TEST_ID_GROUP}>...)' group, so it can match a name without yielding an ID"
        )
    return pattern


def _build_artifact(item: dict, repo: str, config_path: Path) -> ArtifactSpec:
    """Validate one artifact entry and build its ArtifactSpec.

    Args:
        item (dict): One item from a repository's ``artifacts`` list.
        repo (str): Owning repository, used in error messages.
        config_path (Path): Used only to make error messages locatable.

    Returns:
        ArtifactSpec: The validated spec, with ``steps`` defaulting to ``all``
        and ``keep`` to ``every``.

    Raises:
        ConfigError: If ``name``, ``parser`` or ``glob`` is missing, the parser
            is unknown, the name will not compile as a regex, or a policy is
            not one of the accepted values.
    """
    name = item.get("name")
    parser = item.get("parser")
    glob = item.get("glob")

    if not name or not parser or not glob:
        raise ConfigError(f"{config_path}: an artifact of '{repo}' is missing name/parser/glob")

    if parser not in VALID_PARSERS:
        known = ", ".join(sorted(VALID_PARSERS))
        raise ConfigError(
            f"{config_path}: '{repo}' names unknown parser '{parser}' (known: {known})"
        )

    try:
        pattern = re.compile(name)
    except re.error as error:
        raise ConfigError(
            f"{config_path}: '{repo}' artifact pattern '{name}' is invalid: {error}"
        ) from error

    steps = str(item.get("steps") or "all")
    keep = str(item.get("keep") or "every")
    for key, value, allowed in (("steps", steps, STEP_POLICIES), ("keep", keep, KEEP_POLICIES)):
        if value not in allowed:
            raise ConfigError(
                f"{config_path}: '{repo}' artifact '{name}' has {key} '{value}' "
                f"(known: {', '.join(sorted(allowed))})"
            )

    return ArtifactSpec(pattern=pattern, parser=parser, glob=glob, steps=steps, keep=keep)
