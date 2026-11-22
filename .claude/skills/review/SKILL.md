---
name: review
description: Senior review of the current branch, a PR or a commit range of mobilealerts_cloud by the Opus ha-reviewer agent (protection rules, correctness, HA standards, tests, docs). Use before opening or merging a PR, and as the last step of /add-profile, /bump-ha and /release.
argument-hint: "[PR number | git range, default origin/main...HEAD]"
context: fork
agent: ha-reviewer
background: false
disallowed-tools: Edit, Write, NotebookEdit
---

# /review

Review target: `$ARGUMENTS` (empty means the current branch against `origin/main`, plus uncommitted changes).

Current state of the working tree:

!`git status --short --branch`

Recent commits:

!`git log --oneline -8`

## Steps

1. Get the diff. Empty target: `git fetch -q origin main` then `git diff origin/main...HEAD` and `git diff HEAD`. A number: read the PR with `gh api repos/{owner}/{repo}/pulls/<n>` and its files with `.../pulls/<n>/files?per_page=100`; check it out read-only with `git fetch origin pull/<n>/head` and diff against its base. A range: `git diff <range>`.
2. Review it as your agent instructions describe (AGENTS.md section 5 first, then correctness, HA standards verified in the installed source, tests, docs).
3. Run the test files that cover the changed code, and `pytest -q tests/test_migration.py tests/test_coexistence.py` when `migration.py`, unique IDs or device identifiers are touched.

## Report

A table of findings (severity, `file:line`, defect, failure scenario, fix), then the verdict. Write nothing to GitHub: the caller decides what to do with the findings.
