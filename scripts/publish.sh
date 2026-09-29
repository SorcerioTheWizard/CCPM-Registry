#!/bin/sh
# CCPM Registry Publisher
#
# Builds the registry and replaces the `dist` branch with the build, for GitHub Actions.
#
# Requires `TOKEN` (a token that can push) and `GITHUB_REPOSITORY`, which GitHub Actions sets.

set -eu

# MARK: Execution
# Build from the commit being published
REVISION=$(git rev-parse HEAD)
uv run --locked ccpm-registry build --out dist

# Replace the `dist` branch with the build
cd dist
git init -q -b dist
git add -A
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -q -m "Publish $REVISION"
git push -f -q "https://x-access-token:$TOKEN@github.com/$GITHUB_REPOSITORY.git" dist
