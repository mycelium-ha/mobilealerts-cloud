---
name: release
description: Prepare a mobilealerts_cloud release (quality gate, SemVer bump in manifest.json and pyproject.toml, CHANGELOG, Hassfest/HACS checks, release PR). Tagging and publishing need explicit maintainer approval.
argument-hint: "[X.Y.Z]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
disable-model-invocation: true
---

# /release

Started only by the maintainer (`disable-model-invocation`). `$ARGUMENTS` may name the version; otherwise derive it in step 1.

## 1. Decide the version

Read `CHANGELOG.md` `[Unreleased]`. SemVer: breaking change (removed/renamed action or field, event payload change, raised minimum HA, removed option, changed entity naming or state values) → major; new feature or deprecation → minor; fixes only → patch. Propose the version and wait for confirmation if it is a major release. For a major release, list deprecations that have been deprecated for at least one released minor version and ask whether to remove them.

## 2. Pre-flight

1. Start from an up-to-date `main` on a branch `release/vX.Y.Z` (or the branch your environment prescribes).
2. Run `/check-quality`; everything active must be green.
3. Check the latest CI run on `main` is green (`gh api repos/{owner}/{repo}/actions/runs?branch=main&per_page=1`), including Hassfest, HACS validation and, once it exists, the minimum-HA job. Report the latest beta run too; a red beta run does not block, but name it in the release PR.
4. Run `/quality-scale-audit` and resolve any drift; confirm README documents every user-visible change.
5. Run `/review` on the changes since the last tag (`git describe --tags --abbrev=0`..HEAD); no open `blocker` or `major` finding.

## 3. Bump

1. `custom_components/mobilealerts_cloud/manifest.json` → `"version": "X.Y.Z"` (keep key order: `domain`, `name`, then alphabetical).
2. `pyproject.toml` → `version = "X.Y.Z"`.
3. If the minimum HA changed: `hacs.json` → `homeassistant`, `requirements_test_min_ha.txt`, AGENTS.md section 1, README and an ADR, all to the same version.
4. `CHANGELOG.md`: rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD`, add a fresh empty `[Unreleased]`, group entries as Breaking changes / Added / Changed / Deprecated / Removed / Fixed (Breaking changes first, empty groups left out). Breaking changes need migration instructions.

## 4. Release PR

Commit (`Release vX.Y.Z`), push the branch, open a PR with the CHANGELOG section as body (`gh api repos/{owner}/{repo}/pulls`), assign the maintainer and wait for green CI. If CI fails, fix the cause in this PR (never skip or disable a test); after 3 failed attempts on the same check, stop and report.

## 5. Tag and publish (only after explicit maintainer approval)

After the PR is merged and the maintainer approves: create the GitHub release `vX.Y.Z` from `main` with the CHANGELOG section as notes. HACS / Marketplace picks the release up from the tag. Never create tags or releases without that approval.

## 6. After the release

Note what the maintainer should check on a live instance: integration loads without errors, entities and devices look as before (names, areas), diagnostics download works and contains no sensor IDs, no warnings in the log, no unexpected entries under Settings → Repairs, no rate-limit warnings after a restart.
