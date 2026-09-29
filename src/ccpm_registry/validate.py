"""
CCPM Registry Validation

Checks the rules the JSON schemas cannot express, like install paths, hosts, dependencies, and program name clashes.
"""

# MARK: Imports
from __future__ import annotations

import re
from collections import defaultdict

from ccpm_registry.registry import Package, Problem, Registry
from ccpm_registry.semver import parse_range

# MARK: Constants
SAFE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
BIN_PATTERN = re.compile(r"^bin/[A-Za-z0-9_-]+\.lua$")
COMPAT_KEYS = ("cc", "mc")


# MARK: Functions
def _check_path(package: Package, path: str) -> str | None:
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

    # Keep libraries under the package's own module name
    if segments[0] == "lib":
        if path == f"lib/{package.name}.lua" or (len(segments) > 2 and segments[1] == package.name):
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
        path_problem = _check_path(package, entry["path"])
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


def _check_native(package: Package, manifest: dict, where: str) -> list[Problem]:
    """
    Checks rules that only apply to packages published directly to this registry.

    Args:
        package: The package.
        manifest: The version manifest.
        where: The registry-relative path of the manifest.

    Returns:
        The problems found.
    """
    # Require tracked files so installs can be removed and verified
    if manifest["kind"] != "files":
        return [Problem(where, "packages published to this registry must use the `files` kind")]

    return []


def _check_program_owners(registry: Registry) -> list[Problem]:
    """
    Checks that no two packages install a program with the same name.

    Args:
        registry: The registry.

    Returns:
        The problems found.
    """
    # Collect the owners of every program path
    owners: dict[str, set[str]] = defaultdict(set)
    for package in registry.packages.values():
        for manifest in package.versions.values():
            for entry in manifest.get("files", []):
                if entry["path"].startswith("bin/"):
                    owners[entry["path"]].add(package.name)

    # Report shared paths
    return [
        Problem("packages", f"program `{path}` is installed by more than one package: {', '.join(sorted(names))}")
        for path, names in sorted(owners.items())
        if len(names) > 1
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
            problems.extend(_check_native(package, manifest, where))
            problems.extend(_check_files(registry, package, manifest, where))
            problems.extend(_check_ranges(registry, package, manifest, where))

    # Check clashes between packages
    problems.extend(_check_program_owners(registry))

    return problems

