"""Loading and validation of ``config/sources.yaml``.

Validation is strict and happens once at startup rather than at the point of
use. A misspelled parser name or an unparseable artifact pattern should stop the
run immediately with a message naming the offending entry, not surface hours
later as a repository that quietly produced no rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

#: Parser names accepted in the config, matching the modules in collector.parsers.
VALID_PARSERS: Final[frozenset[str]] = frozenset({"allure_raw", "allure_report", "junit"})

#: Default location of the source configuration, relative to the repository root.
DEFAULT_CONFIG_PATH: Final[Path] = Path("config") / "sources.yaml"


class ConfigError(ValueError):
    """Raised when the source configuration is malformed."""


@dataclass(frozen=True)
class ArtifactSpec:
    """One artifact pattern within a repository.

    Attributes:
        pattern: Compiled regex matched against the artifact name. Named groups
            become parameter columns on every result parsed from it.
        parser: Which parser reads the extracted files.
        glob: fnmatch pattern selecting members inside the artifact zip.
    """

    pattern: re.Pattern[str]
    parser: str
    glob: str

    def match_params(self, artifact_name: str) -> dict[str, str] | None:
        """Match an artifact name and extract its parameter groups.

        Args:
            artifact_name: Name as reported by the Actions API.

        Returns:
            The named groups as a parameter dict when the name matches, or None
            when it does not. An empty dict is a match with no parameters, which
            is why None rather than an empty dict signals "no match".
        """
        # pylint cannot infer the compiled type through a dataclass field and
        # reports no-member here; the attribute is a re.Pattern at run time, as
        # every ingestion exercising this path demonstrates.
        match = self.pattern.match(artifact_name)  # pylint: disable=no-member
        if match is None:
            return None
        return {key: value for key, value in match.groupdict().items() if value is not None}


@dataclass(frozen=True)
class SourceSpec:
    """One repository and the artifacts worth reading from it.

    Attributes:
        repo: Owner-qualified repository name.
        prefix: Assigned test-ID prefix for this repository, e.g. ``PAWA``.
        artifacts: Artifact patterns to look for.
    """

    repo: str
    prefix: str
    artifacts: tuple[ArtifactSpec, ...]

    @property
    def slug(self) -> str:
        """Return the filesystem-safe short name used for data partitions.

        Returns:
            The repository name without its owner, lower-cased.
        """
        return self.repo.split("/")[-1].lower()


def load_sources(path: Path | None = None) -> tuple[SourceSpec, ...]:
    """Read and validate the source configuration.

    Args:
        path: Location of the YAML file. Defaults to ``config/sources.yaml``.

    Returns:
        One SourceSpec per configured repository, in file order.

    Raises:
        ConfigError: If the file is missing a required key, names an unknown
            parser, or carries an artifact pattern that is not a valid regex.
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
        entry: One item from the ``sources`` list.
        config_path: Used only to make error messages locatable.

    Returns:
        The validated SourceSpec.

    Raises:
        ConfigError: If a required key is missing or an artifact is invalid.
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
    )


def _build_artifact(item: dict, repo: str, config_path: Path) -> ArtifactSpec:
    """Validate one artifact entry and build its ArtifactSpec.

    Args:
        item: One item from a repository's ``artifacts`` list.
        repo: Owning repository, used in error messages.
        config_path: Used only to make error messages locatable.

    Returns:
        The validated ArtifactSpec.

    Raises:
        ConfigError: If the parser is unknown or the pattern will not compile.
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

    return ArtifactSpec(pattern=pattern, parser=parser, glob=glob)
