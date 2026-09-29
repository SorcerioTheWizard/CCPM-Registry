"""
CCPM Package Wizard

Asks a few questions and writes a valid package entry, so publishing never requires learning the format.
"""

# MARK: Imports
from __future__ import annotations

import json
import posixpath
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import questionary

from ccpm_registry.manifest import file_entry, github_file_url, list_files, list_gist_files, parse_gist, parse_gist_file, parse_paste, parse_repo, paste_url, resolve_commit
from ccpm_registry.registry import PACKAGE_FILE, PACKAGES_DIR, Package, load_registry, write_json
from ccpm_registry.semver import Version, parse_range, parse_version
from ccpm_registry.sources.base import normalize_tags
from ccpm_registry.validate import check_path, program_name, validate_registry
from ccpm_registry.verify import NotTextError

# MARK: Constants
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 200
AUTHOR_LIMIT = 64
PACKAGE_SCHEMA_REFERENCE = "../../schemas/package.schema.json"
VERSION_SCHEMA_REFERENCE = "../../schemas/version.schema.json"
DEFAULT_VERSION = "1.0.0"
NO_STARTUP = "No"
LUA_EXTENSIONS = ("", ".lua")

# Where package files can come from
SOURCE_GITHUB = "A GitHub repository"
SOURCE_GIST = "A GitHub gist"
SOURCE_PASTEBIN = "Pastebin"
SOURCE_RAW = "Other raw file URLs"
SOURCES = [SOURCE_GITHUB, SOURCE_GIST, SOURCE_PASTEBIN, SOURCE_RAW]

# Checks an answer, returning a problem to show or `None` if it is fine
Check = Callable[[str], str | None]


# MARK: Classes
class WizardCancelled(Exception):
    """
    Raised when the user stops the wizard.
    """


class Prompter(Protocol):
    """
    Asks the wizard's questions.
    """

    # MARK: Functions
    def text(self, message: str, default: str = "", check: Check | None = None) -> str:
        """
        Asks for text until it passes a check.

        Args:
            message: The question.
            default: The answer used when nothing is typed.
            check: Returns a problem to show, or `None` when the answer is fine.

        Returns:
            The answer.
        """
        ...

    def select(self, message: str, choices: list[str]) -> str:
        """
        Asks to pick one choice.

        Args:
            message: The question.
            choices: The choices.

        Returns:
            The picked choice.
        """
        ...

    def checkbox(self, message: str, choices: list[str]) -> list[str]:
        """
        Asks to pick any number of choices.

        Args:
            message: The question.
            choices: The choices.

        Returns:
            The picked choices.
        """
        ...

    def confirm(self, message: str, default: bool = True) -> bool:
        """
        Asks a yes or no question.

        Args:
            message: The question.
            default: The answer used when nothing is typed.

        Returns:
            The answer.
        """
        ...


class QuestionaryPrompter:
    """
    Asks the wizard's questions in the terminal with arrow key menus.
    """

    # MARK: Private Functions
    @staticmethod
    def _ask(question: questionary.Question):
        """
        Asks a question, stopping the wizard if the user presses Ctrl+C.

        Args:
            question: The question.

        Returns:
            The answer.

        Raises:
            WizardCancelled: If the question was cancelled.
        """
        answer = question.ask()
        if answer is None:
            raise WizardCancelled()

        return answer

    # MARK: Functions
    def text(self, message: str, default: str = "", check: Check | None = None) -> str:
        """
        Asks for text until it passes a check.

        Args:
            message: The question.
            default: The answer used when nothing is typed.
            check: Returns a problem to show, or `None` when the answer is fine.

        Returns:
            The answer.
        """
        validate = (lambda value: check(value) or True) if check else None
        return self._ask(questionary.text(message, default=default, validate=validate)).strip()

    def select(self, message: str, choices: list[str]) -> str:
        """
        Asks to pick one choice.

        Args:
            message: The question.
            choices: The choices.

        Returns:
            The picked choice.
        """
        return self._ask(questionary.select(message, choices=choices))

    def checkbox(self, message: str, choices: list[str]) -> list[str]:
        """
        Asks to pick any number of choices, requiring at least one.

        Args:
            message: The question.
            choices: The choices.

        Returns:
            The picked choices.
        """
        return self._ask(questionary.checkbox(message, choices=choices, validate=lambda picked: bool(picked) or "Pick at least one."))

    def confirm(self, message: str, default: bool = True) -> bool:
        """
        Asks a yes or no question.

        Args:
            message: The question.
            default: The answer used when nothing is typed.

        Returns:
            The answer.
        """
        return self._ask(questionary.confirm(message, default=default))


class Wizard:
    """
    Walks through publishing a new package, or a new version of one, and writes its files.
    """

    # MARK: Initializer
    def __init__(self, root: Path, prompter: Prompter, client: httpx.Client, echo: Callable[[str], None] = print) -> None:
        """
        Prepares the wizard.

        Args:
            root: The registry root.
            prompter: Asks the questions.
            client: The HTTP client for finding and hashing files.
            echo: Prints messages.
        """
        self.root = root
        self.prompter = prompter
        self.client = client
        self.echo = echo
        self.registry = load_registry(root)[0]

    # MARK: Private Functions
    def _check_name(self, name: str) -> str | None:
        """
        Checks a package name.

        Args:
            name: The name.

        Returns:
            The problem, or `None` if the name can be used.
        """
        if not NAME_PATTERN.match(name) or len(name) > NAME_LIMIT:
            return "Use lowercase letters, digits, `-`, and `_`, starting with a letter or digit."
        return None

    def _ask_name(self) -> tuple[str, Package | None]:
        """
        Asks for the package name, noticing when it already exists.

        Returns:
            The name, and the existing package when publishing a new version of it.
        """
        while True:
            name = self.prompter.text("Package name, used in `ccpm install <name>`:", check=self._check_name)
            package = self.registry.packages.get(name)
            if package is None:
                return name, None
            if self.prompter.confirm(f"`{name}` already exists. Publish a new version of it?"):
                return name, package

    def _git_name(self) -> str:
        """
        Reads the user's name from git to suggest as the author.

        Returns:
            The name, or an empty string if git has none.
        """
        try:
            return subprocess.run(["git", "config", "user.name"], cwd=self.root, capture_output=True, text=True, check=False).stdout.strip()
        except OSError:
            return ""

    def _ask_meta(self, name: str) -> dict:
        """
        Asks for the metadata of a new package.

        Args:
            name: The package name.

        Returns:
            The package metadata.
        """
        def check_https(value: str) -> str | None:
            return None if not value or value.startswith("https://") else "Use an `https://` link, or leave it blank."

        # Ask for the required details
        description = self.prompter.text(
            f"Describe it in one or two sentences, up to {DESCRIPTION_LIMIT} characters:",
            check=lambda value: None if 0 < len(value.strip()) <= DESCRIPTION_LIMIT else f"Write 1 to {DESCRIPTION_LIMIT} characters ({len(value.strip())} now).",
        )
        author = self.prompter.text("Author:", default=self._git_name(), check=lambda value: None if 0 < len(value.strip()) <= AUTHOR_LIMIT else f"Write 1 to {AUTHOR_LIMIT} characters.")

        # Ask for the optional ones
        license_id = self.prompter.text("License, like `MIT` (leave blank for none):")
        repository = self.prompter.text("Source code link (leave blank for none):", check=check_https)
        tags = normalize_tags(self.prompter.text("Search tags, separated by commas, like `mining, turtle` (leave blank for none):"))

        meta = {"$schema": PACKAGE_SCHEMA_REFERENCE, "name": name, "description": description, "author": author}
        for key, value in (("license", license_id), ("repository", repository), ("tags", tags)):
            if value:
                meta[key] = value

        return meta

    def _ask_version(self, package: Package | None) -> str:
        """
        Asks for the version to publish.

        Args:
            package: The existing package, if publishing a new version of it.

        Returns:
            The version.
        """
        # Suggest the next patch version
        latest = package.latest if package else None
        default = str(Version(latest.major, latest.minor, latest.patch + 1)) if latest else DEFAULT_VERSION
        existing = set(package.versions) if package else set()

        def check(value: str) -> str | None:
            try:
                version = parse_version(value)
            except ValueError:
                return "Use a version like `1.0.0`."
            return f"`{value}` is already published; versions never change." if version in existing else None

        return self.prompter.text("Version:", default=default, check=check)

    def _candidates(self, source: str, name: str) -> list[tuple[str, str]]:
        """
        Asks where the files are and finds their pinned URLs.

        Args:
            source: Where the files come from.
            name: The package name.

        Returns:
            Pairs of labels, like file names, and pinned URLs.

        Raises:
            httpx.HTTPError: If the files cannot be found.
        """
        # List a repository's files at a pinned commit
        if source == SOURCE_GITHUB:
            repo = parse_repo(self.prompter.text("Repository, like `owner/name` or its link:", check=lambda value: None if parse_repo(value) else "Use `owner/name` or a GitHub link."))
            assert repo is not None
            ref = self.prompter.text("Branch, tag, or commit to publish:", default="HEAD")
            sha = resolve_commit(self.client, repo, ref)
            self.echo(f"Pinning the files to commit {sha[:7]}.")
            return [(path, github_file_url(repo, sha, path)) for path in list_files(self.client, repo, sha)]

        # Use a raw link to one gist file as given, or list every file of the gist at its current revision
        if source == SOURCE_GIST:
            answer = self.prompter.text("Gist link or ID:", check=lambda value: None if parse_gist(value) else "Use a gist link or its ID.")
            pinned = parse_gist_file(answer)
            if pinned:
                return [pinned]
            gist = parse_gist(answer)
            assert gist is not None
            return list_gist_files(self.client, gist)

        # Read pastes and other links
        if source == SOURCE_PASTEBIN:
            answer = self.prompter.text("Paste codes or links, separated by commas:", check=lambda value: None if all(parse_paste(part) for part in value.split(",")) else "Use paste codes like `AbC123`, or their links.")
            codes = [parse_paste(part) for part in answer.split(",")]
            return [(name if len(codes) == 1 else code, paste_url(code)) for code in codes if code]

        answer = self.prompter.text(
            "File URLs, separated by commas:",
            check=lambda value: None if all(self.registry.is_allowed_url(part.strip()) for part in value.split(",")) else "Every URL must be on an allowed host: " + ", ".join(self.registry.prefixes),
        )
        return [(part.strip().rsplit("/", 1)[-1], part.strip()) for part in answer.split(",")]

    def _suggest_path(self, name: str, label: str, library: bool, count: int) -> str:
        """
        Suggests where to install a file.

        Args:
            name: The package name.
            label: The file's label, like its name.
            library: If the files are a library.
            count: How many files there are.

        Returns:
            The suggested install path.
        """
        base = program_name(posixpath.splitext(posixpath.basename(label))[0]) or name
        if not library:
            return f"bin/{base}.lua"

        # Keep single file libraries at the package's module name
        if count == 1:
            return f"lib/{name}.lua"
        return f"lib/{name}/{'init' if base == name else base}.lua"

    def _ask_files(self, name: str) -> list[dict]:
        """
        Asks for the files to install, then downloads and hashes each one.

        Args:
            name: The package name.

        Returns:
            The file entries.

        Raises:
            WizardCancelled: If the files cannot be used and the user stops.
        """
        package = Package(name, f"{PACKAGES_DIR}/{name}", {})
        while True:
            try:
                # Pick the files
                source = self.prompter.select("Where are the files?", SOURCES)
                candidates = self._candidates(source, name)
                if not candidates:
                    raise ValueError("no files were found there")
                labels = [label for label, _ in candidates]
                picked = labels if len(labels) == 1 else self.prompter.checkbox("Which files should be installed?", labels)
                chosen = [(label, url) for label, url in candidates if label in picked]

                # Confirm files that do not look like Lua
                chosen = [
                    (label, url)
                    for label, url in chosen
                    if posixpath.splitext(label)[1].lower() in LUA_EXTENSIONS or self.prompter.confirm(f"`{label}` does not look like a Lua file. Install it anyway?", default=False)
                ]
                if not chosen:
                    raise ValueError("none of the files were picked")

                # Choose where each one goes
                library = self.prompter.confirm("Are these files a library that other programs `require`?", default=False)
                entries = []
                for label, url in chosen:
                    path = self.prompter.text(
                        f"Install `{label}` to:",
                        default=self._suggest_path(name, label, library, len(chosen)),
                        check=lambda value: check_path(package, value),
                    )
                    self.echo(f"Checking `{url}`...")
                    entries.append(file_entry(self.client, url, path))

                return entries
            except (httpx.HTTPError, NotTextError, ValueError) as error:
                self.echo(f"Those files cannot be used: {error}")
                if not self.prompter.confirm("Try again?"):
                    raise WizardCancelled() from None

    def _ask_extras(self, name: str, files: list[dict]) -> dict:
        """
        Asks for dependencies, compatibility, and a startup program.

        Args:
            name: The package name.
            files: The file entries.

        Returns:
            The optional manifest keys that were given.
        """
        extras: dict = {}

        def check_range(value: str) -> str | None:
            try:
                parse_range(value)
            except ValueError:
                return "Use a range like `^1.2.0` or `>=1.100`."
            return None

        # Ask for dependencies until a blank answer, if it has any
        dependencies = {}
        needs_packages = self.prompter.confirm("Does it need other packages from the registry to work, like a library it `require`s?", default=False)
        while needs_packages:
            dependency = self.prompter.text(
                "Name of a package it needs (leave blank when done):",
                check=lambda value: None if not value or (value in self.registry.packages and value != name) else f"`{value}` is not in the registry.",
            )
            if not dependency:
                break
            latest = self.registry.packages[dependency].latest
            dependencies[dependency] = self.prompter.text(f"Versions of `{dependency}` it works with:", default=f"^{latest}", check=check_range)
        if dependencies:
            extras["dependencies"] = dependencies

        # Ask which game versions it works on
        compat = {}
        for key, label in (("cc", "Which ComputerCraft versions does it work on, like `>=1.100`?"), ("mc", "Which Minecraft versions does it work on, like `1.20`?")):
            answer = self.prompter.text(f"{label} Leave blank if you're not sure:", check=lambda value: None if not value else check_range(value))
            if answer:
                compat[key] = answer
        if compat:
            extras["compat"] = compat

        # Ask for a program to start at boot
        programs = [entry["path"] for entry in files if entry["path"].startswith("bin/")]
        if programs:
            startup = self.prompter.select("Run a program every time the computer boots, like a door lock?", [NO_STARTUP, *programs])
            if startup != NO_STARTUP:
                extras["startup"] = startup

        return extras

    # MARK: Functions
    def run(self) -> list[Path]:
        """
        Asks every question, writes the package files, and checks them.

        Returns:
            The files written.

        Raises:
            WizardCancelled: If the user stops the wizard.
        """
        self.echo("This wizard writes a package entry for the CCPM registry. Press Ctrl+C at any time to stop.")

        # Ask everything
        name, package = self._ask_name()
        meta = None if package else self._ask_meta(name)
        version = self._ask_version(package)
        files = self._ask_files(name)
        manifest = {"$schema": VERSION_SCHEMA_REFERENCE, "kind": "files", "files": files, **self._ask_extras(name, files)}

        # Review before writing
        writes = {}
        if meta is not None:
            writes[f"{PACKAGES_DIR}/{name}/{PACKAGE_FILE}"] = meta
        writes[f"{PACKAGES_DIR}/{name}/{version}.json"] = manifest
        for path, data in writes.items():
            self.echo(f"\n{path}\n{json.dumps(data, indent=4)}")
        if not self.prompter.confirm("Write these files?"):
            raise WizardCancelled()
        for path, data in writes.items():
            write_json(self.root, path, data)

        # Check the result with the registry's own rules
        registry, problems = load_registry(self.root)
        problems += validate_registry(registry)
        if problems:
            self.echo("\nThe files were written, but the registry reports problems to fix before publishing:")
            for problem in problems:
                self.echo(f"  {problem}")
        else:
            branch = f"add-{name}-{version}"
            self.echo(
                "\nThe package is valid. Publish it with a pull request:\n"
                f"  git checkout -b {branch}\n"
                f"  git add {PACKAGES_DIR}/{name}\n"
                f'  git commit -m "Add {name} {version}"\n'
                f"  git push -u origin {branch}\n"
                "Then open a pull request from that branch on GitHub."
            )

        return [self.root / path for path in writes]
