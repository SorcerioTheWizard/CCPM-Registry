"""
CCPM Semver Tests

Tests for version parsing, ordering, and ranges.
"""

# MARK: Imports
import pytest

from ccpm_registry.semver import Version, parse_range, parse_version


# MARK: Tests
def test_parses_versions():
    assert parse_version("1.2.3") == Version(1, 2, 3)
    assert parse_version("1.0.0-beta.2") == Version(1, 0, 0, ("beta", "2"))
    assert str(parse_version("1.0.0-rc.1")) == "1.0.0-rc.1"


@pytest.mark.parametrize("text", ["1.2", "01.2.3", "1.2.3+build", "v1.2.3", "1.2.3-", ""])
def test_rejects_invalid_versions(text):
    with pytest.raises(ValueError):
        parse_version(text)


def test_orders_by_semver_precedence():
    # The ordering example from the semver specification
    ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.10.0", "2.0.0"]
    versions = [parse_version(text) for text in ordered]
    assert sorted(reversed(versions)) == versions


@pytest.mark.parametrize(
    ("range_text", "matching", "failing"),
    [
        ("*", ["0.0.1", "9.9.9"], ["1.0.0-beta"]),
        ("", ["1.0.0"], []),
        ("1.2.3", ["1.2.3"], ["1.2.4"]),
        ("=1.2.3", ["1.2.3"], ["1.2.2"]),
        ("1.20", ["1.20.0", "1.20.6"], ["1.19.9", "1.21.0"]),
        ("1", ["1.0.0", "1.99.0"], ["2.0.0"]),
        ("^1.2.3", ["1.2.3", "1.9.0"], ["1.2.2", "2.0.0"]),
        ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4"]),
        ("^1.2", ["1.2.0", "1.9.9"], ["2.0.0"]),
        ("^0.0", ["0.0.5"], ["0.1.0"]),
        ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0"]),
        ("~1", ["1.5.0"], ["2.0.0"]),
        (">=1.19 <1.21", ["1.19.0", "1.20.4"], ["1.18.2", "1.21.0"]),
        (">1.2", ["1.3.0"], ["1.2.9"]),
        ("<=1.2", ["1.2.9"], ["1.3.0"]),
        (">1.2.3", ["1.2.4"], ["1.2.3"]),
        ("<1.2.3", ["1.2.2"], ["1.2.3"]),
        ("1.2.3 || >=2.0.0", ["1.2.3", "2.5.0"], ["1.5.0"]),
        (">=1.0.0-beta.2 <2.0.0", ["1.0.0-beta.3", "1.0.0"], ["1.0.0-beta.1", "1.1.0-beta.1"]),
    ],
)
def test_ranges(range_text, matching, failing):
    version_range = parse_range(range_text)
    for text in matching:
        assert version_range.test(parse_version(text)), f"{text} should satisfy {range_text!r}"
    for text in failing:
        assert not version_range.test(parse_version(text)), f"{text} should not satisfy {range_text!r}"


@pytest.mark.parametrize("text", ["^", ">=x", "1.2.3.4", "^1.2.3 ~", "1.2.3 ||| 2"])
def test_rejects_invalid_ranges(text):
    with pytest.raises(ValueError):
        parse_range(text)


def test_picks_the_best_version():
    versions = [parse_version(text) for text in ["1.0.0", "1.4.0", "2.0.0", "1.5.0-beta"]]
    assert parse_range("^1.0.0").best(versions) == parse_version("1.4.0")
    assert parse_range("^3.0.0").best(versions) is None
