"""
CCPM Source Sync Tests

Tests for mirroring external catalogs and the Pinestore adapter.
"""

# MARK: Imports
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from ccpm_registry.registry import load_registry
from ccpm_registry.semver import Version
from ccpm_registry.sources.base import ExternalProject, normalize_tags, slugify
from ccpm_registry.sources.pinestore import PinestoreSource
from ccpm_registry.manifest import normalize_url
from ccpm_registry.sync import date_version, parse_download, sync_source
from ccpm_registry.validate import validate_registry

from conftest import program

# MARK: Constants
UPDATED = datetime(2026, 9, 1, 12, 0, 5, tzinfo=UTC)
NOW = datetime(2026, 9, 29, 8, 30, 0, tzinfo=UTC)
FILES = {"https://raw.githubusercontent.com/a/radar/main/radar.lua": b"print('radar')\n"}


# MARK: Classes
class FakeSource:
    """
    A source that lists whatever projects a test gives it.
    """

    # MARK: Properties
    name = "fake"

    # MARK: Initializer
    def __init__(self, projects: list[ExternalProject]) -> None:
        """
        Creates the source.

        Args:
            projects: The projects to list.
        """
        self.projects = projects

    # MARK: Functions
    def fetch(self, client: httpx.Client) -> list[ExternalProject]:
        """
        Lists the projects.

        Args:
            client: The HTTP client, unused.

        Returns:
            The projects.
        """
        return self.projects


# MARK: Functions
def project(project_id: str = "1", title: str = "Radar", command: str | None = "wget https://raw.githubusercontent.com/a/radar/main/radar.lua radar.lua", **extra) -> ExternalProject:
    """
    Builds an external project.

    Args:
        project_id: The project ID.
        title: The title.
        command: The install command.
        **extra: Fields to override.

    Returns:
        The project.
    """
    fields = {
        "id": project_id,
        "title": title,
        "description": f"The {title} project.",
        "author": "Tester",
        "tags": ["utility"],
        "repository": None,
        "url": f"https://example.com/projects/{project_id}",
        "command": command,
        "target": None,
        "library": False,
        "updated": UPDATED,
    }
    fields.update(extra)

    return ExternalProject(**fields)


def client(files: dict[str, bytes] | None = None) -> httpx.Client:
    """
    Creates a client serving file contents by URL.

    Args:
        files: Contents keyed by URL, defaulting to `FILES`.

    Returns:
        The client.
    """
    served = FILES if files is None else files

    def handle(request: httpx.Request) -> httpx.Response:
        body = served.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


def sync(builder, projects: list[ExternalProject], files: dict[str, bytes] | None = None, now: datetime = NOW):
    """
    Syncs the fake source into a built registry.

    Args:
        builder: The registry builder.
        projects: The projects the source lists.
        files: File contents keyed by URL.
        now: The time of the sync.

    Returns:
        The sync report.
    """
    return sync_source(builder.root, FakeSource(projects), client(files), now)


def read(builder, path: str) -> dict:
    """
    Reads a JSON file from the registry.

    Args:
        builder: The registry builder.
        path: The registry-relative path.

    Returns:
        The data.
    """
    return json.loads((builder.root / path).read_text(encoding="utf-8"))


def versions(builder, slug: str) -> list[str]:
    """
    Lists the published versions of a synced package.

    Args:
        builder: The registry builder.
        slug: The package name after the source prefix.

    Returns:
        The versions, sorted as text.
    """
    return sorted(path.stem for path in (builder.root / "external/fake" / slug).glob("*.json") if path.name != "package.json")


# MARK: Tests
@pytest.mark.parametrize(
    ("command", "url", "file"),
    [
        ("wget https://x.test/a.lua", "https://x.test/a.lua", None),
        ("wget https://x.test/a.lua b.lua", "https://x.test/a.lua", "b.lua"),
        ("pastebin get AbC123 game", "https://pastebin.com/raw/AbC123", "game"),
        ('wget "https://x.test/a b.lua" c.lua', "https://x.test/a b.lua", "c.lua"),
    ],
)
def test_parses_downloads(command, url, file):
    download = parse_download(command)
    assert download is not None
    assert (download.url, download.file) == (url, file)


@pytest.mark.parametrize("command", ["wget run https://x.test/i.lua", "pastebin run AbC123", "hello", "wget https://x.test/a.lua b c", "wget notaurl"])
def test_leaves_other_commands_to_installers(command):
    assert parse_download(command) is None


def test_normalizes_urls():
    assert normalize_url("https://gist.github.com/u/abc/raw/rev/f.lua") == "https://gist.githubusercontent.com/u/abc/raw/rev/f.lua"
    assert normalize_url("https://github.com/o/r/raw/master/f.lua") == "https://raw.githubusercontent.com/o/r/master/f.lua"
    assert normalize_url("http://raw.github.com/o/r/main/f.lua") == "https://raw.githubusercontent.com/o/r/main/f.lua"
    assert normalize_url("https://github.com/o/r/releases/download/v1/f.lua") == "https://github.com/o/r/releases/download/v1/f.lua"


def test_dates_versions_in_utc():
    assert date_version(datetime(2026, 9, 29, 14, 30, 5, tzinfo=UTC)) == Version(2026, 929, 143005)
    assert date_version(datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)) < date_version(datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC))


def test_cleans_names_and_tags():
    assert slugify("Pixelbox Lite! (v2)") == "pixelbox-lite-v2"
    assert slugify("!!!") == ""
    assert normalize_tags("Utility, Turtle,,utility, Big Tag!") == ["utility", "turtle", "big-tag"]
    assert normalize_tags([""]) == []


def test_mirrors_a_new_download_as_tracked_files(builder):
    report = sync(builder, [project(repository="https://github.com/a/radar")])

    assert report.added == ["fake/radar"]
    meta = read(builder, "external/fake/radar/package.json")
    assert meta["origin"] == {"source": "fake", "id": "1", "url": "https://example.com/projects/1"}
    assert meta["repository"] == "https://github.com/a/radar"

    # Record where the file comes from without downloading it
    manifest = read(builder, "external/fake/radar/2026.901.120005.json")
    assert manifest["files"] == [{"url": next(iter(FILES)), "path": "bin/radar.lua"}]

    # Check the result passes the registry's rules
    registry, problems = load_registry(builder.root)
    assert problems + validate_registry(registry) == []


def test_changes_nothing_when_nothing_changed(builder):
    sync(builder, [project()])
    report = sync(builder, [project()])

    assert (report.added, report.updated) == ([], [])
    assert versions(builder, "radar") == ["2026.901.120005"]


def test_publishes_new_versions_when_the_install_changes(builder):
    sync(builder, [project()])

    # Date an update the source reported
    later = datetime(2026, 9, 10, 0, 0, 0, tzinfo=UTC)
    report = sync(builder, [project(command="wget https://raw.githubusercontent.com/a/radar/v2/radar.lua radar.lua", updated=later)])
    assert report.updated == ["fake/radar"]

    # Date a change the source did not report, like a new override, with the sync time
    builder.write("external/fake/overrides.json", {"1": {"compat": {"cc": ">=1.100"}}})
    sync(builder, [project(command="wget https://raw.githubusercontent.com/a/radar/v2/radar.lua radar.lua", updated=later)])
    assert versions(builder, "radar") == ["2026.901.120005", "2026.910.0", "2026.929.83000"]


def test_runs_installers_and_downloaded_installers(builder):
    sync(builder, [project("1", "Big App", "wget run https://example.com/install.lua /"), project("2", "Other", "wget https://raw.githubusercontent.com/a/radar/main/radar.lua installer.lua")])

    assert read(builder, "external/fake/big-app/2026.901.120005.json")["installer"] == {"command": "wget run https://example.com/install.lua /"}
    assert read(builder, "external/fake/other/2026.901.120005.json")["installer"] == {"command": "wget run https://raw.githubusercontent.com/a/radar/main/radar.lua"}


def test_places_libraries_and_startup_programs(builder):
    files = {"https://raw.githubusercontent.com/a/b/main/pixel.lua": b"return {}", "https://raw.githubusercontent.com/a/b/main/s.lua": b"os.sleep(1)"}
    sync(
        builder,
        [
            project("1", "Pixel", "wget https://raw.githubusercontent.com/a/b/main/pixel.lua", library=True),
            project("2", "Door Lock", "wget https://raw.githubusercontent.com/a/b/main/s.lua startup"),
        ],
        files=files,
    )

    assert read(builder, "external/fake/pixel/2026.901.120005.json")["files"][0]["path"] == "lib/pixel.lua"
    door = read(builder, "external/fake/door-lock/2026.901.120005.json")
    assert door["files"][0]["path"] == "bin/door-lock.lua"
    assert door["startup"] == "bin/door-lock.lua"


def test_avoids_clashing_names_and_keeps_names_stable(builder):
    builder.package("radar", {"1.0.0": program("radar")})
    sync(builder, [project("7", "Radar")])

    # Rename the program and the package away from the native package
    assert read(builder, "external/fake/radar/2026.901.120005.json")["files"][0]["path"] == "bin/radar-7.lua"

    # Keep the package name when the project is renamed
    report = sync(builder, [project("7", "Radar Deluxe")])
    assert report.added == []
    assert read(builder, "external/fake/radar/package.json")["description"] == "The Radar Deluxe project."

    # Name a second project with the same title apart
    sync(builder, [project("7", "Radar Deluxe"), project("8", "Radar", command="wget run https://example.com/x")])
    assert (builder.root / "external/fake/radar-8").is_dir()


def test_delists_and_relists_projects(builder):
    sync(builder, [project()])

    report = sync(builder, [])
    assert report.delisted == ["fake/radar"]
    assert read(builder, "external/fake/radar/package.json")["delisted"] is True
    assert versions(builder, "radar") == ["2026.901.120005"]

    report = sync(builder, [project()])
    assert report.updated == ["fake/radar"]
    assert "delisted" not in read(builder, "external/fake/radar/package.json")


def test_applies_overrides(builder):
    builder.write(
        "external/fake/overrides.json",
        {
            "1": {"note": "fix the command", "command": "wget https://raw.githubusercontent.com/a/radar/main/radar.lua radar.lua", "dependencies": {"base": "^1.0.0"}},
            "2": {"skip": True},
        },
    )
    builder.package("base")

    report = sync(builder, [project("1", command="broken"), project("2", "Skipped")])
    manifest = read(builder, "external/fake/radar/2026.901.120005.json")
    assert manifest["kind"] == "files"
    assert manifest["dependencies"] == {"base": "^1.0.0"}
    assert not (builder.root / "external/fake/skipped").exists()
    assert "fake project 2: skipped by an override" in report.skipped


def test_refuses_broken_overrides(builder):
    builder.write("external/fake/overrides.json", {"1": {"kind": "files"}})

    with pytest.raises(ValueError, match="overrides.json"):
        sync(builder, [project()])

    registry, _ = load_registry(builder.root)
    assert any("overrides.json" in str(problem) for problem in validate_registry(registry))


def test_downloads_nothing_but_the_catalog(builder):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected download of {request.url}")

    report = sync_source(builder.root, FakeSource([project()]), httpx.Client(transport=httpx.MockTransport(refuse)), NOW)
    assert report.added == ["fake/radar"]


def test_skips_github_pages_and_projects_without_commands(builder):
    report = sync(
        builder,
        [
            project("1", "Page", "wget https://github.com/a/page page.lua"),
            project("2", "Release", "wget https://github.com/a/r/releases/download/v1/r.lua r.lua"),
            project("3", "Nothing", command=None),
            project("4", "Latest", "wget https://github.com/a/l/releases/latest/download/l.lua l.lua"),
            project("5", "Archive", "wget https://github.com/a/z/archive/refs/tags/v1.zip"),
        ],
    )

    assert report.added == ["fake/release", "fake/latest"]
    assert "fake/page: `https://github.com/a/page` is a GitHub page or archive, not a file download" in report.skipped
    assert any(reason.startswith("fake/archive:") for reason in report.skipped)
    assert "fake/nothing: no install command" in report.skipped


def test_delists_projects_that_stop_being_installable(builder):
    sync(builder, [project("1", "Radar"), project("2", "Other", command="wget run https://example.com/i.lua")])

    # Delist a project whose command broke and one skipped by an override
    builder.write("external/fake/overrides.json", {"2": {"skip": True}})
    report = sync(builder, [project("1", "Radar", command="wget https://github.com/a/radar"), project("2", "Other", command="wget run https://example.com/i.lua")])
    assert sorted(report.delisted) == ["fake/other", "fake/radar"]

    # List it again once fixed
    report = sync(builder, [project("1", "Radar")])
    assert report.updated == ["fake/radar"]
    assert "delisted" not in read(builder, "external/fake/radar/package.json")


def test_reads_pinestore_projects():
    payload = {
        "success": True,
        "projects": [
            {
                "id": 12,
                "name": "Pixel Box",
                "owner_name": "Dev",
                "description": "# Pixel Box\n\nA **fast** renderer.\n\nMore text.",
                "tags": ["library", "Graphics"],
                "repository": "http://insecure.example",
                "install_command": "",
                "download_url": "https://raw.githubusercontent.com/d/p/main/pb.lua",
                "target_file": "pb.lua",
                "date_updated": 1759148405000,
                "visible": True,
            },
            {"id": 13, "name": "Hidden", "visible": False},
        ],
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    projects = PinestoreSource().fetch(httpx.Client(transport=transport))

    assert len(projects) == 1
    pixel = projects[0]
    assert (pixel.id, pixel.title, pixel.author) == ("12", "Pixel Box", "Dev")
    assert pixel.description == "Pixel Box"
    assert pixel.command == "wget https://raw.githubusercontent.com/d/p/main/pb.lua pb.lua"
    assert pixel.library is True
    assert pixel.tags == ["library", "graphics"]
    assert pixel.repository is None
    assert pixel.url == "https://pinestore.cc/projects/12/pixel-box"
    assert pixel.updated == datetime.fromtimestamp(1759148405, tz=UTC)


def test_rejects_unexpected_pinestore_responses():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"success": False, "error": "down"}))

    with pytest.raises(ValueError, match="down"):
        PinestoreSource().fetch(httpx.Client(transport=transport))
