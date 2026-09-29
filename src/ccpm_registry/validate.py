"""
CCPM Registry Validation

Checks the rules the JSON schemas cannot express, like install paths, hosts, dependencies, and name clashes between packages.
"""

# MARK: Imports
from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from urllib.parse import urlparse

from ccpm_registry.registry import Package, Problem, Registry, external_sources, load_overrides
from ccpm_registry.semver import parse_range

# MARK: Constants
SAFE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
BIN_PATTERN = re.compile(r"^bin/[A-Za-z0-9_-]+\.lua$")
MODULE_FILE_PATTERN = re.compile(r"^lib/[A-Za-z0-9_-]+\.lua$")
COMPAT_KEYS = ("cc", "mc")


# MARK: Functions
def check_path(package: Package, path: str) -> str | None:
    """
    Checks that an install path is safe and inside a location the package may write to.

    Args:
        package: The package installing the file.
        path: The install path relative to the CCPM root.

    Returns:
        A description of the problem, or `None` if the path is allowed.
    """
    # Reject anything that could escape the CCPM root
    segments = path.split("/")
    if not SAFE_PATH_PATTERN.match(path) or any(segment in ("", ".", "..") for segment in segments):
        return f"path `{path}` must be relative and only use letters, digits, `.`, `_`, `-`, and `/`"

    # Allow programs directly in `bin`
    if segments[0] == "bin":
        if not BIN_PATTERN.match(path):
            return f"program `{path}` must be a `.lua` file directly inside `bin/`"
        return None

    # Keep libraries under the package's own module name, except single file libraries synced from external sources
    if segments[0] == "lib":
        if path == f"lib/{package.name}.lua" or (len(segments) > 2 and segments[1] == package.name):
            return None
        if package.source and MODULE_FILE_PATTERN.match(path):
            return None
        return f"library `{path}` must be `lib/{package.name}.lua` or inside `lib/{package.name}/`"

    # Keep read-only assets in the package's own folder
    if segments[0] == "share":
        if len(segments) > 2 and segments[1] == package.name:
            return None
        return f"asset `{path}` must be inside `share/{package.name}/`"

    return f"path `{path}` must be inside `bin/`, `lib/`, or `share/`"


def _check_files(registry: Registry, package: Package, manifest: dict, where: str) -> list[Problem]:
    """
    Checks the files and startup program of a version.

    Args:
        registry: The registry.
        package: The package.
        manifest: The version manifest.
        where: The registry-relative path of the manifest.

    Returns:
        The problems found.
    """
    problems = []
    seen = set()
    for entry in manifest.get("files", []):
        # Check the install path
        path_problem = check_path(package, entry["path"])
        if path_problem:
            problems.append(Problem(where, path_problem))

        # Reject duplicate install paths
        if entry["path"] in seen:
            problems.append(Problem(where, f"path `{entry['path']}` is listed more than once"))
        seen.add(entry["path"])

        # Require an allowed host
        if not registry.is_allowed_url(entry["url"]):
            problems.append(Problem(where, f"URL `{entry['url']}` is not on an allowed host (see `hosts.json`)"))

    # Require the startup program to be one of the installed programs
    startup = manifest.get("startup")
    if startup is not None and (startup not in seen or not startup.startswith("bin/")):
        problems.append(Problem(where, f"startup `{startup}` must be a `bin/` file listed in `files`"))

    return problems


def _check_ranges(registry: Registry, package: Package, manifest: dict, where: str) -> list[Problem]:
    """
    Checks the dependencies and compatibility ranges of a version.

    Args:
        registry: The registry.
        package: The package.
        manifest: The version manifest.
        where: The registry-relative path of the manifest.

    Returns:
        The problems found.
    """
    problems = []

    # Require every dependency to exist with a satisfying version
    for name, text in manifest.get("dependencies", {}).items():
        if name == package.name:
            problems.append(Problem(where, "a package cannot depend on itself"))
            continue
        try:
            version_range = parse_range(text)
        except ValueError as error:
            problems.append(Problem(where, f"dependency `{name}`: {error}"))
            continue
        dependency = registry.packages.get(name)
        if dependency is None:
            problems.append(Problem(where, f"dependency `{name}` is not in the registry"))
        elif version_range.best(list(dependency.versions)) is None:
            problems.append(Problem(where, f"no version of `{name}` satisfies `{text}`"))

    # Require readable compatibility ranges
    for key in COMPAT_KEYS:
        text = manifest.get("compat", {}).get(key)
        if text is None:
            continue
        try:
            parse_range(text)
        except ValueError as error:
            problems.append(Problem(where, f"compat `{key}`: {error}"))

    return problems


def _check_origin(package: Package, manifest: dict, where: str) -> list[Problem]:
    """
    Checks the rules that differ between packages published directly and packages synced from external sources.

    Args:
        package: The package.
        manifest: The version manifest.
        where: The registry-relative path of the manifest.

    Returns:
        The problems found.
    """
    origin = package.meta.get("origin")

    # Require tracked and hashed files for packages published directly, so installs can be removed and verified
    if package.source is None:
        if origin is not None:
            return [Problem(where, "only packages synced from an external source may have an `origin`")]
        if manifest["kind"] != "files":
            return [Problem(where, "packages published to this registry must use the `files` kind")]
        return [Problem(where, f"`{entry['path']}` must have a `sha256`") for entry in manifest["files"] if "sha256" not in entry]

    # Require synced packages to say where they came from
    if origin is None or origin["source"] != package.source:
        return [Problem(where, f"packages synced from `{package.source}` must have an `origin` with that source")]

    return []


def program_name(*candidates: str | None) -> str:
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


def provides(manifest: dict) -> set[str]:
    """
    Lists the names a version makes available on a computer, which must be unique across packages.

    Args:
        manifest: The version manifest.

    Returns:
        Names like `program tool` for `bin/tool.lua` and `library tool` for `lib/tool.lua` or `lib/tool/...`.
    """
    names = set()
    for entry in manifest.get("files", []):
        segments = entry["path"].split("/")
        if segments[0] == "bin":
            names.add(f"program {segments[-1].removesuffix('.lua')}")
        elif segments[0] == "lib" and len(segments) > 1:
            names.add(f"library {segments[1].removesuffix('.lua')}")

    return names


def _check_owners(registry: Registry) -> list[Problem]:
    """
    Checks that no two listed packages install a program or library with the same name.

    Args:
        registry: The registry.

    Returns:
        The problems found.
    """
    # Collect the owners of every name
    owners: dict[str, set[str]] = defaultdict(set)
    for package in registry.packages.values():
        if package.meta.get("delisted"):
            continue
        for manifest in package.versions.values():
            for name in provides(manifest):
                owners[name].add(package.name)

    # Report shared names
    return [
        Problem("packages", f"{name} is installed by more than one package: {', '.join(sorted(packages))}")
        for name, packages in sorted(owners.items())
        if len(packages) > 1
    ]


def validate_registry(registry: Registry) -> list[Problem]:
    """
    Checks every rule the schemas cannot express.

    Args:
        registry: The loaded registry.

    Returns:
        The problems found.
    """
    problems = []

    # Require at least one allowed host
    if not registry.prefixes:
        problems.append(Problem("hosts.json", "must list at least one URL prefix"))

    # Check every version of every package
    for package in registry.packages.values():
        if not package.versions:
            problems.append(Problem(package.folder, "has no versions"))
        for version, manifest in sorted(package.versions.items(), key=lambda item: item[0]):
            where = package.version_path(version)
            problems.extend(_check_origin(package, manifest, where))
            problems.extend(_check_files(registry, package, manifest, where))
            problems.extend(_check_ranges(registry, package, manifest, where))

    # Check clashes between packages
    problems.extend(_check_owners(registry))

    # Check the maintainer overrides of each external source
    for source in external_sources(registry.root):
        problems.extend(load_overrides(registry.root, source)[1])

    return problems

