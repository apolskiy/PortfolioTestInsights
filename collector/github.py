"""Read-only client for the GitHub Actions REST API.

Scope is deliberately narrow: list runs, list artifacts, download an artifact
zip. The collector never writes to a source repository, so no method here issues
anything but a GET.

Artifact listings carry a ``workflow_run`` block, which means one paginated
listing per repository is enough to associate every artifact with its run. That
is worth stating because the obvious implementation - fetch the run for each
artifact - would turn a few hundred requests into a few thousand for no gain.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any, Final

import requests

LOGGER = logging.getLogger(__name__)

#: API root. Kept a constant so tests can point a session elsewhere.
API_ROOT: Final[str] = "https://api.github.com"

#: Environment variables consulted for a token, in order of preference.
TOKEN_VARIABLES: Final[tuple[str, ...]] = ("PORTFOLIO_READ_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

#: Page size. 100 is the maximum the API accepts and minimizes round trips.
PAGE_SIZE: Final[int] = 100

#: Statuses worth retrying. 403 appears here because GitHub returns it for
#: secondary rate limiting, not only for genuine authorization failures.
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({403, 429, 500, 502, 503, 504})

#: Attempts per request, and the base for exponential backoff in seconds.
MAX_ATTEMPTS: Final[int] = 4
BACKOFF_BASE_SECONDS: Final[float] = 2.0

#: Requests time out rather than hanging a scheduled job indefinitely.
TIMEOUT_SECONDS: Final[int] = 60


class GitHubError(RuntimeError):
    """Raised when the API cannot be reached or refuses a request."""


def resolve_token() -> str:
    """Find a token without ever putting one in a log line or a file.

    Checks the environment first, then falls back to the locally authenticated
    ``gh`` CLI so that a developer who has already signed in needs no extra
    setup. CI supplies ``PORTFOLIO_READ_TOKEN`` and never reaches the fallback.

    Returns:
        The token string.

    Raises:
        GitHubError: If no token can be found. The message names the variables
            that were checked and never echoes any value.
    """
    for variable in TOKEN_VARIABLES:
        value = os.environ.get(variable)
        if value:
            LOGGER.debug("Using token from %s", variable)
            return value.strip()

    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        checked = ", ".join(TOKEN_VARIABLES)
        raise GitHubError(
            f"No API token available. Set one of {checked}, or authenticate the gh CLI."
        ) from error

    token = completed.stdout.strip()
    if not token:
        raise GitHubError("The gh CLI returned an empty token.")
    LOGGER.debug("Using token from the gh CLI")
    return token


class GitHubClient:
    """Minimal paginating client for the endpoints the collector reads."""

    def __init__(self, token: str | None = None, session: requests.Session | None = None) -> None:
        """Prepare a session with authentication and API versioning headers.

        Args:
            token: Token to use. Resolved from the environment when omitted.
            session: Pre-built session, for tests that stub transport.
        """
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token or resolve_token()}",
                "User-Agent": "PortfolioTestInsights",
            }
        )

    def list_runs(self, repo: str) -> list[dict[str, Any]]:
        """List every workflow run for a repository.

        Args:
            repo: Owner-qualified repository name.

        Returns:
            Run objects, newest first as the API returns them.
        """
        return list(self._paginate(f"/repos/{repo}/actions/runs", "workflow_runs"))

    def list_artifacts(self, repo: str) -> list[dict[str, Any]]:
        """List every artifact for a repository.

        Args:
            repo: Owner-qualified repository name.

        Returns:
            Artifact objects, each carrying the ``workflow_run`` block that
            associates it with the run that produced it.
        """
        return list(self._paginate(f"/repos/{repo}/actions/artifacts", "artifacts"))

    def download_artifact(self, repo: str, artifact_id: int) -> bytes:
        """Download one artifact archive.

        Args:
            repo: Owner-qualified repository name.
            artifact_id: Artifact to fetch.

        Returns:
            The raw zip bytes. Authentication is required even for public
            repositories, which is the only reason this project needs a token.
        """
        response = self._request(f"/repos/{repo}/actions/artifacts/{artifact_id}/zip")
        return response.content

    def _paginate(self, path: str, key: str) -> Iterator[dict[str, Any]]:
        """Walk every page of a list endpoint.

        Args:
            path: API path below the root.
            key: Name of the array within each response body.

        Yields:
            One object at a time, so a caller that stops early costs no further
            requests.
        """
        page = 1
        while True:
            response = self._request(path, params={"per_page": PAGE_SIZE, "page": page})
            items = response.json().get(key) or []
            if not items:
                return
            yield from items
            if len(items) < PAGE_SIZE:
                return
            page += 1

    def _request(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        """Issue a GET with bounded retries and rate-limit awareness.

        Args:
            path: API path below the root.
            params: Query parameters.

        Returns:
            The successful response.

        Raises:
            GitHubError: If every attempt fails, or the API returns a status
                that retrying cannot fix.
        """
        url = f"{API_ROOT}{path}"
        last_error = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._session.get(url, params=params, timeout=TIMEOUT_SECONDS)
            except requests.RequestException as error:
                last_error = str(error)
                self._sleep(attempt, None)
                continue

            if response.ok:
                return response

            last_error = f"HTTP {response.status_code} for {path}"
            if response.status_code not in RETRYABLE_STATUSES:
                raise GitHubError(last_error)

            LOGGER.warning("%s - attempt %d of %d", last_error, attempt, MAX_ATTEMPTS)
            self._sleep(attempt, response)

        raise GitHubError(f"Giving up after {MAX_ATTEMPTS} attempts: {last_error}")

    @staticmethod
    def _sleep(attempt: int, response: requests.Response | None) -> None:
        """Wait before a retry, honouring the server's own guidance when given.

        Args:
            attempt: 1-based attempt number just completed.
            response: The failed response, when there was one.
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(int(retry_after))
                return
        time.sleep(BACKOFF_BASE_SECONDS ** attempt)
