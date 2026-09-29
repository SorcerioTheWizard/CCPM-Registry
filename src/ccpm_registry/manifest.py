"""
CCPM Manifest Builder

Builds version manifests from files hosted on GitHub, gists, and Pastebin, pinning every file and recording its hash.
"""

# MARK: Imports
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from ccpm_registry.registry import PACKAGES_DIR
from ccpm_registry.verify import hash_text_file

# MARK: Constants
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
SCHEMA_REFERENCE = "../../schemas/version.schema.json"
TOKEN_VARIABLE = "GITHUB_TOKEN"
PASTEBIN_RAW = "https://pastebin.com/raw"
REPO_PATTERN = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
GIST_PATTERN = re.compile(r"^(?:https?://gist\.github(?:usercontent)?\.com/(?:[^/]+/)?)?([0-9a-f]{20,})(?:[/#?].*)?$")
# Links that serve the same file from a host CCPM allows
URL_REWRITES = (
    (re.compile(r"^https?://gist\.github\.com/([^/]+/[^/]+)/raw(/.*)?$"), r"https://gist.githubusercontent.com/\1/raw\2"),
    (re.compile(r"^https?://github\.com/([^/]+/[^/]+)/raw/(.+)$"), r"https://raw.githubusercontent.com/\1/\2"),
    (re.compile(r"^https?://raw\.github\.com/(.+)$"), r"https://raw.githubusercontent.com/\1"),
)

# A raw link to one file of a gist, pinned to a revision
GIST_FILE_PATTERN = re.compile(r"^https://gist\.githubusercontent\.com/[^/]+/[0-9a-f]+/raw/[0-9a-f]{40}/([^/?#]+)$")
PASTE_PATTERN = re.compile(r"^(?:https?://pastebin\.com/(?:raw/)?)?([A-Za-z0-9]{4,})/?$")


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


def normalize_url(url: str) -> str:
    """
    Rewrites links to their raw file equivalents on allowed hosts.

    Args:
        url: The URL.

    Returns:
        The rewritten URL, or the URL unchanged.
    """
    for pattern, replacement in URL_REWRITES:
        if pattern.match(url):
            return pattern.sub(replacement, url)

    return url


def parse_gist_file(text: str) -> tuple[str, str] | None:
    """
    Reads a raw link to one file of a gist, pinned to a revision.

    Args:
        text: The link, from the gist page's `Raw` button.

    Returns:
        The file name and the link, or `None` if the text is not a pinned raw gist link.
    """
    url = normalize_url(text.strip())
    match = GIST_FILE_PATTERN.match(url)
    return (match.group(1), url) if match else None


def parse_repo(text: str) -> str | None:
    """
    Reads a GitHub repository from `owner/name` or its URL.

    Args:
        text: The repository or its URL.

    Returns:
        The repository, like `owner/name`, or `None` if the text is not one.
    """
    match = REPO_PATTERN.match(text.strip())
    return match.group(1) if match else None


def parse_gist(text: str) -> str | None:
    """
    Reads a gist ID from the ID itself or any link to the gist.

    Args:
        text: The gist ID or URL.

    Returns:
        The gist ID, or `None` if the text is not one.
    """
    match = GIST_PATTERN.match(text.strip())
    return match.group(1) if match else None


def parse_paste(text: str) -> str | None:
    """
    Reads a Pastebin code from the code itself or a link to the paste.

    Args:
        text: The paste code or URL.

    Returns:
        The paste code, or `None` if the text is not one.
    """
    match = PASTE_PATTERN.match(text.strip())
    return match.group(1) if match else None


def paste_url(code: str) -> str:
    """
    Gets the raw URL of a paste.

    Args:
        code: The paste code.

    Returns:
        The URL.
    """
    return f"{PASTEBIN_RAW}/{code}"


def github_file_url(repo: str, sha: str, path: str) -> str:
    """
    Gets the raw URL of a repository file pinned to a commit.

    Args:
        repo: The repository, like `owner/name`.
        sha: The commit SHA.
        path: The repository-relative file path.

    Returns:
        The URL.
    """
    return f"{GITHUB_RAW}/{repo}/{sha}/{quote(path)}"


def list_gist_files(client: httpx.Client, gist: str) -> list[tuple[str, str]]:
    """
    Lists a gist's files at its current revision.

    Args:
        client: The HTTP client.
        gist: The gist ID.

    Returns:
        Pairs of file names and raw URLs pinned to the current revision.

    Raises:
        httpx.HTTPError: If GitHub cannot find the gist.
    """
    response = client.get(f"{GITHUB_API}/gists/{gist}", headers=_api_headers())
    response.raise_for_status()
    return sorted((name, entry["raw_url"]) for name, entry in response.json()["files"].items())


def file_entry(client: httpx.Client, url: str, path: str) -> dict:
    """
    Builds a version manifest file entry, checking the URL serves a text file and recording its hash.

    Args:
        client: The HTTP client.
        url: The pinned file URL.
        path: The install path.

    Returns:
        The entry.

    Raises:
        httpx.HTTPError: If the download fails.
        NotTextError: If the URL does not serve a text file.
    """
    return {"url": url, "path": path, "sha256": hash_text_file(client, url)}


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


def expand_mappings(mappings: list[tuple[str, str]], tree: list[str]) -> list[tuple[str, str]]:
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
        ValueError: If a mapping matches no files, or a file is not text.
    """
    # Pin the files to a commit
    sha = resolve_commit(client, request.repo, request.ref)
    files = []
    for source, dest in expand_mappings(request.mappings, list_files(client, request.repo, sha)):
        files.append(file_entry(client, github_file_url(request.repo, sha, source), dest))

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
