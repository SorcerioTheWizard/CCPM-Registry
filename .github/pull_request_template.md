<!-- Fixing a bug in the tools or a package? Use the bug template instead by adding `?template=bug.md` to this page's URL. -->

## Summary

<!-- What does this add or change, and why? Link any related issue, like "Closes #12". -->

## Changes

<!-- The notable changes, one per line. -->

-

## Packages

<!-- Only for pull requests that add or update packages; delete this section otherwise. -->

- [ ] Every file URL is on an allowed host and pinned to the exact content, like a tag or commit
- [ ] Every file has the `sha256` of the bytes its URL serves
- [ ] No published version was edited; changes are published as new versions
- [ ] `compat` lists the ComputerCraft and Minecraft versions it was tested on, if known
- [ ] I installed it with `ccpm` in CraftOS-PC or in game

## Tools

<!-- Only for pull requests that change the Python tools; delete this section otherwise. -->

- [ ] `uv run pytest` passes
- [ ] New behavior is covered by tests
- [ ] Dependencies were changed with `uv add` or `uv remove` only

## Checklist

- [ ] `uv run ccpm-registry validate` passes
- [ ] Follows the code style in `CLAUDE.md`
- [ ] The README and `CLAUDE.md` are updated where behavior or structure changed
