"""
CCPM Registry CLI Tests

Tests for the command line entry point.
"""

# MARK: Imports
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
