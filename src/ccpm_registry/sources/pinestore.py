"""
CCPM Pinestore Source

Reads every project from Pinestore's public API for `ccpm-registry sync pinestore`.
"""

# MARK: Imports
from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx

from ccpm_registry.sources.base import ExternalProject, normalize_tags, slugify

# MARK: Constants
API_URL = "https://pinestore.cc/api/projects"
PROJECT_URL = "https://pinestore.cc/projects/{id}/{slug}"
LIBRARY_TAG = "library"
DATE_KEYS = ("date_updated", "date_publish", "date_added")


# MARK: Classes
class PinestoreSource:
    """
    The Pinestore catalog at `pinestore.cc`.
    """

    # MARK: Properties
    name = "pinestore"

    # MARK: Private Functions
    @staticmethod
    def _describe(project: dict) -> str:
        """
        Picks a plain one paragraph description.

        Args:
            project: The Pinestore project.

        Returns:
            The short description, the first paragraph of the long one, or the project name.
        """
        for key in ("description_short", "description"):
            text = str(project.get(key) or "").strip()
            if text:
                paragraph = text.split("\n\n", 1)[0]
                return re.sub(r"[#*_`>\[\]]+", "", paragraph).replace("\n", " ").strip()

        return str(project.get("name") or "")

    @staticmethod
    def _updated(project: dict) -> datetime:
        """
        Reads when the project last changed.

        Args:
            project: The Pinestore project.

        Returns:
            The time in UTC.
        """
        stamp = next((project[key] for key in DATE_KEYS if project.get(key)), 0)
        return datetime.fromtimestamp(stamp / 1000, tz=UTC)

    def _project(self, project: dict) -> ExternalProject:
        """
        Converts a Pinestore project.

        Args:
            project: The project from the API.

        Returns:
            The project.
        """
        project_id = str(project["id"])
        title = str(project.get("name") or f"project {project_id}").strip()
        tags = normalize_tags(project.get("tags"))

        # Use the install command, or build one from the download link
        command = str(project.get("install_command") or "").strip() or None
        target = str(project.get("target_file") or "").strip() or None
        download_url = str(project.get("download_url") or "").strip()
        if not command and download_url:
            command = f"wget {download_url}" + (f" {target}" if target else "")

        # Only link repositories over HTTPS
        repository = str(project.get("repository") or "").strip()

        return ExternalProject(
            id=project_id,
            title=title,
            description=self._describe(project),
            author=str(project.get("owner_name") or "unknown").strip(),
            tags=tags,
            repository=repository if repository.startswith("https://") else None,
            url=PROJECT_URL.format(id=project_id, slug=slugify(title) or project_id),
            command=command,
            target=target,
            library=LIBRARY_TAG in tags,
            updated=self._updated(project),
        )

    # MARK: Functions
    def fetch(self, client: httpx.Client) -> list[ExternalProject]:
        """
        Downloads every visible Pinestore project.

        Args:
            client: The HTTP client.

        Returns:
            The projects.

        Raises:
            httpx.HTTPError: If Pinestore cannot be reached.
            ValueError: If Pinestore's response cannot be read.
        """
        response = client.get(API_URL)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or data.get("success") is False:
            raise ValueError(f"Pinestore returned an unexpected response: {data.get('error') if isinstance(data, dict) else data}")

        return [self._project(project) for project in data["projects"] if project.get("visible", True)]
