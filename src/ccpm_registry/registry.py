"""
CCPM Registry Loader

Loads the registry's packages, versions, and allowed hosts from disk and checks them against the JSON schemas.
"""

# MARK: Imports
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from ccpm_registry.semver import Version, parse_version

# MARK: Constants
PACKAGES_DIR = "packages"
SCHEMAS_DIR = "schemas"
HOSTS_FILE = "hosts.json"
PACKAGE_FILE = "package.json"
PACKAGE_SCHEMA = "package.schema.json"
VERSION_SCHEMA = "version.schema.json"


# MARK: Classes
@dataclass(frozen=True)
class Problem:
    """
    Something wrong with a registry file.
    """

    # MARK: Properties
    path: str
    message: str

    # MARK: Dunders
    def __str__(self) -> str:
        """
        Formats the problem for output.

        Returns:
            The problem, like `packages/foo/1.0.0.json: message`.
        """
        return f"{self.path}: {self.message}"


@dataclass
class Package:
    """
    A package and all of its published versions.
    """

    # MARK: Properties
    name: str
    folder: str
    meta: dict
    versions: dict[Version, dict] = field(default_factory=dict)

    # MARK: Functions
    @property
    def latest(self) -> Version | None:
        """
        The highest release, or the highest prerelease if there are no releases.
        """
        releases = [version for version in self.versions if not version.prerelease]
        candidates = releases or list(self.versions)
        return max(candidates) if candidates else None

    def version_path(self, version: Version) -> str:
        """
        Gets the registry-relative path of a version manifest.

        Args:
            version: The version.

        Returns:
            The path, like `packages/foo/1.0.0.json`.
        """
        return f"{self.folder}/{version}.json"


@dataclass
class Registry:
    """
    Everything in the registry.
    """

    # MARK: Properties
    root: Path
    prefixes: list[str]
    packages: dict[str, Package] = field(default_factory=dict)

    # MARK: Functions
    def is_allowed_url(self, url: str) -> bool:
        """
        Checks if a URL is on an allowed host.

        Args:
            url: The URL to check.

        Returns:
            If the URL starts with an allowed prefix.
        """
        return any(url.startswith(prefix) for prefix in self.prefixes)


# MARK: Functions
def _read_json(root: Path, path: str, problems: list[Problem]) -> dict | None:
    """
    Reads a JSON object file, recording a problem if it cannot be read.

    Args:
        root: The registry root.
        path: The registry-relative path.
        problems: The list to record problems in.

    Returns:
        The parsed object, or `None` if it could not be read.
    """
    try:
        data = json.loads((root / path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        problems.append(Problem(path, f"cannot be read: {error}"))
        return None

    # Require an object at the top level
    if not isinstance(data, dict):
        problems.append(Problem(path, "must be a JSON object"))
        return None

    return data


def _check_schema(validator: Draft202012Validator, data: dict, path: str, problems: list[Problem]) -> bool:
    """
    Checks data against a schema, recording every violation.

    Args:
        validator: The schema validator.
        data: The data to check.
        path: The registry-relative path of the data.
        problems: The list to record problems in.

    Returns:
        If the data is valid.
    """
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.absolute_path))
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path)
        problems.append(Problem(path, f"{location or '(root)'}: {error.message}"))

    return not errors


def _load_validator(root: Path, name: str) -> Draft202012Validator:
    """
    Loads a schema from the registry's schema folder.

    Args:
        root: The registry root.
        name: The schema file name.

    Returns:
        A validator for the schema.
    """
    schema = json.loads((root / SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _load_package(root: Path, folder: str, validators: tuple[Draft202012Validator, Draft202012Validator], problems: list[Problem]) -> Package | None:
    """
    Loads a package folder and its versions.

    Args:
        root: The registry root.
        folder: The registry-relative package folder.
        validators: The package and version schema validators.
        problems: The list to record problems in.

    Returns:
        The loaded package, or `None` if its metadata is unusable.
    """
    package_validator, version_validator = validators

    # Load the metadata
    meta_path = f"{folder}/{PACKAGE_FILE}"
    meta = _read_json(root, meta_path, problems)
    if meta is None or not _check_schema(package_validator, meta, meta_path, problems):
        return None
    package = Package(meta["name"], folder, meta)

    # Load each version
    for version_file in sorted((root / folder).glob("*.json")):
        if version_file.name == PACKAGE_FILE:
            continue

        # Name the file after its version
        version_path = f"{folder}/{version_file.name}"
        try:
            version = parse_version(version_file.stem)
        except ValueError as error:
            problems.append(Problem(version_path, f"file name must be the version: {error}"))
            continue

        # Keep versions that match the schema
        manifest = _read_json(root, version_path, problems)
        if manifest is not None and _check_schema(version_validator, manifest, version_path, problems):
            package.versions[version] = manifest

    return package


def load_registry(root: Path) -> tuple[Registry, list[Problem]]:
    """
    Loads the registry from disk.

    Args:
        root: The registry root folder.

    Returns:
        The loaded registry and every problem found while loading it.
    """
    problems: list[Problem] = []

    # Load the allowed hosts
    hosts = _read_json(root, HOSTS_FILE, problems) or {}
    registry = Registry(root, list(hosts.get("prefixes", [])))

    # Load every package folder
    validators = (_load_validator(root, PACKAGE_SCHEMA), _load_validator(root, VERSION_SCHEMA))
    packages_dir = root / PACKAGES_DIR
    folders = sorted(path for path in packages_dir.iterdir() if path.is_dir()) if packages_dir.is_dir() else []
    for folder in folders:
        package = _load_package(root, f"{PACKAGES_DIR}/{folder.name}", validators, problems)
        if package is None:
            continue

        # Require the name to match the folder
        if package.name != folder.name:
            problems.append(Problem(f"{package.folder}/{PACKAGE_FILE}", f"name `{package.name}` must match the folder name `{folder.name}`"))
            continue
        registry.packages[package.name] = package

    return registry, problems
