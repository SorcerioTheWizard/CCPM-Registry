"""
CCPM Registry Build Tests

Tests for the published form of the registry.
"""

# MARK: Imports
import json

from ccpm_registry.build import build_dist
from ccpm_registry.registry import load_registry

from conftest import program


# MARK: Tests
def test_builds_the_index_and_manifests(builder, tmp_path):
    builder.package(
        "tool",
        {"1.0.0": {"$schema": "../../schemas/version.schema.json", **program("tool")}, "1.2.0": program("tool"), "2.0.0-beta": program("tool")},
        tags=["util"],
    )
    registry, _ = load_registry(builder.root)
    out = tmp_path / "out"
    (out / "stale.json").parent.mkdir(parents=True)
    (out / "stale.json").write_text("{}")

    build_dist(registry, out)

    # Check the index
    index = json.loads((out / "index.json").read_text())
    assert index["format"] == 1
    assert index["hosts"] == registry.prefixes
    assert index["packages"]["tool"] == {
        "description": "The tool package.",
        "author": "Tester",
        "tags": ["util"],
        "latest": "1.2.0",
        "versions": ["2.0.0-beta", "1.2.0", "1.0.0"],
    }

    # Check the manifests are copied without the schema reference
    manifest = json.loads((out / "packages/tool/1.0.0.json").read_text())
    assert manifest == program("tool")

    # Check old output is removed
    assert not (out / "stale.json").exists()
