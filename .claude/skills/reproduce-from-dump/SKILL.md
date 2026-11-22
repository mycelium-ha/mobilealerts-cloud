---
name: reproduce-from-dump
description: Turn a user's Mobile Alerts dump (dump_raw_response output or raw API response from an issue or a file) into anonymised fixture devices and a failing test that reproduces the reported bug. Use for bug reports about wrong, missing or broken entities, before fixing anything.
argument-hint: "<path to dump JSON | issue number>"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /reproduce-from-dump

Goal: a test that fails today for the reason the user reported, built from their real sensor payloads without their sensor IDs. Fixing the bug is a separate step (`/add-profile` or a normal change) and starts once this test fails for the right reason.

Input: `$ARGUMENTS` is a local dump file or a GitHub issue number. For an issue, read it with `gh api repos/{owner}/{repo}/issues/<n>` and `.../comments`. The useful dump is the response of `mobilealerts_cloud.dump_raw_response` (unredacted, so ask the user to replace the IDs before posting, or to send it privately). A diagnostics download hides the IDs and with them the profile prefix: ask for the prefix (first two characters) if that is all you have.

## 1. Anonymise first

Never commit, paste or quote a raw dump, not even in a PR or a comment (AGENTS.md section 5.8).

1. Keep the raw file outside the repository (scratch directory).
2. `python3 scripts/anonymize_dump.py <raw.json> -o <scratch>/anon.json`. IDs become `<prefix>0000AA<nnnn>`, phone IDs are dropped; the summary on stderr lists prefixes and dropped keys.
3. Check the output by hand: no 12-hex ID other than the fixture ones, no phone ID, no names.

## 2. Add to the fixture

- Add the reduced devices to `tests/fixtures/live_responses.json`: `live_captured_shapes` for real payload shapes, `edge_cases` for odd values. Name the key after what it covers (`ID0B_gust_without_speed`).
- If the device should be part of the default test hub, add its ID to `mock_config_entry` in `tests/conftest.py`; this changes registry snapshots. Regenerate with `pytest tests/test_platforms.py --snapshot-update` and check the `.ambr` diff contains only the new device.
- Timestamps decide availability (ADR-005): tests run at `FIXTURE_NOW` in `tests/conftest.py`. Shift `lastseen`/`ts`/`c` into its range unless the bug is about time.

## 3. Write the failing test

- Use `mock_api`, `hub_entry`, `init_integration`, `entity_id_for` (AGENTS.md section 6); look entities up by unique ID.
- Name it after the behaviour (`test_wind_gust_without_speed_is_unknown`) and mention the issue in the docstring.
- Run it and confirm it fails **for the reported reason**. If after 3 attempts it passes or fails for another reason, stop and report: the bug may need data the dump lacks, or it is already fixed on `main`.

## 4. Report

Reply with what the user's payload has that the fixture lacked, the test name and its failure message, and the suggested fix in one or two sentences. Commit fixture and test together with the fix on a branch.
