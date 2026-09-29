"""
CCPM Manifest Builder

Writes version manifests for packages hosted on GitHub, pinning every file to a commit and recording its hash.
"""

# MARK: Imports
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from ccpm_registry.registry import PACKAGES_DIR
from ccpm_registry.verify import hash_url

# MARK: Constants
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
SCHEMA_REFERENCE = "../../schemas/version.schema.json"
TOKEN_VARIABLE = "GITHUB_TOKEN"


# MARK: Classes
@dataclass
class ManifestRequest:
    """
    What to put in a version manifest.
    """

    # MARK: Properties
    repo: str
    ref: str
    mappings: list[tuple[str, str]]
    dependencies: dict[str, str] = field(default_factory=dict)
    compat: dict[str, str] = field(default_factory=dict)
    startup: str | None = None


# MARK: Functions
def _api_headers() -> dict[str, str]:
    """
    Builds GitHub API headers, using a token from the environment when one is set to raise the rate limit.

    Returns:
        The headers.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get(TOKEN_VARIABLE)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def resolve_commit(client: httpx.Client, repo: str, ref: str) -> str:
    """
    Resolves a branch, tag, or commit to the full commit SHA so file URLs never change.

    Args:
        client: The HTTP client.
        repo: The repository, like `owner/name`.
        ref: The branch, tag, or commit.

    Returns:
        The full commit SHA.

    Raises:
        httpx.HTTPError: If GitHub cannot resolve the ref.
    """
    response = client.get(f"{GITHUB_API}/repos/{repo}/commits/{quote(ref, safe='')}", headers=_api_headers())
    response.raise_for_status()
    return response.json()["sha"]


def list_files(client: httpx.Client, repo: str, sha: str) -> list[str]:
    """
    Lists every file in a repository at a commit.

    Args:
        client: The HTTP client.
        repo: The repository, like `owner/name`.
        sha: The commit SHA.

    Returns:
        The repository-relative file paths.

    Raises:
        httpx.HTTPError: If GitHub cannot list the files.
    """
    response = client.get(f"{GITHUB_API}/repos/{repo}/git/trees/{sha}", params={"recursive": "1"}, headers=_api_headers())
    response.raise_for_status()
    return [item["path"] for item in response.json()["tree"] if item["type"] == "blob"]


def _expand_mappings(mappings: list[tuple[str, str]], tree: list[str]) -> list[tuple[str, str]]:
    """
    Expands folder mappings into one mapping per file.

    Args:
        mappings: Repository paths mapped to install paths. A folder maps every file inside it.
        tree: Every file in the repository.

    Returns:
        Repository file paths mapped to install paths.

    Raises:
        ValueError: If a source matches no files.
    """
    files = set(tree)
    expanded = []
    for source, dest in mappings:
        # Map a single file
        if source in files:
            expanded.append((source, dest))
            continue

        # Map every file in a folder
        prefix = source.rstrip("/") + "/"
        matched = sorted(path for path in tree if path.startswith(prefix))
        if not matched:
            raise ValueError(f"`{source}` is not a file or folder in the repository")
        expanded.extend((path, dest.rstrip("/") + "/" + path[len(prefix) :]) for path in matched)

    return expanded


def build_manifest(client: httpx.Client, request: ManifestRequest) -> dict:
    """
    Builds a version manifest, downloading every file to record its hash.

    Args:
        client: The HTTP client.
        request: What to put in the manifest.

    Returns:
        The version manifest.

    Raises:
        httpx.HTTPError: If GitHub or a file download fails.
        ValueError: If a mapping matches no files.
    """
    # Pin the files to a commit
    sha = resolve_commit(client, request.repo, request.ref)
    files = []
    for source, dest in _expand_mappings(request.mappings, list_files(client, request.repo, sha)):
        url = f"{GITHUB_RAW}/{request.repo}/{sha}/{quote(source)}"
        files.append({"url": url, "path": dest, "sha256": hash_url(client, url)})

    # Add the optional keys that were given
    manifest = {"$schema": SCHEMA_REFERENCE, "kind": "files", "files": sorted(files, key=lambda entry: entry["path"])}
    if request.dependencies:
        manifest["dependencies"] = request.dependencies
    if request.compat:
        manifest["compat"] = request.compat
    if request.startup:
        manifest["startup"] = request.startup

    return manifest


def write_manifest(root: Path, package: str, version: str, manifest: dict) -> Path:
    """
    Writes a version manifest into the registry without replacing an existing one.

    Args:
        root: The registry root.
        package: The package name.
        version: The version.
        manifest: The manifest.

    Returns:
        The path written.

    Raises:
        FileExistsError: If the version already exists.
    """
    path = root / PACKAGES_DIR / package / f"{version}.json"
    if path.exists():
        raise FileExistsError(f"`{path.relative_to(root).as_posix()}` already exists; published versions never change")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8", newline="\n")
    return path
