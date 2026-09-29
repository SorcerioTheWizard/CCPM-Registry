## Bug

<!-- What went wrong? Link the issue, like "Fixes #12", or describe the bug and how to reproduce it. For a broken package, name the package and version. -->

## Cause

<!-- Why it happened. -->

## Fix

<!-- What this changes to fix it. For a synced package, this is usually an entry in `external/<source>/overrides.json`. For a package published here, it is a new version, never an edit to a published one. -->

## Testing

- [ ] Added a test that fails without this fix, for tool changes
- [ ] `uv run pytest` passes
- [ ] `uv run ccpm-registry validate` passes
- [ ] Checked the original steps no longer reproduce the bug

## Checklist

- [ ] Follows the code style in `CLAUDE.md`
