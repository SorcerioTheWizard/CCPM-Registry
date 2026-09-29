"""
CCPM Semver

Parses semantic versions and the version range syntax shared with the CCPM client.
"""

# MARK: Imports
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

# MARK: Constants
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
PARTIAL_PATTERN = re.compile(r"^(0|[1-9]\d*)(?:\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?)?)?$")
COMPARATOR_PATTERN = re.compile(r"^(\^|~|>=|<=|>|<|=)?(.+)$")
ANY_RANGES = ("", "*")


# MARK: Classes
@total_ordering
@dataclass(frozen=True)
class Version:
    """
    A semantic version without build metadata.
    """

    # MARK: Properties
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = field(default=())

    # MARK: Dunders
    def __str__(self) -> str:
        """
        Formats the version as text.

        Returns:
            The version, like `1.2.3-beta.1`.
        """
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)

        return text

    def __lt__(self, other: object) -> bool:
        """
        Compares two versions by semver precedence.

        Args:
            other: The version to compare against.

        Returns:
            If this version has lower precedence.
        """
        if not isinstance(other, Version):
            return NotImplemented

        return self._key() < other._key()

    # MARK: Private Functions
    def _key(self) -> tuple:
        """
        Builds a sort key following semver precedence.

        Returns:
            A tuple that sorts in precedence order.
        """
        # Releases sort above their prereleases
        if not self.prerelease:
            return (self.major, self.minor, self.patch, (1,))

        # Numeric identifiers sort below alphanumeric ones
        identifiers = tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in self.prerelease)
        return (self.major, self.minor, self.patch, (0, identifiers))

    # MARK: Functions
    @property
    def core(self) -> tuple[int, int, int]:
        """
        The version without its prerelease.
        """
        return (self.major, self.minor, self.patch)


@dataclass(frozen=True)
class Comparator:
    """
    A single version comparison like `>=1.2.0`.
    """

    # MARK: Properties
    op: str
    version: Version

    # MARK: Functions
    def test(self, version: Version) -> bool:
        """
        Checks a version against this comparison.

        Args:
            version: The version to check.

        Returns:
            If the version passes the comparison.
        """
        if self.op == "=":
            return version == self.version
        if self.op == "<":
            return version < self.version
        if self.op == "<=":
            return version <= self.version
        if self.op == ">":
            return version > self.version

        return version >= self.version


@dataclass(frozen=True)
class Range:
    """
    A version range made of alternatives joined by `||`, each a set of comparators that must all pass.
    """

    # MARK: Properties
    alternatives: tuple[tuple[Comparator, ...], ...]

    # MARK: Functions
    def test(self, version: Version) -> bool:
        """
        Checks if a version satisfies the range.

        Prereleases only satisfy an alternative that names a prerelease of the same `major.minor.patch`.

        Args:
            version: The version to check.

        Returns:
            If the version satisfies the range.
        """
        for comparators in self.alternatives:
            # Check every comparison
            if not all(comparator.test(version) for comparator in comparators):
                continue

            # Only allow prereleases the range opted into
            if version.prerelease and not any(c.version.prerelease and c.version.core == version.core for c in comparators):
                continue

            return True

        return False

    def best(self, versions: list[Version]) -> Version | None:
        """
        Picks the highest version that satisfies the range.

        Args:
            versions: The candidate versions.

        Returns:
            The highest satisfying version, or `None` if none satisfy it.
        """
        matching = [version for version in versions if self.test(version)]
        return max(matching) if matching else None


# MARK: Functions
def parse_version(text: str) -> Version:
    """
    Parses a full semantic version like `1.2.3` or `1.2.3-beta.1`.

    Args:
        text: The version text.

    Returns:
        The parsed version.

    Raises:
        ValueError: If the text is not a full semantic version.
    """
    match = VERSION_PATTERN.match(text)
    if not match:
        raise ValueError(f"`{text}` is not a semantic version like `1.2.3`")

    major, minor, patch, prerelease = match.groups()
    return Version(int(major), int(minor), int(patch), tuple(prerelease.split(".")) if prerelease else ())


def _parse_partial(text: str) -> tuple[list[int], tuple[str, ...]]:
    """
    Parses a possibly partial version like `1`, `1.20`, or `1.20.1`.

    Args:
        text: The version text.

    Returns:
        The numeric parts that were given and the prerelease identifiers.

    Raises:
        ValueError: If the text is not a version.
    """
    match = PARTIAL_PATTERN.match(text)
    if not match:
        raise ValueError(f"`{text}` is not a version")

    *numbers, prerelease = match.groups()
    return [int(part) for part in numbers if part is not None], tuple(prerelease.split(".")) if prerelease else ()


def _bump(parts: list[int]) -> Version:
    """
    Gets the lowest version above every version starting with the given parts.

    Args:
        parts: One to three numeric parts, like `[1, 20]`.

    Returns:
        The next version, like `1.21.0` for `[1, 20]`.
    """
    padded = parts + [0] * (3 - len(parts))
    padded[len(parts) - 1] += 1

    return Version(*padded)


def _parse_comparator(text: str) -> list[Comparator]:
    """
    Expands one range token into the comparisons it stands for.

    Args:
        text: The token, like `^1.2.0`, `>=1.19`, or `1.20`.

    Returns:
        The equivalent comparisons.

    Raises:
        ValueError: If the token is not valid.
    """
    # Split the operator from the version
    op, rest = COMPARATOR_PATTERN.match(text).groups()
    parts, prerelease = _parse_partial(rest)
    lower = Version(*(parts + [0] * (3 - len(parts))), prerelease)
    is_full = len(parts) == 3

    # Match an exact version or every version starting with a partial one
    if op in (None, "="):
        if is_full:
            return [Comparator("=", lower)]
        return [Comparator(">=", lower), Comparator("<", _bump(parts))]

    # Allow changes that do not modify the leftmost non-zero part
    if op == "^":
        significant = next((index for index, part in enumerate(parts) if part != 0), len(parts) - 1)
        return [Comparator(">=", lower), Comparator("<", _bump(parts[: significant + 1]))]

    # Allow patch changes, or minor changes when only a major is given
    if op == "~":
        return [Comparator(">=", lower), Comparator("<", _bump(parts[:2]))]

    # Treat partial bounds as covering every version starting with them
    if op == ">" and not is_full:
        return [Comparator(">=", _bump(parts))]
    if op == "<=" and not is_full:
        return [Comparator("<", _bump(parts))]

    return [Comparator(op, lower)]


def parse_range(text: str) -> Range:
    """
    Parses a version range.

    The syntax is a subset of npm's: `*`, `1.2.3`, `1.20`, `^1.2.0`, `~1.2.0`, `>=1.19 <1.21`, and alternatives joined by `||`.

    Args:
        text: The range text.

    Returns:
        The parsed range.

    Raises:
        ValueError: If the text is not a valid range.
    """
    alternatives = []
    for alternative in text.split("||"):
        # Treat an empty or wildcard alternative as matching everything
        tokens = alternative.split()
        if len(tokens) == 0 or (len(tokens) == 1 and tokens[0] in ANY_RANGES):
            alternatives.append(())
            continue

        # Expand every token
        comparators = []
        for token in tokens:
            try:
                comparators.extend(_parse_comparator(token))
            except ValueError as error:
                raise ValueError(f"`{text}` is not a valid range: {error}") from None
        alternatives.append(tuple(comparators))

    return Range(tuple(alternatives))

