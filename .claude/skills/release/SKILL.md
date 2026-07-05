---
name: release
description: Cut a new batcontrol release. Use when asked to prepare, create, or finalize a release, bump the version for a release, or write release notes. Covers the prepare-release workflow, release notes, tagging, and the follow-up steps (next dev version, HA add-on promotion).
---

# Release batcontrol

Versioning: `X.Y.Zdev` during development, `X.Y.Z` for releases. The version lives in
`src/batcontrol/__pkginfo__.py` (`__version__`) and is mirrored in
`pyproject.toml` `[tool.bumpversion] current_version`. All version changes go through
`bump-my-version` (config in `pyproject.toml`) — never edit the version by hand, the two
locations would drift.

Execution policy: run the preparation steps directly, but STOP and ask for confirmation
before every irreversible/outward-facing action — triggering the Prepare Release workflow,
pushing a tag, and publishing the GitHub release.

## Step 1 — Preconditions (check, do not skip)

1. Current version is a `dev` version (`grep __version__ src/batcontrol/__pkginfo__.py`).
2. `main` is green: latest runs of the `pytest` and `pylint` workflows passed.
3. All PRs intended for the release are merged; nothing release-critical open.
4. `./run_tests.sh` passes locally.
5. Docs for new features exist under `docs/` (they publish independently via `docs.yml`).

## Step 2 — Draft the release notes

The Prepare Release workflow creates the GitHub release body with a placeholder — the notes
are written by hand. Draft them now:

1. Find the previous release tag: `git tag --sort=-v:refname | head -1` (or the GitHub
   releases page if the local clone has no tags).
2. Collect merged changes since then: `git log <prev-tag>..main --oneline --merges` or the
   GitHub compare view; group by category the way the HA add-on changelog does
   (Major New Features / Enhancements / Technical Updates / Breaking Changes), one bullet
   per change with PR number `(#NNN)`.
3. Keep the draft ready — it is pasted into the draft release in Step 4.

## Step 3 — Run the Prepare Release workflow  [CONFIRM FIRST]

Preferred path — trigger `prepare-release.yml` (workflow_dispatch, on `main`) via the GitHub
MCP actions tools or the GitHub UI (Actions -> "Prepare Release" -> Run workflow). It:

- runs `bump-my-version bump release --commit` (drops the `dev` suffix, e.g.
  `0.8.1dev` -> `0.8.1`),
- builds wheel + sdist with `uv build --no-sources`,
- creates a **draft** GitHub release with tag `X.Y.Z` and both artifacts attached,
- opens a PR `prepare-release-<timestamp>` -> `main` with the version bump commit.

Fallback (workflow unavailable): do the same steps locally on a branch —
`uv pip install bump-my-version && bump-my-version bump release --commit`, `uv build
--no-sources .`, push branch, open PR, create the draft release with the artifacts manually.

## Step 4 — Finalize  [CONFIRM before tag push and publish]

1. Review and merge the release PR into `main`.
2. Tag the merged commit: `git tag X.Y.Z <merge-commit> && git push origin X.Y.Z`.
   The tag push triggers `docker-image.yml` (multi-arch Docker build).
3. Paste the release notes from Step 2 into the draft release, verify the wheel asset is
   named exactly `batcontrol-X.Y.Z-py3-none-any.whl` (the HA add-on Dockerfile downloads it
   under that name), then publish the release.
4. Verify the Docker image workflow run succeeds.

## Step 5 — Open the next development cycle

Bump `main` to the next dev version: trigger the `Bump version` workflow
(`bump_version.yml`) with bump-type `patch` — bump-my-version rolls `X.Y.Z` to
`X.Y.(Z+1)dev` and opens a `bump-version/...` PR. Merge it.

## Step 6 — Promote into the Home Assistant add-on

The stable HA add-on (`MaStr/batcontrol_ha_addon`, directory `batcontrol/`) pins its
`version` to a release tag and downloads that release wheel. Switch to that repo and run its
`release-addon` skill to promote this release (options/schema sync, changelog, version).

## Checklist

```
[ ] main green, tests pass locally, dev version confirmed
[ ] Release notes drafted (PRs since last tag, categorized)
[ ] Prepare Release workflow run (confirmed by user)
[ ] Release PR merged
[ ] Tag X.Y.Z pushed (confirmed by user) -> Docker build green
[ ] Draft release: notes added, wheel asset name verified, published (confirmed by user)
[ ] Next dev version bumped on main (bump-type: patch)
[ ] HA add-on promotion done (release-addon skill in MaStr/batcontrol_ha_addon)
```
