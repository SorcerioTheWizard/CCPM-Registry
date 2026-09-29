"""
CCPM Registry History Tests

Tests for finding new versions and changes to published ones with git.
"""

# MARK: Imports
import subprocess

from ccpm_registry.history import version_changes

from conftest import program


# MARK: Functions
def git(root, *args) -> None:
    """
    Runs git quietly in the registry.

    Args:
        root: The registry root.
        *args: The git arguments.
    """
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=root, check=True, capture_output=True)


# MARK: Tests
def test_finds_added_and_changed_versions(builder):
    # Publish a first version
    git(builder.root, "init", "-q", "-b", "master")
    builder.package("tool", {"1.0.0": program("tool")})
    git(builder.root, "add", ".")
    git(builder.root, "commit", "-q", "-m", "base")
    git(builder.root, "branch", "base")

    # Change it, add a version, and edit the metadata
    builder.package("tool", {"1.0.0": program("tool", startup="bin/tool.lua"), "1.1.0": program("tool")}, license="MIT")
    git(builder.root, "add", ".")
    git(builder.root, "commit", "-q", "-m", "change")

    changes = version_changes(builder.root, "base")
    assert changes.added == ["packages/tool/1.1.0.json"]
    assert [problem.path for problem in changes.problems] == ["packages/tool/1.0.0.json"]


def test_protects_external_versions(builder):
    git(builder.root, "init", "-q", "-b", "master")
    builder.write("external/fake/radar/package.json", {"name": "fake/radar"})
    builder.write("external/fake/radar/2026.901.1.json", program("radar"))
    git(builder.root, "add", ".")
    git(builder.root, "commit", "-q", "-m", "base")
    git(builder.root, "branch", "base")

    # Change the published version and add another
    builder.write("external/fake/radar/2026.901.1.json", program("radar", startup="bin/radar.lua"))
    builder.write("external/fake/radar/2026.902.1.json", program("radar"))
    builder.write("external/fake/radar/package.json", {"name": "fake/radar", "delisted": True})
    git(builder.root, "add", ".")
    git(builder.root, "commit", "-q", "-m", "change")

    changes = version_changes(builder.root, "base")
    assert changes.added == ["external/fake/radar/2026.902.1.json"]
    assert [problem.path for problem in changes.problems] == ["external/fake/radar/2026.901.1.json"]
