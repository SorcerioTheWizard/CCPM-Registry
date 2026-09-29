"""
CCPM Registry Validation Tests

Tests for loading the registry and checking its rules.
"""

# MARK: Imports
import pytest

from ccpm_registry.registry import load_registry
from ccpm_registry.semver import parse_version
from ccpm_registry.validate import validate_registry

from conftest import file_entry, program


# MARK: Functions
def problems_of(builder) -> list[str]:
    """
    Loads and validates a built registry.

    Args:
        builder: The registry builder.

    Returns:
        Every problem as text.
    """
    registry, problems = load_registry(builder.root)
    return [str(problem) for problem in problems + validate_registry(registry)]


# MARK: Tests
def test_accepts_a_valid_registry(builder):
    builder.package("mylib", {"1.0.0": {"kind": "files", "files": [file_entry("lib/mylib.lua")]}})
    builder.package(
        "doorlock",
        {"1.0.0": program("doorlock", startup="bin/doorlock.lua", dependencies={"mylib": "^1.0.0"}, compat={"cc": ">=1.100", "mc": "1.20"})},
        license="MIT",
        tags=["security"],
    )

    assert problems_of(builder) == []


def test_loads_versions(builder):
    builder.package("tool", {"1.0.0": program("tool"), "1.1.0-beta": program("tool")})
    registry, _ = load_registry(builder.root)

    assert registry.packages["tool"].latest == parse_version("1.0.0")


def test_reports_schema_errors(builder):
    builder.write("packages/tool/package.json", {"name": "tool", "author": "Tester"})
    builder.package("other", {"1.0.0": {"kind": "files", "files": [{"url": "https://raw.githubusercontent.com/x", "path": "bin/other.lua"}]}})

    problems = problems_of(builder)
    assert any("packages/tool/package.json" in p and "description" in p for p in problems)
    assert any("packages/other/1.0.0.json" in p and "sha256" in p for p in problems)


def test_requires_matching_folder_and_version_names(builder):
    builder.write("packages/tool/package.json", {"name": "other", "description": "x", "author": "x"})
    builder.package("good", {"1.0": program("good")})

    problems = problems_of(builder)
    assert any("name `other` must be `tool` to match its folder" in p for p in problems)
    assert any("packages/good/1.0.json" in p and "file name must be the version" in p for p in problems)


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("../startup.lua", "must be relative"),
        ("/bin/tool.lua", "must be relative"),
        ("bin/sub/tool.lua", "directly inside `bin/`"),
        ("bin/tool", "directly inside `bin/`"),
        ("lib/other.lua", "must be `lib/tool.lua`"),
        ("lib/tool", "must be `lib/tool.lua`"),
        ("share/other/icon.nfp", "inside `share/tool/`"),
        ("startup.lua", "inside `bin/`, `lib/`, or `share/`"),
    ],
)
def test_rejects_unsafe_paths(builder, path, fragment):
    builder.package("tool", {"1.0.0": {"kind": "files", "files": [file_entry(path)]}})

    assert any(fragment in p for p in problems_of(builder))


def test_accepts_library_and_asset_folders(builder):
    files = [file_entry("lib/tool/init.lua"), file_entry("lib/tool/util/math.lua"), file_entry("share/tool/icon.nfp")]
    builder.package("tool", {"1.0.0": {"kind": "files", "files": files}})

    assert problems_of(builder) == []


def test_rejects_disallowed_hosts_and_duplicates(builder):
    files = [file_entry("bin/tool.lua", url="https://example.com/tool.lua"), file_entry("bin/tool.lua")]
    builder.package("tool", {"1.0.0": {"kind": "files", "files": files}})

    problems = problems_of(builder)
    assert any("not on an allowed host" in p for p in problems)
    assert any("listed more than once" in p for p in problems)


def test_requires_startup_to_be_an_installed_program(builder):
    builder.package("tool", {"1.0.0": program("tool", startup="bin/other.lua")})

    assert any("startup `bin/other.lua`" in p for p in problems_of(builder))


def test_checks_dependencies(builder):
    builder.package("base", {"1.0.0": program("base")})
    builder.package("tool", {"1.0.0": program("tool", dependencies={"missing": "*", "base": "^2.0.0", "tool": "*"})})
    builder.package("other", {"1.0.0": program("other", dependencies={"base": ">=x"})})

    problems = problems_of(builder)
    assert any("dependency `missing` is not in the registry" in p for p in problems)
    assert any("no version of `base` satisfies `^2.0.0`" in p for p in problems)
    assert any("cannot depend on itself" in p for p in problems)
    assert any("dependency `base`" in p and "not a valid range" in p for p in problems)


def test_checks_compat_ranges(builder):
    builder.package("tool", {"1.0.0": program("tool", compat={"mc": "one point twenty"})})

    assert any("compat `mc`" in p for p in problems_of(builder))


def test_rejects_program_clashes(builder):
    builder.package("one", {"1.0.0": {"kind": "files", "files": [file_entry("bin/tool.lua")]}})
    builder.package("two", {"1.0.0": {"kind": "files", "files": [file_entry("bin/tool.lua")]}})

    assert any("installed by more than one package: one, two" in p for p in problems_of(builder))


def test_requires_the_files_kind(builder):
    builder.package("tool", {"1.0.0": {"kind": "installer", "installer": {"command": "wget run https://example.com"}}})

    assert any("must use the `files` kind" in p for p in problems_of(builder))


def test_requires_versions_and_hosts(builder):
    builder.package("tool", {})
    builder.write("hosts.json", {"prefixes": []})

    problems = problems_of(builder)
    assert any("packages/tool: has no versions" in p for p in problems)
    assert any("hosts.json" in p for p in problems)


def external(builder, slug: str, versions: dict[str, dict], **meta) -> None:
    """
    Writes a package synced from the `fake` source.

    Args:
        builder: The registry builder.
        slug: The name after the source prefix.
        versions: Version manifests keyed by version.
        **meta: Metadata overriding the defaults.
    """
    base = {"name": f"fake/{slug}", "description": "Synced.", "author": "Tester", "origin": {"source": "fake", "id": slug, "url": "https://example.com"}}
    builder.write(f"external/fake/{slug}/package.json", {**base, **meta})
    for version, manifest in versions.items():
        builder.write(f"external/fake/{slug}/{version}.json", manifest)


def test_accepts_external_installers_and_module_files(builder):
    external(builder, "app", {"1.0.0": {"kind": "installer", "installer": {"command": "wget run https://example.com/i.lua"}}})
    external(builder, "lib", {"1.0.0": {"kind": "files", "files": [file_entry("lib/pixel.lua")]}})

    assert problems_of(builder) == []


def test_requires_external_origins_and_keeps_native_rules(builder):
    external(builder, "app", {"1.0.0": program("app")}, origin={"source": "other", "id": "1", "url": "https://example.com"})
    builder.package("native", {"1.0.0": {"kind": "files", "files": [file_entry("lib/pixel.lua")]}}, origin={"source": "fake", "id": "1", "url": "https://example.com"})

    problems = problems_of(builder)
    assert any("must have an `origin` with that source" in p for p in problems)
    assert any("only packages synced from an external source may have an `origin`" in p for p in problems)
    assert any("must be `lib/native.lua`" in p for p in problems)


def test_rejects_library_clashes_but_ignores_delisted_packages(builder):
    builder.package("pixel", {"1.0.0": {"kind": "files", "files": [file_entry("lib/pixel/init.lua")]}})
    external(builder, "one", {"1.0.0": {"kind": "files", "files": [file_entry("lib/pixel.lua")]}})
    external(builder, "two", {"1.0.0": program("tool")})
    builder.package("tool")
    external(builder, "gone", {"1.0.0": program("gone")}, delisted=True)
    builder.package("gone")

    problems = problems_of(builder)
    assert any("library pixel is installed by more than one package: fake/one, pixel" in p for p in problems)
    assert any("program tool is installed by more than one package: fake/two, tool" in p for p in problems)
    assert not any("program gone" in p for p in problems)


def test_requires_hashes_only_for_native_packages(builder):
    builder.package("tool", {"1.0.0": {"kind": "files", "files": [{"url": "https://raw.githubusercontent.com/x/tool.lua", "path": "bin/tool.lua"}]}})
    external(builder, "live", {"1.0.0": {"kind": "files", "files": [{"url": "https://raw.githubusercontent.com/x/live.lua", "path": "bin/live.lua"}]}})

    problems = problems_of(builder)
    assert any("packages/tool/1.0.0.json" in p and "`bin/tool.lua` must have a `sha256`" in p for p in problems)
    assert not any("live" in p for p in problems)
