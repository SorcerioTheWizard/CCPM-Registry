# Computer Craft Package Manager Registry

> The primary registry for the ComputerCraft Package Manager.
> 
> The package manager for ComputerCraft and ComputerCraft: Tweaked with Pinestore (and more) support.

This repository lists the packages that [CCPM](https://github.com/SorcerioTheWizard/ComputerCraft-Package-Manager) can install.
It only stores metadata; package files stay wherever their authors host them.

* [Computer Craft Package Manager Registry](#computer-craft-package-manager-registry)
    * [How It Works](#how-it-works)
    * [Publishing a Package](#publishing-a-package)
        * [By Hand](#by-hand)
    * [Package Format](#package-format)
        * [Package Metadata](#package-metadata)
        * [Version Manifests](#version-manifests)
        * [Install Paths](#install-paths)
        * [Version Ranges](#version-ranges)
    * [Allowed Hosts](#allowed-hosts)
    * [External Sources](#external-sources)
        * [Overrides](#overrides)
    * [Development](#development)

## How It Works

Every package has a folder under `packages/` holding a `package.json` with its metadata and one `<version>.json` manifest per published version.
Each manifest lists the files to install, the URL each is downloaded from, and the SHA-256 of its contents.

When a change lands on `master`, CI validates the registry and publishes a compact build to the `dist` branch.
CCPM clients read `index.json` from that branch to search and resolve packages, then fetch the manifests of the versions they install:

```
https://raw.githubusercontent.com/SorcerioTheWizard/CCPM-Registry/dist/index.json
https://raw.githubusercontent.com/SorcerioTheWizard/CCPM-Registry/dist/packages/<name>/<version>.json
```

Anyone can host a registry of their own by publishing files in the same layout.

## Publishing a Package

The easiest way is the publishing wizard.
Clone this repository, install [uv](https://docs.astral.sh/uv/), and run:

```bash
uv run ccpm-registry new
```

It asks for your package's name, description, and version, then where its files are: a GitHub repository, a GitHub gist, Pastebin, or other raw links on an [allowed host](#allowed-hosts).
It pins every file to the exact version you are publishing, checks it is a text file, records its hash, and suggests where to install it.
When it is done, it writes the package, checks it with the registry's own rules, and prints the commands to open a pull request.
Run it again with an existing package's name to publish a new version.

### By Hand

1. Host your files at raw text URLs on an [allowed host](#allowed-hosts), pinned to the exact content you are publishing.
2. Hash each file: `uv run ccpm-registry hash <url> [<url> ...]`.
3. Add `packages/<name>/package.json` if the package is new, then add `packages/<name>/<version>.json`.
4. Check your work: `uv run ccpm-registry validate`.
5. Open a pull request. CI validates the registry and downloads your files to confirm their hashes.

Published versions never change.
To fix a release, publish a new version.

If your files live in a public GitHub repository, steps 2 and 3 can be done in one command.
It pins every file to the commit the ref points at and records its hash:

```bash
uv run ccpm-registry manifest <name> <version> <repo path>=<install path> ... --github <owner>/<repo> --ref <branch, tag, or commit>
```

A folder maps every file inside it, and `--depends <package>=<range>`, `--cc <range>`, `--mc <range>`, and `--startup <path>` fill in the optional keys.
For example, CCPM itself is published with:

```bash
uv run ccpm-registry manifest ccpm 0.1.0 src/bin/ccpm.lua=bin/ccpm.lua src/lib/ccpm=lib/ccpm --github SorcerioTheWizard/ComputerCraft-Package-Manager --ref master
```

Set `GITHUB_TOKEN` if you hit GitHub's rate limit for anonymous API requests.

A minimal program looks like this:

`packages/orescanner/package.json`

```json
{
    "$schema": "../../schemas/package.schema.json",
    "name": "orescanner",
    "description": "Scans for ores around the player and shows them on a radar.",
    "author": "SorcerioTheWizard",
    "license": "MIT",
    "tags": ["mining", "advanced-peripherals"]
}
```

`packages/orescanner/1.0.0.json`

```json
{
    "$schema": "../../schemas/version.schema.json",
    "kind": "files",
    "files": [
        {
            "url": "https://gist.githubusercontent.com/SorcerioTheWizard/6e363dd7186148677fcfd17d169e631b/raw/<revision>/OreScanner.lua",
            "path": "bin/orescanner.lua",
            "sha256": "<hash from step 2>"
        }
    ],
    "compat": { "cc": ">=1.100" }
}
```

## Package Format

The full rules are in the JSON schemas under `schemas/`, which editors use for autocompletion through the `$schema` key.

### Package Metadata

`packages/<name>/package.json`:

| Key           | Required | Description                                                                                    |
| ------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `name`        | Yes      | Lowercase letters, digits, `-`, and `_`. Must match the folder name.                           |
| `description` | Yes      | A summary of up to 200 characters, shown in search results.                                    |
| `author`      | Yes      | The author or team.                                                                            |
| `license`     | No       | An SPDX identifier, like `MIT`.                                                                |
| `repository`  | No       | An `https://` link to the source.                                                              |
| `homepage`    | No       | An `https://` link to documentation.                                                           |
| `tags`        | No       | Up to 10 lowercase search keywords.                                                            |

### Version Manifests

`packages/<name>/<version>.json`, where `<version>` is a [semantic version](https://semver.org) like `1.2.0` or `2.0.0-beta.1`:

| Key            | Required | Description                                                                                                  |
| -------------- | -------- | ------------------------------------------------------------------------------------------------------------ |
| `kind`         | Yes      | Always `files` for packages published here.                                                                  |
| `files`        | Yes      | The files to install, each with a `url`, an install `path`, and the `sha256` of the exact bytes served.      |
| `dependencies` | No       | Other packages this version needs, mapped to the [version range](#version-ranges) it accepts.                |
| `compat`       | No       | Version ranges of ComputerCraft (`cc`) and Minecraft (`mc`) this version works on.                           |
| `startup`      | No       | A `bin/` file from `files` that CCPM runs every time the computer boots, like a door lock or kiosk.          |

CCPM reads the running ComputerCraft and Minecraft versions from `_HOST` and refuses to install a version whose `compat` does not match unless the user passes `--force`.

### Install Paths

Paths are relative to the CCPM folder on the computer, `/ccpm`:

| Path                                          | Purpose                                                                                   |
| --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `bin/<program>.lua`                           | A program, run by typing its name. Program names must be unique across the registry.      |
| `lib/<name>.lua` or `lib/<name>/...`          | A library, loaded with `require("<name>")`.                                               |
| `share/<name>/...`                            | Read-only assets like images or default configs.                                          |

### Version Ranges

Dependencies and `compat` use a subset of npm's range syntax:

| Range            | Matches                                              |
| ---------------- | ---------------------------------------------------- |
| `*`              | Any release.                                         |
| `1.2.3`          | Exactly `1.2.3`.                                     |
| `1.20`           | Any `1.20.x`.                                        |
| `^1.2.3`         | `>=1.2.3 <2.0.0` (`^0.2.3` is `>=0.2.3 <0.3.0`).     |
| `~1.2.3`         | `>=1.2.3 <1.3.0`.                                    |
| `>=1.19 <1.21`   | Every comparison must pass.                          |
| `1.2 \|\| >=2.0` | Either side may pass.                                |

Prereleases only match a range that names a prerelease of the same version, like `>=2.0.0-beta.1`.

## Allowed Hosts

Package files must come from one of the URL prefixes in `hosts.json`:

- `https://raw.githubusercontent.com/`: pin the URL to a tag or commit, like `.../<user>/<repo>/v1.0.0/src/tool.lua`.
- `https://gist.githubusercontent.com/`: use the revision link from the gist's `Raw` button, like `.../<user>/<gist>/raw/<revision>/tool.lua`.
- `https://github.com/`: for release downloads, like `.../<user>/<repo>/releases/download/v1.0.0/tool.lua`.
- `https://pastebin.com/raw/`: pastes cannot be pinned, so a paste that is edited later will fail its hash check and stop installing.

The weekly audit re-downloads every file of the packages published here and reports any that no longer match their hash.

## External Sources

The registry also mirrors other ComputerCraft catalogs, so their projects can be searched and installed with CCPM.
[Pinestore](https://pinestore.cc) is mirrored today, and its projects are named `pinestore/<name>`.

Once a day, the `Sync` workflow runs `ccpm-registry sync <source>`, commits what changed under `external/<source>/`, and publishes the registry.
It can also be started by hand from the Actions tab.

The sync only reads the source's catalog; it never downloads a project's files.
Files are downloaded when a computer installs the project, straight from where the author hosts them, exactly like the source's own install command would.

For each project, the sync:

- Installs commands that only download one file, like `wget <url> <file>` or `pastebin get <code> <file>`, as tracked files CCPM can update and remove.
  Files tagged `library` are installed to `lib/` so programs can `require` them, and files saved as `startup` run at boot.
- Runs every other command, like `wget run <url>`, as the project's own installer after the user confirms it.
  CCPM cannot track or remove the files an installer creates.
- Publishes a new version when what the project installs changes, like its command, its download link, or an override.
  Versions are the time of the change in UTC, like `2026.929.143005` for 2026-09-29 14:30:05, so they sort by date.
- Keeps a package's name forever once assigned, even if the project is renamed, and adds the project's ID to names that would clash.
- Marks projects as `delisted` when the source no longer lists them or they cannot be installed, like a command that downloads a web page instead of a file.
  Delisted packages are left out of the published index but keep their versions, and are listed again once the project is fixed.

Because synced files have no published hash, CCPM installs them unverified, says so before installing, and refuses any download that turns out to be a web page.

Mirroring never costs authors their stats: every time CCPM installs a synced project, it reports the download to the source, so Pinestore download counts keep growing exactly as if the project were installed from Pinestore directly.

Synced packages are generated; never edit them by hand.
Published versions never change, the same as packages published directly.

### Overrides

Maintainers can correct projects in `external/<source>/overrides.json`, keyed by the project's ID in the source.
The sync applies them every time it runs, and `schemas/overrides.schema.json` describes every key.

```json
{
    "$schema": "../../schemas/overrides.schema.json",
    "123": {
        "note": "The listed command downloads the installer instead of running it.",
        "command": "wget run https://example.com/install.lua",
        "dependencies": { "pixelbox": "*" },
        "compat": { "cc": ">=1.100" }
    },
    "456": { "skip": true }
}
```

## Development

The registry tools are Python, managed by [uv](https://docs.astral.sh/uv/):

```bash
uv sync                          # Install the tools
uv run pytest                    # Test the tools
uv run ccpm-registry validate    # Check the registry offline
uv run ccpm-registry verify      # Download every file and check its hash
uv run ccpm-registry build       # Write the published form to `dist/`
uv run ccpm-registry new         # Publish a package by answering questions
uv run ccpm-registry sync <src>  # Mirror an external source into `external/<src>/`
```

`uv run main.py <command>` is the same as `uv run ccpm-registry <command>`, and `uv run main.py -h` lists every command.

Add or remove dependencies with `uv add` and `uv remove`; never edit `pyproject.toml` dependencies by hand.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to publish packages, fix synced ones, and propose changes to the tools.
