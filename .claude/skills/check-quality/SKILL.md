---
name: check-quality
description: Run the local quality gate for mobilealerts_cloud (ruff, format, mypy strict, pytest with coverage gate incl. migration/coexistence guard suites, translation parity, minimum HA) and summarise failures. Use before finishing any change.
allowed-tools: Bash, Read, Grep, Glob
disallowed-tools: Edit, Write, NotebookEdit
context: fork
agent: quality-runner
background: false
---

# /check-quality

Run every step even if an earlier one fails, then report. This skill runs in the read-only `quality-runner` agent (Haiku) and never fixes anything; the caller fixes and runs it again.

## Steps

Run from the repository root in a Python 3.14 environment with `requirements_test.txt` installed (the system `python3` may be older).

1. `ruff check custom_components tests scripts .claude/hooks`
2. `ruff format --check custom_components tests scripts .claude/hooks`
3. `mypy`
4. `pytest --cov --cov-report=term -q` (fails below 95 % coverage)
5. `pytest -q tests/test_migration.py tests/test_coexistence.py` (guard suites; report them separately even if step 4 already ran them)
6. `python3 scripts/check_translations.py`
7. JSON/YAML sanity: `python3 -c "import json,yaml,pathlib as p; d=p.Path('custom_components/mobilealerts_cloud'); [json.loads(f.read_text()) for f in [d/'manifest.json',d/'strings.json',*d.glob('translations/*.json'),*d.glob('icons.json')]]; yaml.safe_load((d/'services.yaml').read_text()); yaml.safe_load((d/'quality_scale.yaml').read_text())"`
8. Minimum Home Assistant, when the change touches HA APIs, platforms or `requirements_test*.txt`: run `pytest` in a second environment with `requirements_test_min_ha.txt`.
9. Optional, only if Docker is available: `docker run --rm -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest`

Never run tests against the real Mobile Alerts API; the suite uses `tests/mock_api_server.py`.

## Report

Reply with one table, then details only for failures:

| Check | Result | Notes |
|---|---|---|
| ruff check | ✅ / ❌ n findings | |
| ruff format | ✅ / ❌ n files | |
| mypy | ✅ / ❌ n errors | |
| pytest | ✅ n passed / ❌ n failed | coverage xx % (if active) |
| guard suites | ✅ / ❌ | test_migration, test_coexistence |
| minimum HA | ✅ / ❌ / skipped | HA version |
| translations | ✅ / ❌ | missing/orphaned/placeholder counts |
| JSON/YAML | ✅ / ❌ | |

For each failure: file:line, the error in one line, and the likely fix. A failing guard suite is always reported first and blocks the change (AGENTS.md section 5).

If only snapshot tests fail, name the platforms and summarise the `.ambr` difference. Do not run `--snapshot-update` in this skill: whether the change is intended is the author's decision.

Untranslated-value warnings from `check_translations.py` are informational; list them only if the change touched translations.
