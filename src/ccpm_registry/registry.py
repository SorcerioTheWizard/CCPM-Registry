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
EXTERNAL_DIR = "external"
SCHEMAS_DIR = "schemas"
HOSTS_FILE = "hosts.json"
PACKAGE_FILE = "package.json"
PACKAGE_SCHEMA = "package.schema.json"
VERSION_SCHEMA = "version.schema.json"
OVERRIDES_FILE = "overrides.json"
OVERRIDES_SCHEMA = "overrides.schema.json"


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

    @property
    def source(self) -> str | None:
        """
        The external source the package was synced from, or `None` for packages published directly.
        """
        return self.name.split("/", 1)[0] if "/" in self.name else None

    @property
    def listed(self) -> bool:
        """
        If the package belongs in the published index.
        """
        return bool(self.versions) and not self.meta.get("delisted", False)

    def version_path(self, version: Version) -> str:
        """
        Gets the registry-relative path of a version manifest.

        Args:
            version: The version.

        Returns:
            The path, like `packages/foo/1.0.0.json` or `external/pinestore/foo/1.0.0.json`.
        """
        return f"{self.folder}/{version}.json"

    def published_path(self, version: Version) -> str:
        """
        Gets the path clients download a version manifest from, relative to the published registry.

        Args:
            version: The version.

        Returns:
            The path, like `packages/foo/1.0.0.json` or `packages/pinestore/foo/1.0.0.json`.
        """
        return f"{PACKAGES_DIR}/{self.name}/{version}.json"


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
def read_json(root: Path, path: str, problems: list[Problem]) -> dict | None:
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


def write_json(root: Path, path: str, data: dict) -> None:
    """
    Writes a JSON file in the registry's readable style, creating its folder.

    Args:
        root: The registry root.
        path: The registry-relative path.
        data: The data to write.
    """
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def check_schema(validator: Draft202012Validator, data: dict, path: str, problems: list[Problem]) -> bool:
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


def load_validator(root: Path, name: str) -> Draft202012Validator:
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


def load_overrides(root: Path, source: str) -> tuple[dict[str, dict], list[Problem]]:
    """
    Loads the maintainer overrides of an external source, if it has any.

    Args:
        root: The registry root.
        source: The source name.

    Returns:
        The overrides keyed by project ID, and the problems found in the file.
    """
    path = f"{EXTERNAL_DIR}/{source}/{OVERRIDES_FILE}"
    if not (root / path).exists():
        return {}, []

    # Check the file against its schema
    problems: list[Problem] = []
    data = read_json(root, path, problems)
    if data is None or not check_schema(load_validator(root, OVERRIDES_SCHEMA), data, path, problems):
        return {}, problems

    return {key: value for key, value in data.items() if key != "$schema"}, []


def external_sources(root: Path) -> list[str]:
    """
    Lists the external sources that have a folder in the registry.

    Args:
        root: The registry root.

    Returns:
        The source names, sorted.
    """
    external_dir = root / EXTERNAL_DIR
    return sorted(path.name for path in external_dir.iterdir() if path.is_dir()) if external_dir.is_dir() else []


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
    meta = read_json(root, meta_path, problems)
    if meta is None or not check_schema(package_validator, meta, meta_path, problems):
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
        manifest = read_json(root, version_path, problems)
        if manifest is not None and check_schema(version_validator, manifest, version_path, problems):
            package.versions[version] = manifest

    return package


def _package_folders(root: Path) -> list[tuple[str, str]]:
    """
    Lists every package folder with the name its package must have.

    Args:
        root: The registry root.

    Returns:
        Pairs of registry-relative folders and expected names, like `("external/pinestore/foo", "pinestore/foo")`.
    """
    folders = []

    # List packages published directly
    packages_dir = root / PACKAGES_DIR
    if packages_dir.is_dir():
        folders.extend((f"{PACKAGES_DIR}/{path.name}", path.name) for path in sorted(packages_dir.iterdir()) if path.is_dir())

    # List packages synced from each external source
    for source in external_sources(root):
        source_dir = root / EXTERNAL_DIR / source
        folders.extend((f"{EXTERNAL_DIR}/{source}/{path.name}", f"{source}/{path.name}") for path in sorted(source_dir.iterdir()) if path.is_dir())

    return folders


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
    hosts = read_json(root, HOSTS_FILE, problems) or {}
    registry = Registry(root, list(hosts.get("prefixes", [])))

    # Load every package folder
    validators = (load_validator(root, PACKAGE_SCHEMA), load_validator(root, VERSION_SCHEMA))
    for folder, expected in _package_folders(root):
        package = _load_package(root, folder, validators, problems)
        if package is None:
            continue

        # Require the name to match the folder
        if package.name != expected:
            problems.append(Problem(f"{package.folder}/{PACKAGE_FILE}", f"name `{package.name}` must be `{expected}` to match its folder"))
            continue
        registry.packages[package.name] = package

    return registry, problems
