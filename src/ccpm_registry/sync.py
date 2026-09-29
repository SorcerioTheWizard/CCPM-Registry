"""
CCPM Source Sync

Mirrors an external catalog into `external/<source>/`, publishing a new version whenever what a project installs changes.
"""

# MARK: Imports
from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ccpm_registry.registry import EXTERNAL_DIR, PACKAGE_FILE, Package, Registry, load_overrides, load_registry, write_json
from ccpm_registry.semver import Version
from ccpm_registry.sources.base import ExternalProject, Source, slugify
from ccpm_registry.validate import provides

# MARK: Constants
PACKAGE_SCHEMA_REFERENCE = "../../../schemas/package.schema.json"
VERSION_SCHEMA_REFERENCE = "../../../schemas/version.schema.json"
DESCRIPTION_LIMIT = 200
AUTHOR_LIMIT = 64
STARTUP_NAME = "startup"

# Downloaded files named like this are installers the author expects to be run next
INSTALLER_NAME_PATTERN = re.compile(r"install", re.IGNORECASE)

# GitHub serves files only from release downloads, like `releases/download/v1/tool.lua` or `releases/latest/download/tool.lua`
GITHUB_PREFIX = "https://github.com/"
GITHUB_FILE_PATTERN = re.compile(r"^https://github\.com/[^/]+/[^/]+/releases/(latest/)?download/.+")

# Links that serve the same file from a host CCPM allows
URL_REWRITES = (
    (re.compile(r"^https?://gist\.github\.com/([^/]+/[^/]+)/raw(/.*)?$"), r"https://gist.githubusercontent.com/\1/raw\2"),
    (re.compile(r"^https?://github\.com/([^/]+/[^/]+)/raw/(.+)$"), r"https://raw.githubusercontent.com/\1/\2"),
    (re.compile(r"^https?://raw\.github\.com/(.+)$"), r"https://raw.githubusercontent.com/\1"),
)


# MARK: Classes
@dataclass
class DownloadCommand:
    """
    A command that downloads a single file, which CCPM can install and track itself.
    """

    # MARK: Properties
    url: str
    file: str | None
    run: str


@dataclass
class SyncReport:
    """
    What a sync changed.
    """

    # MARK: Properties
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    delisted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    # MARK: Dunders
    def __str__(self) -> str:
        """
        Summarizes the sync for the workflow log.

        Returns:
            One line per changed package, then totals.
        """
        lines = [f"added {name}" for name in self.added] + [f"updated {name}" for name in self.updated] + [f"delisted {name}" for name in self.delisted]
        lines += [f"skipped {reason}" for reason in self.skipped] + [f"failed {reason}" for reason in self.failed]
        lines.append(f"{len(self.added)} added, {len(self.updated)} updated, {len(self.delisted)} delisted, {len(self.skipped)} skipped, {len(self.failed)} failed")
        return "\n".join(lines)


# MARK: Functions
def date_version(moment: datetime) -> Version:
    """
    Turns a time into a version that sorts by time, like `2026.929.143005` for 2026-09-29 14:30:05 UTC.

    Args:
        moment: The time, which must be timezone aware.

    Returns:
        The version.
    """
    utc = moment.astimezone(UTC)
    return Version(utc.year, utc.month * 100 + utc.day, utc.hour * 10000 + utc.minute * 100 + utc.second)


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


def is_web_page(url: str) -> bool:
    """
    Recognizes GitHub links that serve a web page or an archive instead of a release file, like a repository page.

    Args:
        url: The URL, after `normalize_url`.

    Returns:
        If the URL is known not to serve a file CCPM can install.
    """
    return url.startswith(GITHUB_PREFIX) and not GITHUB_FILE_PATTERN.match(url)


def parse_download(command: str) -> DownloadCommand | None:
    """
    Recognizes a command that only downloads one file, like `wget <url> <file>` or `pastebin get <code> <file>`.

    Args:
        command: The install command.

    Returns:
        The download, or `None` if the command does anything else, like `wget run`.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    # Read `wget <url> [file]`
    if len(tokens) in (2, 3) and tokens[0] == "wget" and tokens[1].startswith(("http://", "https://")):
        return DownloadCommand(url=tokens[1], file=tokens[2] if len(tokens) == 3 else None, run=f"wget run {tokens[1]}")

    # Read `pastebin get <code> [file]`
    if len(tokens) in (3, 4) and tokens[:2] == ["pastebin", "get"] and re.fullmatch(r"[A-Za-z0-9]+", tokens[2]):
        return DownloadCommand(url=f"https://pastebin.com/raw/{tokens[2]}", file=tokens[3] if len(tokens) == 4 else None, run=f"pastebin run {tokens[2]}")

    return None


def _program_name(*candidates: str | None) -> str:
    """
    Picks a program or module name from the first usable file name.

    Args:
        *candidates: File names or paths, best first.

    Returns:
        The name without `.lua`, using only letters, digits, `-`, and `_`, or an empty string.
    """
    for candidate in candidates:
        base = posixpath.basename(urlparse(candidate).path if candidate and "://" in candidate else (candidate or "")).strip()
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", base.removesuffix(".lua")).strip("-")
        if name:
            return name

    return ""


class _Sync:
    """
    One run of mirroring a source into the registry.
    """

    # MARK: Initializer
    def __init__(self, root: Path, source: Source, client: httpx.Client, now: datetime) -> None:
        """
        Prepares a sync.

        Args:
            root: The registry root.
            source: The source to mirror.
            client: The HTTP client for hashing files.
            now: The time of the sync, used to version changes the source did not date.
        """
        self.root = root
        self.source = source
        self.client = client
        self.now = now
        self.report = SyncReport()
        self.registry: Registry = load_registry(root)[0]

        # Refuse to sync with broken overrides
        self.overrides, problems = load_overrides(root, source.name)
        if problems:
            raise ValueError("\n".join(str(problem) for problem in problems))

        # Index this source's packages by project ID
        self.existing: dict[str, Package] = {}
        for package in self.registry.packages.values():
            origin = package.meta.get("origin") or {}
            if package.source == source.name and origin.get("id"):
                self.existing[origin["id"]] = package

        # Record which names every package already takes
        self.taken: dict[str, str] = {}
        for package in self.registry.packages.values():
            if not package.meta.get("delisted") and package.latest is not None:
                for name in provides(package.versions[package.latest]):
                    self.taken[name] = package.name

    # MARK: Private Functions
    def _slug(self, project: ExternalProject, override: dict) -> str:
        """
        Picks a package name that never changes once assigned.

        Args:
            project: The project.
            override: The project's overrides.

        Returns:
            The name after the source prefix.
        """
        # Keep the name of a package synced before
        package = self.existing.get(project.id)
        if package:
            return package.name.split("/", 1)[1]

        # Name it after the project, avoiding names already used
        slug = override.get("slug") or slugify(project.title) or f"project-{project.id}"
        used = {package.name for package in self.registry.packages.values()}
        if f"{self.source.name}/{slug}" in used:
            slug = f"{slug[: 48 - len(project.id) - 1]}-{project.id}"

        return slug

    def _claim(self, kind: str, name: str, package_name: str, project_id: str) -> str:
        """
        Claims a program or library name, adding the project ID when another package already has it.

        Args:
            kind: `program` or `library`.
            name: The wanted name.
            package_name: The package claiming it.
            project_id: The project ID.

        Returns:
            The claimed name.
        """
        owner = self.taken.get(f"{kind} {name}")
        if owner is not None and owner != package_name:
            name = f"{name}-{project_id}"
        self.taken[f"{kind} {name}"] = package_name

        return name

    def _manifest(self, project: ExternalProject, override: dict, package_name: str, slug: str) -> dict | None:
        """
        Builds the version manifest for what a project installs right now.

        Args:
            project: The project.
            override: The project's overrides.
            package_name: The package name.
            slug: The name after the source prefix.

        Returns:
            The manifest, or `None` if the project cannot be installed.
        """
        command = override.get("command") or project.command
        if not command:
            self.report.skipped.append(f"{package_name}: no install command")
            return None

        # Refuse GitHub pages and archives, which could never be a working program
        download = parse_download(command)
        url = normalize_url(download.url) if download else None
        if url and is_web_page(url):
            self.report.skipped.append(f"{package_name}: `{url}` is a GitHub page or archive, not a file download")
            return None

        # Run downloaded installers, since installing is what the author expects them to be used for
        manifest: dict = {"$schema": VERSION_SCHEMA_REFERENCE}
        name = _program_name(download.file, project.target, url) if download else ""
        if download and INSTALLER_NAME_PATTERN.search(name) and not override.get("command"):
            command = download.run
            download = None

        # Install other single file downloads from allowed hosts as tracked files, fetched live like the source's own command would
        if download and url and self.registry.is_allowed_url(url):
            library = override.get("library", project.library)
            name = name or slug

            # Name programs saved as `startup` after the package and run them at boot instead
            starts = name.lower() == STARTUP_NAME
            if starts:
                name = slug
                starts = not library

            # Place it where programs or libraries are found
            kind = "library" if library else "program"
            name = self._claim(kind, name, package_name, project.id)
            path = f"lib/{name}.lua" if library else f"bin/{name}.lua"

            manifest.update({"kind": "files", "files": [{"url": url, "path": path}]})
            if starts:
                manifest["startup"] = path
        else:
            # Run anything else as the project's own installer
            manifest.update({"kind": "installer", "installer": {"command": command}})

        # Add what the maintainers know
        for key in ("dependencies", "compat"):
            if override.get(key):
                manifest[key] = override[key]

        return manifest

    def _meta(self, project: ExternalProject, package_name: str) -> dict:
        """
        Builds the package metadata.

        Args:
            project: The project.
            package_name: The package name.

        Returns:
            The metadata.
        """
        description = project.description or project.title
        if len(description) > DESCRIPTION_LIMIT:
            description = description[: DESCRIPTION_LIMIT - 3].rsplit(" ", 1)[0] + "..."

        meta = {
            "$schema": PACKAGE_SCHEMA_REFERENCE,
            "name": package_name,
            "description": description,
            "author": project.author[:AUTHOR_LIMIT] or "unknown",
        }
        if project.tags:
            meta["tags"] = project.tags
        if project.repository:
            meta["repository"] = project.repository
        meta["origin"] = {"source": self.source.name, "id": project.id, "url": project.url}

        return meta

    def _publish(self, project: ExternalProject, package: Package | None, folder: str, manifest: dict) -> bool:
        """
        Publishes a new version when what the project installs changed.

        Args:
            project: The project.
            package: The package as synced before, if it was.
            folder: The registry-relative package folder.
            manifest: The current manifest.

        Returns:
            If a new version was published.
        """
        # Compare with the latest published version
        latest = package.latest if package else None
        if latest is not None and package is not None and package.versions[latest] == manifest:
            return False

        # Date the version when the source changed it, or now when only an override or this tool changed it
        version = date_version(project.updated)
        if latest is not None and version <= latest:
            version = date_version(self.now)
        if latest is not None and version <= latest:
            self.report.failed.append(f"{folder}: the new version would not be newer than {latest}")
            return False

        write_json(self.root, f"{folder}/{version}.json", manifest)
        return True

    def _sync_project(self, project: ExternalProject) -> bool:
        """
        Mirrors one project.

        Args:
            project: The project.

        Returns:
            If the project can be installed and belongs in the index.
        """
        override = self.overrides.get(project.id, {})
        package = self.existing.get(project.id)
        if override.get("skip"):
            self.report.skipped.append(f"{self.source.name} project {project.id}: skipped by an override")
            return False

        # Build the package
        slug = self._slug(project, override)
        package_name = f"{self.source.name}/{slug}"
        folder = f"{EXTERNAL_DIR}/{self.source.name}/{slug}"
        manifest = self._manifest(project, override, package_name, slug)
        if manifest is None:
            return False

        # Write the metadata when it changed
        meta = self._meta(project, package_name)
        if package is None or package.meta != meta:
            write_json(self.root, f"{folder}/{PACKAGE_FILE}", meta)

        # Publish a version when the install changed
        if self._publish(project, package, folder, manifest):
            (self.report.added if package is None else self.report.updated).append(package_name)
        elif package is not None and package.meta.get("delisted"):
            self.report.updated.append(package_name)

        return True

    # MARK: Functions
    def run(self) -> SyncReport:
        """
        Mirrors every project and delists the ones the source dropped or that cannot be installed.

        Returns:
            What changed.

        Raises:
            httpx.HTTPError: If the source cannot be downloaded.
            ValueError: If the source's response cannot be read.
        """
        # Mirror the listed projects
        projects = sorted(self.source.fetch(self.client), key=lambda project: (len(project.id), project.id))
        listed = {project.id for project in projects if self._sync_project(project)}

        # Delist the rest, keeping their versions
        for project_id, package in sorted(self.existing.items()):
            if project_id not in listed and not package.meta.get("delisted"):
                write_json(self.root, f"{package.folder}/{PACKAGE_FILE}", {**package.meta, "delisted": True})
                self.report.delisted.append(package.name)

        return self.report


def sync_source(root: Path, source: Source, client: httpx.Client, now: datetime) -> SyncReport:
    """
    Mirrors an external catalog into the registry.

    Args:
        root: The registry root.
        source: The source to mirror.
        client: The HTTP client.
        now: The time of the sync.

    Returns:
        What changed.

    Raises:
        httpx.HTTPError: If the source cannot be downloaded.
        ValueError: If the source's response or the overrides file cannot be read.
    """
    return _Sync(root, source, client, now).run()
