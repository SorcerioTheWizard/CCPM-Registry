"""
CCPM Source Base

The shape of a project from an external catalog, shared by every source adapter.
"""

# MARK: Imports
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

# MARK: Constants
SLUG_MAX_LENGTH = 48
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TAG_MAX_LENGTH = 32
TAG_LIMIT = 10


# MARK: Classes
@dataclass
class ExternalProject:
    """
    A project as an external catalog describes it.
    """

    # MARK: Properties
    id: str
    title: str
    description: str
    author: str
    tags: list[str]
    repository: str | None
    url: str
    command: str | None
    target: str | None
    library: bool
    updated: datetime


class Source(Protocol):
    """
    An external catalog that can be mirrored into the registry.
    """

    # MARK: Properties
    name: str

    # MARK: Functions
    def fetch(self, client: httpx.Client) -> list[ExternalProject]:
        """
        Downloads every project the catalog currently lists.

        Args:
            client: The HTTP client.

        Returns:
            The projects.

        Raises:
            httpx.HTTPError: If the catalog cannot be downloaded.
            ValueError: If the catalog's response cannot be read.
        """
        ...


# MARK: Functions
def slugify(text: str) -> str:
    """
    Turns a title into a package name.

    Args:
        text: The title, like `Pixelbox Lite!`.

    Returns:
        The name, like `pixelbox-lite`, or an empty string if nothing usable remains.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].rstrip("-")


def normalize_tags(values: list[str] | str | None) -> list[str]:
    """
    Turns a catalog's tags into registry tags, dropping any that cannot be used.

    Args:
        values: A list of tags or a comma separated string.

    Returns:
        Up to ten unique lowercase tags.
    """
    raw = values.split(",") if isinstance(values, str) else (values or [])
    tags = []
    for value in raw:
        tag = re.sub(r"[^a-z0-9-]+", "-", str(value).strip().lower()).strip("-")[:TAG_MAX_LENGTH]
        if TAG_PATTERN.match(tag) and tag not in tags:
            tags.append(tag)

    return tags[:TAG_LIMIT]
