"""
CCPM Manifest Builder Tests

Tests for writing version manifests from a GitHub repository.
"""

# MARK: Imports
import hashlib
import json

import httpx
import pytest

from ccpm_registry.cli import main
from ccpm_registry.manifest import ManifestRequest, build_manifest, write_manifest
from ccpm_registry.registry import load_registry
from ccpm_registry.validate import validate_registry

# MARK: Constants
SHA = "0123456789abcdef0123456789abcdef01234567"
FILES = {
    "src/bin/tool.lua": b"print('tool')\n",
    "src/lib/tool/init.lua": b"return {}\n",
    "src/lib/tool/util/math.lua": b"return 1\n",
    "README.md": b"# Tool\n",
}


# MARK: Functions
def fake_github(request: httpx.Request) -> httpx.Response:
    """
    Answers like the GitHub API and raw file host for a small repository.

    Args:
        request: The request.

    Returns:
        The response.
    """
    path = request.url.path
    if request.url.host == "api.github.com":
        if path == "/repos/example/tool/commits/v1.0.0":
            return httpx.Response(200, json={"sha": SHA})
        if path == f"/repos/example/tool/git/trees/{SHA}":
            return httpx.Response(200, json={"tree": [{"path": name, "type": "blob"} for name in FILES] + [{"path": "src", "type": "tree"}]})
    elif request.url.host == "raw.githubusercontent.com":
        name = path.removeprefix(f"/example/tool/{SHA}/")
        if name in FILES:
            return httpx.Response(200, content=FILES[name])

    return httpx.Response(404)


def client() -> httpx.Client:
    """
    Creates a client answered by `fake_github`.

    Returns:
        The client.
    """
    return httpx.Client(transport=httpx.MockTransport(fake_github))


# MARK: Tests
def test_builds_a_pinned_manifest():
    request = ManifestRequest(repo="example/tool", ref="v1.0.0", mappings=[("src/bin/tool.lua", "bin/tool.lua"), ("src/lib/tool", "lib/tool")], compat={"cc": ">=1.100"})
    manifest = build_manifest(client(), request)

    assert [entry["path"] for entry in manifest["files"]] == ["bin/tool.lua", "lib/tool/init.lua", "lib/tool/util/math.lua"]
    assert manifest["files"][0]["url"] == f"https://raw.githubusercontent.com/example/tool/{SHA}/src/bin/tool.lua"
    assert manifest["files"][0]["sha256"] == hashlib.sha256(FILES["src/bin/tool.lua"]).hexdigest()
    assert manifest["compat"] == {"cc": ">=1.100"}
    assert "dependencies" not in manifest


def test_rejects_unknown_sources():
    request = ManifestRequest(repo="example/tool", ref="v1.0.0", mappings=[("src/missing", "lib/tool")])

    with pytest.raises(ValueError, match="not a file or folder"):
        build_manifest(client(), request)


def test_never_replaces_a_version(builder):
    write_manifest(builder.root, "tool", "1.0.0", {"kind": "files"})

    with pytest.raises(FileExistsError, match="published versions never change"):
        write_manifest(builder.root, "tool", "1.0.0", {"kind": "files"})


def test_cli_writes_a_valid_version(builder, monkeypatch, capsys):
    monkeypatch.setattr("ccpm_registry.cli.create_client", client)
    builder.write("packages/tool/package.json", {"name": "tool", "description": "A tool.", "author": "Tester"})

    code = main(["--root", str(builder.root), "manifest", "tool", "1.0.0", "src/bin/tool.lua=bin/tool.lua", "src/lib/tool=lib/tool", "--github", "example/tool", "--ref", "v1.0.0", "--startup", "bin/tool.lua"])

    assert code == 0
    assert "with 3 file(s)" in capsys.readouterr().out
    written = json.loads((builder.root / "packages/tool/1.0.0.json").read_text())
    assert written["startup"] == "bin/tool.lua"

    # Check the result passes the registry's own rules
    registry, problems = load_registry(builder.root)
    assert problems + validate_registry(registry) == []


def test_cli_rejects_malformed_arguments(builder, capsys):
    with pytest.raises(SystemExit):
        main(["--root", str(builder.root), "manifest", "tool", "1.0.0", "no-equals-sign", "--github", "example/tool"])
    assert "must look like" in capsys.readouterr().err
