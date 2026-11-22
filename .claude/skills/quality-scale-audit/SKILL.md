---
name: quality-scale-audit
description: Audit mobilealerts_cloud rule by rule against the current Home Assistant Integration Quality Scale, with code evidence per rule, and report drift between quality_scale.yaml and reality. Use before a release, after larger changes, or when HA publishes new or changed rules.
argument-hint: "[bronze|silver|gold|platinum|all]"
allowed-tools: Bash, Read, Grep, Glob, WebFetch
disallowed-tools: Edit, Write, NotebookEdit
context: fork
model: sonnet
background: false
---

# /quality-scale-audit

Read-only: this skill reports and proposes; changes go into a normal PR afterwards. `$ARGUMENTS` limits the tiers (default `all`).

## 1. Get the current rule list

Fetch the rule list from the Integration Quality Scale docs (developers.home-assistant.io/docs/core/integration-quality-scale/rules) and compare it with the keys of `custom_components/mobilealerts_cloud/quality_scale.yaml`. New rules are missing from the file; removed or renamed rules are stale keys. Read the rule page itself for every rule you check, because the requirements change over time.

## 2. Check every rule against the code

For each rule produce: status in the file, verdict (`ok`, `drift`, `new`), and **evidence**: a `file:line`, a test name, or a doc section. An `exempt` rule needs a reason that is still true today; a `todo` says what is missing.

Typical evidence:

| Rule area | Where to look |
|---|---|
| config flow, subentries, reconfigure | `config_flow.py`, `tests/test_config_flow.py` (every step and error path) |
| entities: unique ID, naming, translations, icons, categories, disabled by default, device classes | `sensor.py`, `binary_sensor.py`, `event.py`, `api/models.py`, `strings.json`, `icons.json`, registry snapshots |
| actions, exceptions | `services.py` / `__init__.py`, `services.yaml`, `strings.json` `exceptions` |
| devices, stale and dynamic devices | `entity.py` (`DeviceInfo`), `__init__.py` (`_prune_stale_entities`, `async_remove_config_entry_device`) |
| runtime data, unload, parallel updates, availability, logging | `__init__.py`, `coordinator.py`, `entity.py` (`available`), every platform's `PARALLEL_UPDATES` |
| diagnostics, repairs | `diagnostics.py` (redaction of sensor and phone IDs), issue registry calls |
| docs rules | README sections (installation, removal, configuration, data updates, use cases, examples, limitations, troubleshooting, supported devices and functions) |
| test coverage, strict typing, async dependency, websession | `pyproject.toml` (`fail_under`, mypy), `api/client.py` |

Run the relevant tests rather than trusting their names: `pytest -q <file>` for each test file you cite.

## 3. Report

One table per tier: rule, file status, verdict, evidence, proposed change. Then a short list of the changes needed, each tagged as code, test, docs or `quality_scale.yaml` only. Do not set a rule to `done` without evidence; when unsure, propose `todo` with a comment. AGENTS.md section 10 requires file and code to match in the same PR.
