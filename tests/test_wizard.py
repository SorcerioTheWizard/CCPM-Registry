"""
CCPM Package Wizard Tests

Tests for publishing packages by answering the wizard's questions.
"""

# MARK: Imports
import hashlib
import json

import httpx
import pytest

from ccpm_registry.manifest import parse_gist, parse_gist_file, parse_paste, parse_repo
from ccpm_registry.verify import NotTextError, hash_text_file
from ccpm_registry.wizard import SOURCE_GIST, SOURCE_GITHUB, SOURCE_PASTEBIN, SOURCE_RAW, Wizard, WizardCancelled

from conftest import program

# MARK: Constants
SHA = "0123456789abcdef0123456789abcdef01234567"
GIST = "6e363dd7186148677fcfd17d169e631b"
REJECTED = "rejected"
SERVED = {
    f"https://raw.githubusercontent.com/o/tool/{SHA}/src/tool.lua": b"print('tool')\n",
    f"https://raw.githubusercontent.com/o/tool/{SHA}/README.md": b"# Tool\n",
    f"https://gist.githubusercontent.com/u/{GIST}/raw/rev1/Pixel.lua": b"return {}\n",
    "https://pastebin.com/raw/AbC123": b"print('paste')\n",
    "https://raw.githubusercontent.com/o/other/main/other.lua": b"print('other')\n",
    f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/Old.lua": b"print('old')\n",
    f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/NOTES.md": b"# Notes\n",
}


# MARK: Classes
class ScriptedPrompter:
    """
    Answers the wizard's questions from a script, checking each answer like the real prompts do.
    """

    # MARK: Initializer
    def __init__(self, script: list[tuple]) -> None:
        """
        Creates the prompter.

        Args:
            script: Steps of `(part of the question, answer)`, or `(part, answer, REJECTED)` for answers the check must refuse.
        """
        self.script = list(script)

    # MARK: Private Functions
    def _next(self, message: str) -> tuple:
        """
        Takes the next scripted step, checking it answers this question.

        Args:
            message: The question asked.

        Returns:
            The step.
        """
        assert self.script, f"unexpected question: {message}"
        step = self.script.pop(0)
        assert step[0] in message, f"expected a question about {step[0]!r}, got {message!r}"
        return step

    # MARK: Functions
    def text(self, message, default="", check=None):
        while True:
            step = self._next(message)
            answer = default if step[1] is None else step[1]
            problem = check(answer) if check else None
            if len(step) == 3:
                assert problem, f"{answer!r} should have been rejected for {message!r}"
                continue
            assert problem is None, f"{answer!r} was rejected for {message!r}: {problem}"
            return answer

    def select(self, message, choices):
        answer = self._next(message)[1]
        assert answer in choices
        return answer

    def checkbox(self, message, choices):
        answer = self._next(message)[1]
        assert set(answer) <= set(choices)
        return answer

    def confirm(self, message, default=True):
        answer = self._next(message)[1]
        return default if answer is None else answer

    def finished(self) -> bool:
        """
        Checks every scripted step was used.

        Returns:
            If the script is empty.
        """
        return not self.script


# MARK: Functions
def fake_hosts(request: httpx.Request) -> httpx.Response:
    """
    Answers like GitHub, gists, and Pastebin for the test files.

    Args:
        request: The request.

    Returns:
        The response.
    """
    url = str(request.url)
    if url == "https://api.github.com/repos/o/tool/commits/v1":
        return httpx.Response(200, json={"sha": SHA})
    if url == f"https://api.github.com/repos/o/tool/git/trees/{SHA}?recursive=1":
        return httpx.Response(200, json={"tree": [{"path": "src/tool.lua", "type": "blob"}, {"path": "README.md", "type": "blob"}]})
    if url == f"https://api.github.com/gists/{GIST}":
        return httpx.Response(200, json={"files": {"Pixel.lua": {"raw_url": f"https://gist.githubusercontent.com/u/{GIST}/raw/rev1/Pixel.lua"}}})
    if url == "https://pastebin.com/raw/Page1":
        return httpx.Response(200, content=b"<html></html>", headers={"Content-Type": "text/html; charset=utf-8"})
    if url in SERVED:
        return httpx.Response(200, content=SERVED[url], headers={"Content-Type": "text/plain"})

    return httpx.Response(404)


def run(builder, script: list[tuple]) -> tuple[list, list[str]]:
    """
    Runs the wizard with scripted answers.

    Args:
        builder: The registry builder.
        script: The scripted steps.

    Returns:
        The files written and the messages printed.
    """
    prompter = ScriptedPrompter(script)
    messages: list[str] = []
    written = Wizard(builder.root, prompter, httpx.Client(transport=httpx.MockTransport(fake_hosts)), messages.append).run()
    assert prompter.finished(), f"unused steps: {prompter.script}"

    return written, messages


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


# MARK: Tests
def test_publishes_a_new_package_from_github(builder):
    builder.package("base")
    written, messages = run(
        builder,
        [
            ("Package name", "Bad Name!", REJECTED),
            ("Package name", "tool"),
            ("Describe it", "x" * 201, REJECTED),
            ("Describe it", "A handy tool."),
            ("Author", "Tester"),
            ("License", "MIT"),
            ("Source code link", "http://insecure", REJECTED),
            ("Source code link", "https://github.com/o/tool"),
            ("Search tags", "Utility, Turtle"),
            ("Version", None),
            ("Where are the files", SOURCE_GITHUB),
            ("Repository", "https://github.com/o/tool"),
            ("Branch, tag, or commit", "v1"),
            ("Which files", ["src/tool.lua"]),
            ("library", False),
            ("Install `src/tool.lua`", "startup.lua", REJECTED),
            ("Install `src/tool.lua`", None),
            ("other packages", True),
            ("Name of a package", "missing", REJECTED),
            ("Name of a package", "base"),
            ("Versions of `base`", None),
            ("Name of a package", ""),
            ("ComputerCraft versions", ">=1.100"),
            ("Minecraft versions", "one twenty", REJECTED),
            ("Minecraft versions", ""),
            ("boots", "bin/tool.lua"),
            ("Write these files", True),
        ],
    )

    assert [path.name for path in written] == ["package.json", "1.0.0.json"]
    assert read(builder, "packages/tool/package.json") == {
        "$schema": "../../schemas/package.schema.json",
        "name": "tool",
        "description": "A handy tool.",
        "author": "Tester",
        "license": "MIT",
        "repository": "https://github.com/o/tool",
        "tags": ["utility", "turtle"],
    }
    manifest = read(builder, "packages/tool/1.0.0.json")
    assert manifest["files"] == [{"url": f"https://raw.githubusercontent.com/o/tool/{SHA}/src/tool.lua", "path": "bin/tool.lua", "sha256": hashlib.sha256(b"print('tool')\n").hexdigest()}]
    assert manifest["dependencies"] == {"base": "^1.0.0"}
    assert manifest["compat"] == {"cc": ">=1.100"}
    assert manifest["startup"] == "bin/tool.lua"
    assert any("git checkout -b add-tool-1.0.0" in message for message in messages)


def test_publishes_a_library_from_a_gist(builder):
    run(
        builder,
        [
            ("Package name", "pixel"),
            ("Describe it", "Draws pixels."),
            ("Author", "Tester"),
            ("License", ""),
            ("Source code link", ""),
            ("Search tags", ""),
            ("Version", None),
            ("Where are the files", SOURCE_GIST),
            ("Gist link", f"https://gist.github.com/u/{GIST}"),
            ("library", True),
            ("Install `Pixel.lua`", None),
            ("other packages", False),
            ("ComputerCraft versions", ""),
            ("Minecraft versions", ""),
            ("Write these files", True),
        ],
    )

    assert read(builder, "packages/pixel/1.0.0.json")["files"][0]["path"] == "lib/pixel.lua"
    assert set(read(builder, "packages/pixel/package.json")) == {"$schema", "name", "description", "author"}


def test_publishes_a_new_version_and_retries_bad_files(builder):
    builder.package("tool", {"1.2.0": program("tool")})
    written, messages = run(
        builder,
        [
            ("Package name", "tool"),
            ("already exists", True),
            ("Version", "1.2.0", REJECTED),
            ("Version", None),
            ("Where are the files", SOURCE_PASTEBIN),
            ("Paste codes", "https://pastebin.com/Page1"),
            ("library", False),
            ("Install `tool`", None),
            ("Try again", True),
            ("Where are the files", SOURCE_PASTEBIN),
            ("Paste codes", "AbC123"),
            ("library", False),
            ("Install `tool`", None),
            ("other packages", False),
            ("ComputerCraft versions", ""),
            ("Minecraft versions", ""),
            ("boots", "No"),
            ("Write these files", True),
        ],
    )

    # Write only the new version
    assert [path.name for path in written] == ["1.2.1.json"]
    assert read(builder, "packages/tool/1.2.1.json")["files"][0]["url"] == "https://pastebin.com/raw/AbC123"
    assert any("is a web page, not a file" in message for message in messages)


def test_accepts_only_allowed_hosts_for_raw_urls(builder):
    run(
        builder,
        [
            ("Package name", "other"),
            ("Describe it", "Another tool."),
            ("Author", "Tester"),
            ("License", ""),
            ("Source code link", ""),
            ("Search tags", ""),
            ("Version", None),
            ("Where are the files", SOURCE_RAW),
            ("File URLs", "https://example.com/other.lua", REJECTED),
            ("File URLs", "https://raw.githubusercontent.com/o/other/main/other.lua"),
            ("library", False),
            ("Install `other.lua`", None),
            ("other packages", False),
            ("ComputerCraft versions", ""),
            ("Minecraft versions", ""),
            ("boots", "No"),
            ("Write these files", True),
        ],
    )

    assert read(builder, "packages/other/1.0.0.json")["files"][0]["path"] == "bin/other.lua"


def test_writes_nothing_when_stopped(builder):
    with pytest.raises(WizardCancelled):
        run(
            builder,
            [
                ("Package name", "tool"),
                ("Describe it", "A tool."),
                ("Author", "Tester"),
                ("License", ""),
                ("Source code link", ""),
                ("Search tags", ""),
                ("Version", None),
                ("Where are the files", SOURCE_PASTEBIN),
                ("Paste codes", "AbC123"),
                ("library", False),
                ("Install `tool`", None),
                ("other packages", False),
                ("ComputerCraft versions", ""),
                ("Minecraft versions", ""),
                ("boots", "No"),
                ("Write these files", False),
            ],
        )

    assert not (builder.root / "packages/tool").exists()


def test_uses_pinned_gist_links_as_given(builder):
    run(
        builder,
        [
            ("Package name", "old"),
            ("Describe it", "An old version."),
            ("Author", "Tester"),
            ("License", ""),
            ("Source code link", ""),
            ("Search tags", ""),
            ("Version", None),
            ("Where are the files", SOURCE_GIST),
            ("Gist link", f"https://gist.github.com/u/{GIST}/raw/{SHA}/Old.lua"),
            ("library", False),
            ("Install `Old.lua`", None),
            ("other packages", False),
            ("ComputerCraft versions", ""),
            ("Minecraft versions", ""),
            ("boots", "No"),
            ("Write these files", True),
        ],
    )

    entry = read(builder, "packages/old/1.0.0.json")["files"][0]
    assert entry["url"] == f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/Old.lua"
    assert entry["path"] == "bin/Old.lua"


def test_asks_before_including_files_that_are_not_lua(builder):
    written, messages = run(
        builder,
        [
            ("Package name", "notes"),
            ("Describe it", "Some notes."),
            ("Author", "Tester"),
            ("License", ""),
            ("Source code link", ""),
            ("Search tags", ""),
            ("Version", None),
            ("Where are the files", SOURCE_GIST),
            ("Gist link", f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/NOTES.md"),
            ("does not look like a Lua file", False),
            ("Try again", True),
            ("Where are the files", SOURCE_GIST),
            ("Gist link", f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/NOTES.md"),
            ("does not look like a Lua file", True),
            ("library", False),
            ("Install `NOTES.md`", None),
            ("other packages", False),
            ("ComputerCraft versions", ""),
            ("Minecraft versions", ""),
            ("boots", "No"),
            ("Write these files", True),
        ],
    )

    assert any("none of the files were picked" in message for message in messages)
    assert read(builder, "packages/notes/1.0.0.json")["files"][0]["path"] == "bin/NOTES.lua"


def test_reads_links_and_codes():
    assert parse_repo("https://github.com/o/tool.git") == "o/tool"
    assert parse_repo("o/tool") == "o/tool"
    assert parse_repo("not a repo") is None
    assert parse_gist(f"https://gist.github.com/u/{GIST}") == GIST
    assert parse_gist(GIST) == GIST
    assert parse_gist_file(f"https://gist.github.com/u/{GIST}/raw/{SHA}/Old.lua") == ("Old.lua", f"https://gist.githubusercontent.com/u/{GIST}/raw/{SHA}/Old.lua")
    assert parse_gist_file(f"https://gist.github.com/u/{GIST}") is None
    assert parse_paste("https://pastebin.com/raw/AbC123") == "AbC123"
    assert parse_paste("bad code!") is None


@pytest.mark.parametrize(
    ("content", "headers", "problem"),
    [
        (b"<html></html>", {"Content-Type": "text/html"}, "web page"),
        (b"", {}, "empty"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", {}, "not a text file"),
        (b"\xff\xfe", {}, "not a text file"),
    ],
)
def test_rejects_files_that_are_not_text(content, headers, problem):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content, headers=headers)))

    with pytest.raises(NotTextError, match=problem):
        hash_text_file(client, "https://example.com/file")
