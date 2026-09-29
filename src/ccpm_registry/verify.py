"""
CCPM File Verification

Downloads package files and checks them against the hashes recorded in their version manifests.
"""

# MARK: Imports
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import httpx

from ccpm_registry.registry import Problem

# MARK: Constants
TIMEOUT_SECONDS = 30
MAX_WORKERS = 8
USER_AGENT = "ccpm-registry"


# MARK: Functions
def create_client() -> httpx.Client:
    """
    Creates the HTTP client used for downloads.

    Returns:
        A client that follows redirects.
    """
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def hash_url(client: httpx.Client, url: str) -> str:
    """
    Downloads a URL and hashes the exact bytes served.

    Args:
        client: The HTTP client.
        url: The URL to download.

    Returns:
        The lowercase SHA-256 of the response body.

    Raises:
        httpx.HTTPError: If the download fails.
    """
    response = client.get(url)
    response.raise_for_status()
    return hashlib.sha256(response.content).hexdigest()


def _verify_entry(client: httpx.Client, where: str, entry: dict) -> Problem | None:
    """
    Checks one file entry against its recorded hash.

    Args:
        client: The HTTP client.
        where: The registry-relative path of the manifest.
        entry: The file entry.

    Returns:
        The problem found, or `None` if the file matches.
    """
    try:
        actual = hash_url(client, entry["url"])
    except httpx.HTTPError as error:
        return Problem(where, f"`{entry['url']}` could not be downloaded: {error}")

    if actual != entry["sha256"]:
        return Problem(where, f"`{entry['url']}` has SHA-256 `{actual}`, but `{entry['sha256']}` is recorded")

    return None


def verify_files(client: httpx.Client, manifests: dict[str, dict]) -> list[Problem]:
    """
    Downloads every file of the given manifests and checks their hashes.

    Args:
        client: The HTTP client.
        manifests: The manifests to check, keyed by their registry-relative paths.

    Returns:
        The problems found.
    """
    # Flatten the files to check
    jobs = [(where, entry) for where, manifest in manifests.items() for entry in manifest.get("files", [])]

    # Check them in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = pool.map(lambda job: _verify_entry(client, *job), jobs)

    return [problem for problem in results if problem is not None]
