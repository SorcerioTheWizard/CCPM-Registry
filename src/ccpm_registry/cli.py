"""
CCPM Registry CLI

Command line entry point for validating, verifying, building, and syncing the registry.
"""

# MARK: Imports
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ccpm_registry.build import build_dist
from ccpm_registry.history import version_changes
from ccpm_registry.manifest import ManifestRequest, build_manifest, write_manifest
from ccpm_registry.registry import PACKAGE_FILE, PACKAGES_DIR, Problem, Registry, load_registry
from ccpm_registry.sources import SOURCES
from ccpm_registry.sync import sync_source
from ccpm_registry.validate import validate_registry
from ccpm_registry.verify import create_client, hash_url, verify_files


# MARK: Functions
def _report(problems: list[Problem]) -> int:
    """
    Prints problems and converts them to an exit code.

    Args:
        problems: The problems to report.

    Returns:
        `1` if there were problems, otherwise `0`.
    """
    for problem in problems:
        print(problem, file=sys.stderr)

    if problems:
        print(f"{len(problems)} problem(s) found.", file=sys.stderr)
        return 1

    return 0


def _load_valid(root: Path) -> tuple[Registry, list[Problem]]:
    """
    Loads the registry and runs every offline check.

    Args:
        root: The registry root.

    Returns:
        The registry and the problems found.
    """
    registry, problems = load_registry(root)
    return registry, problems + validate_registry(registry)


def _command_validate(args: argparse.Namespace) -> int:
    """
    Runs the offline checks, and checks for changed published versions when a base is given.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    _, problems = _load_valid(args.root)
    if args.base:
        problems += version_changes(args.root, args.base).problems

    # Report success for humans
    code = _report(problems)
    if code == 0:
        print("Registry is valid.")

    return code


def _command_verify(args: argparse.Namespace) -> int:
    """
    Downloads package files and checks their hashes.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    registry, problems = load_registry(args.root)
    if problems:
        return _report(problems)

    # Collect the manifests of listed packages published directly, since synced packages are fetched live
    manifests = {
        package.version_path(version): manifest
        for package in registry.packages.values()
        if package.source is None and package.listed
        for version, manifest in package.versions.items()
    }

    # Keep only the ones added since the base
    if args.base:
        added = set(version_changes(args.root, args.base).added)
        manifests = {where: manifest for where, manifest in manifests.items() if where in added}

    # Check the files
    with create_client() as client:
        code = _report(verify_files(client, manifests))
    if code == 0:
        print(f"Verified the files of {len(manifests)} version(s).")

    return code


def _command_build(args: argparse.Namespace) -> int:
    """
    Validates the registry and writes its published form.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    registry, problems = _load_valid(args.root)
    if problems:
        return _report(problems)

    build_dist(registry, args.out)
    print(f"Built {len(registry.packages)} package(s) into `{args.out}`.")

    return 0


def _command_hash(args: argparse.Namespace) -> int:
    """
    Prints the SHA-256 of each URL, for writing version manifests.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    registry, _ = load_registry(args.root)
    code = 0
    with create_client() as client:
        for url in args.urls:
            # Warn about hosts the registry would reject
            if not registry.is_allowed_url(url):
                print(f"warning: `{url}` is not on an allowed host (see `hosts.json`)", file=sys.stderr)

            # Hash the file
            try:
                print(f"{hash_url(client, url)}  {url}")
            except httpx.HTTPError as error:
                print(f"`{url}` could not be downloaded: {error}", file=sys.stderr)
                code = 1

    return code


def _split_pair(text: str, what: str) -> tuple[str, str]:
    """
    Splits a `left=right` argument.

    Args:
        text: The argument.
        what: What the argument is, for the error message.

    Returns:
        The two sides.

    Raises:
        argparse.ArgumentTypeError: If the argument has no `=`.
    """
    left, separator, right = text.partition("=")
    if not separator or not left or not right:
        raise argparse.ArgumentTypeError(f"`{text}` must look like {what}")

    return left, right


def _command_manifest(args: argparse.Namespace) -> int:
    """
    Writes a version manifest for files hosted on GitHub.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    # Collect the manifest's contents
    request = ManifestRequest(
        repo=args.github,
        ref=args.ref,
        mappings=args.mappings,
        dependencies=dict(args.depends),
        compat={key: value for key, value in (("cc", args.cc), ("mc", args.mc)) if value},
        startup=args.startup,
    )

    # Build and write it
    try:
        with create_client() as client:
            manifest = build_manifest(client, request)
        path = write_manifest(args.root, args.package, args.version, manifest)
    except (httpx.HTTPError, ValueError, FileExistsError) as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Wrote `{path.relative_to(args.root).as_posix()}` with {len(manifest['files'])} file(s).")
    if not (args.root / PACKAGES_DIR / args.package / PACKAGE_FILE).exists():
        print(f"Add `packages/{args.package}/package.json` before publishing.")

    return 0


def _command_sync(args: argparse.Namespace) -> int:
    """
    Mirrors an external catalog into the registry.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.
    """
    source = SOURCES[args.source]()
    try:
        with create_client() as client:
            report = sync_source(args.root, source, client, datetime.now(UTC))
    except (httpx.HTTPError, ValueError) as error:
        print(f"`{args.source}` could not be synced: {error}", file=sys.stderr)
        return 1

    # Report failed projects without failing the sync, so one broken project cannot block the rest
    print(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """
    Builds the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(prog="ccpm-registry", description="Tools for maintaining the CCPM registry.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the registry root folder (default: the current folder)")
    commands = parser.add_subparsers(dest="command", required=True)

    # Add the offline checks
    validate = commands.add_parser("validate", help="check the registry without downloading anything")
    validate.add_argument("--base", help="a git revision; also fail if versions published before it were changed")
    validate.set_defaults(handler=_command_validate)

    # Add the download checks
    verify = commands.add_parser("verify", help="download package files and check their hashes")
    verify.add_argument("--base", help="a git revision; only check versions added since it")
    verify.set_defaults(handler=_command_verify)

    # Add the build
    build = commands.add_parser("build", help="write the published registry")
    build.add_argument("--out", type=Path, default=Path("dist"), help="the output folder, which is replaced (default: `dist`)")
    build.set_defaults(handler=_command_build)

    # Add the hashing helper
    hash_command = commands.add_parser("hash", help="print the SHA-256 of files to publish")
    hash_command.add_argument("urls", nargs="+", help="the raw file URLs")
    hash_command.set_defaults(handler=_command_hash)

    # Add the manifest writer
    manifest = commands.add_parser("manifest", help="write a version manifest for files hosted on GitHub, pinned to a commit")
    manifest.add_argument("package", help="the package name")
    manifest.add_argument("version", help="the version to publish")
    manifest.add_argument("mappings", nargs="+", type=lambda text: _split_pair(text, "`<repo path>=<install path>`"), help="`<repo path>=<install path>`; a folder maps every file inside it")
    manifest.add_argument("--github", required=True, metavar="OWNER/REPO", help="the repository hosting the files")
    manifest.add_argument("--ref", default="HEAD", help="the branch, tag, or commit to pin (default: the default branch)")
    manifest.add_argument("--depends", action="append", default=[], type=lambda text: _split_pair(text, "`<package>=<range>`"), metavar="PACKAGE=RANGE", help="a dependency; repeat for more")
    manifest.add_argument("--cc", metavar="RANGE", help="the ComputerCraft versions it works on")
    manifest.add_argument("--mc", metavar="RANGE", help="the Minecraft versions it works on")
    manifest.add_argument("--startup", metavar="PATH", help="a `bin/` file to run at boot")
    manifest.set_defaults(handler=_command_manifest)

    # Add the external source mirror
    sync = commands.add_parser("sync", help="mirror an external catalog into `external/<source>/`")
    sync.add_argument("source", choices=sorted(SOURCES), help="the catalog to mirror")
    sync.set_defaults(handler=_command_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Runs the CLI.

    Args:
        argv: The arguments, or `None` to use the process arguments.

    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)
    return args.handler(args)
