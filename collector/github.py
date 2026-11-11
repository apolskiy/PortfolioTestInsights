"""Read-only client for the GitHub Actions REST API.

Created: 2026-08-16
Last updated: 2026-11-10

This file contains :class:`GitHubClient`, whose three public methods list a
repository's workflow runs, list its artifacts, and download one artifact zip;
``resolve_token``, which finds the API token; and :class:`GitHubError`, which
both raise when the API cannot be reached or refuses a request.

Ingestion builds one client per collection and calls it before anything else:
one run listing and one artifact listing per repository, then one download per
artifact it decides to read. A :class:`GitHubError` from a listing ends the
collection with exit code 1; one from a download is recorded as a
``download_failed`` anomaly for that artifact and the collection goes on.

**Only GET is ever issued.** The collector never writes to a source repository.

**Retries are bounded.** A request is tried up to ``MAX_ATTEMPTS`` times. A
transport error or a status in ``RETRYABLE_STATUSES`` waits and retries,
honouring a numeric ``Retry-After`` header and otherwise backing off
exponentially; any other non-success status raises at once.

**The token is never logged or written.** ``resolve_token`` reads the first set
variable of ``TOKEN_VARIABLES``, then falls back to ``gh auth token``; log
lines name where the token came from, never its value.

Specified by ``DESIGN.md`` section 9.
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

#: RETRYABLE STATUSES. 403 is included because GitHub returns it for secondary
#: rate limiting, not only for genuine authorization failures.
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({403, 429, 500, 502, 503, 504})

#: BOUNDED RETRIES: attempts per request, and the base for exponential backoff
#: in seconds (``DESIGN.md`` section 9, rate limits).
MAX_ATTEMPTS: Final[int] = 4
BACKOFF_BASE_SECONDS: Final[float] = 2.0

#: BOUNDED, so a request times out rather than hanging a scheduled job.
TIMEOUT_SECONDS: Final[int] = 60


class GitHubError(RuntimeError):
    """Raised when the API cannot be reached or refuses a request.

    The message never carries the token.
    """


def resolve_token() -> str:
    """Find a token without ever putting one in a log line or a file.

    Checks ``TOKEN_VARIABLES`` in order, then falls back to the locally
    authenticated ``gh`` CLI with a 30-second timeout.

    Returns:
        str: The token, stripped of surrounding whitespace.

    Raises:
        GitHubError: If no variable is set and ``gh`` is absent or fails, or if
            ``gh`` returns an empty token. The message names the variables that
            were checked and never echoes a value.
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
            token (str | None): Token to use. Resolved by :func:`resolve_token`
                when omitted.
            session (requests.Session | None): Pre-built session, for tests that
                stub transport.

        Returns:
            None

        Raises:
            GitHubError: If no token is given and none can be resolved.
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
            repo (str): Owner-qualified repository name.

        Returns:
            list[dict[str, Any]]: Run objects, newest first as the API returns
            them.

        Raises:
            GitHubError: If a page cannot be fetched.
        """
        return list(self._paginate(f"/repos/{repo}/actions/runs", "workflow_runs"))

    def list_artifacts(self, repo: str) -> list[dict[str, Any]]:
        """List every artifact for a repository.

        Args:
            repo (str): Owner-qualified repository name.

        Returns:
            list[dict[str, Any]]: Artifact objects, each carrying the
            ``workflow_run`` block that associates it with the run that produced
            it.

        Raises:
            GitHubError: If a page cannot be fetched.
        """
        return list(self._paginate(f"/repos/{repo}/actions/artifacts", "artifacts"))

    def download_artifact(self, repo: str, artifact_id: int) -> bytes:
        """Download one artifact archive.

        Authentication is required even for public repositories.

        Args:
            repo (str): Owner-qualified repository name.
            artifact_id (int): Artifact to fetch.

        Returns:
            bytes: The raw zip.

        Raises:
            GitHubError: If the download fails.
        """
        response = self._request(f"/repos/{repo}/actions/artifacts/{artifact_id}/zip")
        return response.content

    def _paginate(self, path: str, key: str) -> Iterator[dict[str, Any]]:
        """Walk every page of a list endpoint, ``PAGE_SIZE`` items at a time.

        Stops at an empty page or a short one, and requests nothing further
        once the caller stops iterating.

        Args:
            path (str): API path below the root.
            key (str): Name of the array within each response body.

        Returns:
            Iterator[dict[str, Any]]: A generator yielding one object at a time.

        Raises:
            GitHubError: If a page cannot be fetched.
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
            path (str): API path below the root.
            params (dict[str, Any] | None): Query parameters.

        Returns:
            requests.Response: The first successful response.

        Raises:
            GitHubError: If a status outside ``RETRYABLE_STATUSES`` comes back,
                or all ``MAX_ATTEMPTS`` attempts fail.
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
            attempt (int): 1-based attempt number just completed.
            response (requests.Response | None): The failed response, when
                there was one.

        Returns:
            None: Sleeps for a numeric ``Retry-After`` when the response carries
            one, otherwise ``BACKOFF_BASE_SECONDS ** attempt`` seconds.
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(int(retry_after))
                return
        time.sleep(BACKOFF_BASE_SECONDS ** attempt)
