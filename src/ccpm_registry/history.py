"""
CCPM Registry History

Compares the registry against a git base revision to find new versions and changes to published ones.
"""

# MARK: Imports
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ccpm_registry.registry import PACKAGE_FILE, PACKAGES_DIR, Problem


# MARK: Classes
@dataclass
class VersionChanges:
    """
    Version manifests changed since a base revision.
    """

    # MARK: Properties
    added: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


# MARK: Functions
def _is_version_file(path: str) -> bool:
    """
    Checks if a registry-relative path is a version manifest.

    Args:
        path: The path to check.

    Returns:
        If the path is `packages/<name>/<version>.json`.
    """
    parts = path.split("/")
    return len(parts) == 3 and parts[0] == PACKAGES_DIR and parts[2].endswith(".json") and parts[2] != PACKAGE_FILE


def version_changes(root: Path, base: str) -> VersionChanges:
    """
    Finds version manifests added since a base revision and flags any published version that was changed.

    Args:
        root: The registry root, which must be a git checkout.
        base: The git revision to compare against, like `origin/master`.

    Returns:
        The added manifests and problems for changed ones.

    Raises:
        subprocess.CalledProcessError: If git fails.
    """
    # List changes between the merge base and `HEAD`
    output = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", f"{base}...", "--", PACKAGES_DIR],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    # Sort the changes into additions and violations
    changes = VersionChanges()
    for line in output.splitlines():
        status, path = line.split("\t", 1)
        if not _is_version_file(path):
            continue
        if status == "A":
            changes.added.append(path)
        else:
            changes.problems.append(Problem(path, "published versions must never change; publish a new version instead"))

    return changes
