"""
CCPM Registry Build

Writes the published form of the registry that CCPM clients download.
"""

# MARK: Imports
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ccpm_registry.registry import Package, Registry

# MARK: Constants
INDEX_FILE = "index.json"
INDEX_FORMAT = 1
META_KEYS = ("description", "author", "license", "repository", "homepage", "tags", "origin")


# MARK: Functions
def _write_json(path: Path, data: dict) -> None:
    """
    Writes compact JSON with stable key order.

    Args:
        path: The file to write.
        data: The data to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False), encoding="utf-8", newline="\n")


def _index_entry(package: Package) -> dict:
    """
    Builds the compact index entry of a package.

    Args:
        package: The package.

    Returns:
        The entry with its metadata, latest version, and versions from highest to lowest.
    """
    entry = {key: package.meta[key] for key in META_KEYS if key in package.meta}
    entry["latest"] = str(package.latest)
    entry["versions"] = [str(version) for version in sorted(package.versions, reverse=True)]

    return entry


def build_index(registry: Registry) -> dict:
    """
    Builds the index clients download to search and resolve packages.

    Args:
        registry: The validated registry.

    Returns:
        The index data.
    """
    return {
        "format": INDEX_FORMAT,
        "hosts": registry.prefixes,
        "packages": {name: _index_entry(package) for name, package in sorted(registry.packages.items()) if package.listed},
    }


def build_dist(registry: Registry, out: Path) -> None:
    """
    Writes the index and every version manifest to an output folder, replacing its contents.

    Args:
        registry: The validated registry.
        out: The output folder.
    """
    # Start from an empty folder
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # Write the index
    _write_json(out / INDEX_FILE, build_index(registry))

    # Write each listed version manifest without its editor-only schema reference
    for package in registry.packages.values():
        if not package.listed:
            continue
        for version, manifest in package.versions.items():
            published = {key: value for key, value in manifest.items() if key != "$schema"}
            _write_json(out / package.published_path(version), published)
