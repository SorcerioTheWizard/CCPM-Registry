"""
CCPM File Verification Tests

Tests for downloading package files and checking their hashes.
"""

# MARK: Imports
import hashlib

import httpx

from ccpm_registry.verify import verify_files

from conftest import file_entry

# MARK: Constants
CONTENT = b"print('hello')\n"
CONTENT_HASH = hashlib.sha256(CONTENT).hexdigest()


# MARK: Functions
def fake_client() -> httpx.Client:
    """
    Creates a client serving `CONTENT` at `/good` and 404 elsewhere.

    Returns:
        The fake client.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/good"):
            return httpx.Response(200, content=CONTENT)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


# MARK: Tests
def test_accepts_matching_files():
    manifests = {"packages/tool/1.0.0.json": {"files": [file_entry("bin/tool.lua", url="https://raw.githubusercontent.com/good", sha256=CONTENT_HASH)]}}

    assert verify_files(fake_client(), manifests) == []


def test_reports_mismatches_and_failed_downloads():
    manifests = {
        "packages/tool/1.0.0.json": {
            "files": [
                file_entry("bin/tool.lua", url="https://raw.githubusercontent.com/good"),
                file_entry("bin/other.lua", url="https://raw.githubusercontent.com/missing", sha256=CONTENT_HASH),
            ]
        }
    }

    problems = [str(problem) for problem in verify_files(fake_client(), manifests)]
    assert len(problems) == 2
    assert any(f"has SHA-256 `{CONTENT_HASH}`" in p for p in problems)
    assert any("could not be downloaded" in p for p in problems)
