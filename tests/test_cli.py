"""
CCPM Registry CLI Tests

Tests for the command line entry point.
"""

# MARK: Imports
import httpx

from ccpm_registry.cli import main

from conftest import program


# MARK: Tests
def test_validate_reports_success_and_failure(builder, capsys):
    builder.package("tool")
    assert main(["--root", str(builder.root), "validate"]) == 0
    assert "Registry is valid." in capsys.readouterr().out

    builder.package("broken", {"1.0.0": program("broken", dependencies={"missing": "*"})})
    assert main(["--root", str(builder.root), "validate"]) == 1
    assert "1 problem(s) found." in capsys.readouterr().err


def test_build_refuses_an_invalid_registry(builder, tmp_path):
    builder.package("broken", {"1.0.0": program("broken", dependencies={"missing": "*"})})
    out = tmp_path / "out"

    assert main(["--root", str(builder.root), "build", "--out", str(out)]) == 1
    assert not out.exists()


def test_build_writes_the_registry(builder, tmp_path):
    builder.package("tool")
    out = tmp_path / "out"

    assert main(["--root", str(builder.root), "build", "--out", str(out)]) == 0
    assert (out / "index.json").exists()
    assert (out / "packages/tool/1.0.0.json").exists()


def test_verify_skips_synced_packages(builder, monkeypatch, capsys):
    builder.package("tool", {"1.0.0": program("tool", files=[{"url": "https://raw.githubusercontent.com/x/tool.lua", "path": "bin/tool.lua", "sha256": "0" * 64}])})
    builder.write("external/fake/live/package.json", {"name": "fake/live", "description": "Live.", "author": "Tester", "origin": {"source": "fake", "id": "1", "url": "https://example.com"}})
    builder.write("external/fake/live/1.0.0.json", {"kind": "files", "files": [{"url": "https://raw.githubusercontent.com/x/live.lua", "path": "bin/live.lua"}]})
    requested = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"")

    monkeypatch.setattr("ccpm_registry.cli.create_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    main(["--root", str(builder.root), "verify"])

    assert requested == ["https://raw.githubusercontent.com/x/tool.lua"]
