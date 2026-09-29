"""
CCPM Registry Test Fixtures

Builds throwaway registries from the real schemas and hosts for tests.
"""

# MARK: Imports
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

# MARK: Constants
REPO_ROOT = Path(__file__).resolve().parent.parent
GOOD_HASH = "a" * 64


# MARK: Classes
class RegistryBuilder:
    """
    Writes packages into a throwaway registry.
    """

    # MARK: Initializer
    def __init__(self, root: Path) -> None:
        """
        Creates a registry with the real schemas and hosts.

        Args:
            root: The folder to build the registry in.
        """
        self.root = root
        shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
        shutil.copy(REPO_ROOT / "hosts.json", root / "hosts.json")

    # MARK: Functions
    def write(self, path: str, data: dict) -> Path:
        """
        Writes a JSON file into the registry.

        Args:
            path: The registry-relative path.
            data: The data to write.

        Returns:
            The absolute path written.
        """
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def package(self, name: str, versions: dict[str, dict] | None = None, **meta) -> None:
        """
        Writes a package and its versions.

        Args:
            name: The package name.
            versions: Version manifests keyed by version, defaulting to one program version `1.0.0`.
            **meta: Metadata overriding the defaults.
        """
        self.write(f"packages/{name}/package.json", {"name": name, "description": f"The {name} package.", "author": "Tester", **meta})
        for version, manifest in (versions if versions is not None else {"1.0.0": program(name)}).items():
            self.write(f"packages/{name}/{version}.json", manifest)


# MARK: Functions
def file_entry(path: str, url: str | None = None, sha256: str = GOOD_HASH) -> dict:
    """
    Builds a file entry on an allowed host.

    Args:
        path: The install path.
        url: The URL, defaulting to one on raw GitHub.
        sha256: The recorded hash.

    Returns:
        The file entry.
    """
    return {"url": url or f"https://raw.githubusercontent.com/example/repo/abc123/{path}", "path": path, "sha256": sha256}


def program(name: str, **extra) -> dict:
    """
    Builds a version manifest installing a single program.

    Args:
        name: The program name.
        **extra: Manifest keys to add.

    Returns:
        The version manifest.
    """
    return {"kind": "files", "files": [file_entry(f"bin/{name}.lua")], **extra}


@pytest.fixture
def builder(tmp_path: Path) -> RegistryBuilder:
    """
    Provides an empty registry to write packages into.

    Args:
        tmp_path: The pytest temporary folder.

    Returns:
        The registry builder.
    """
    return RegistryBuilder(tmp_path)
