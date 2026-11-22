# Mobile Alerts Cloud — Engineering Handbook

Binding rules for everyone (humans and AI agents) changing this repository. This file is the single source of truth; `CLAUDE.md` imports it. Keep it under 200 lines: detail belongs in `docs/`, linked from here.

## 1. Project & quality target

`mobilealerts_cloud` is a Home Assistant custom integration for Technoline Mobile Alerts and TFA Dostmann WeatherHub sensors, built only on the official Mobile Alerts REST API (`POST https://www.data199.com/api/pv1/device/lastmeasurement`, spec: [mobile-alerts.eu public_server_api_documentation.pdf](https://mobile-alerts.eu/info/public_server_api_documentation.pdf)). It runs side by side with the legacy `mobile_alerts` integration and can take over from it (`mobilealerts_cloud.migrate_from_legacy`).

- **Target:** Integration Quality Scale **Gold**, architecturally ready for **Platinum**. The status of every rule lives in `custom_components/mobilealerts_cloud/quality_scale.yaml`; update it in the same PR that changes a rule's status.
- **Minimum Home Assistant:** 2026.9.0 ([ADR-001](docs/DECISIONS.md)). Do not add compatibility guards for older releases.
- **Binding design decisions:** [docs/DECISIONS.md](docs/DECISIONS.md). Patterns and details: [docs/HA_INTEGRATION_STANDARDS.md](docs/HA_INTEGRATION_STANDARDS.md). Known spec deviations and live-data observations: [docs/API_SPEC_NOTES.md](docs/API_SPEC_NOTES.md).

## 2. Repository layout

- `custom_components/mobilealerts_cloud/api/` — HA-free library: REST client with rate-limit guard (`client.py`), device profiles, validation and payload parser (`models.py`).
- `custom_components/mobilealerts_cloud/` — HA layer: setup and process-wide state (`__init__.py`), actions (`services.py`), hub, options and sensor subentry flows (`config_flow.py`), polling (`coordinator.py`), legacy takeover (`migration.py`), entity base (`entity.py`), platforms (`sensor.py`, `binary_sensor.py`, `event.py`), `diagnostics.py`, `const.py` (HA constants only), `manifest.json`, `services.yaml`, `strings.json`, `translations/` (7 locales), `quality_scale.yaml`, `brand/`.
- `tests/` — test suite, `mock_api_server.py` (API double), `fixtures/live_responses.json` (anonymised payloads).
- `scripts/` — developer tooling (`check_translations.py`, `anonymize_dump.py` for turning user dumps into fixtures).
- `docs/` — decisions, standards, API spec notes, user migration guide.
- `.claude/skills/` — Claude Code workflows (section 9).

## 3. Architecture

1. **Two layers, one direction.** `api/` is written as if it were its own PyPI package: stdlib + `aiohttp` only, never `homeassistant` and never the HA layer (an AST test enforces this), fully typed, raises its own exceptions (`MobileAlertsError` and subclasses). The HA layer depends on `api/`, never the reverse.
2. **Data flow.** `client.fetch_measurements` (one batched POST for all sensors of a hub) → `coordinator` (one `DataUpdateCoordinator` per hub) → `DeviceReading` → entities via `EntityDescription.value_fn`. Entities never call the API directly.
3. **One hub config entry, one subentry per sensor** (`integration_type: hub`), typed `entry.runtime_data` (`MobileAlertsCloudConfigEntry`). Sensors are added and removed only through the sensor subentry flow. A sensor ID is unique across all hubs ([ADR-004](docs/DECISIONS.md)).
4. **Device model:** hub device (`DeviceEntryType.SERVICE`, `hub_{entry_id}`) → one device per sensor (`(mobilealerts_cloud, device_id)`, `via_device_id` = hub). The profile (`IDxx`) is resolved by ID prefix, overridable per subentry.
5. **Services** are registered once in `async_setup` (`services.py`) and target an entry via `config_entry_id`. Actions that change state need exactly one target; read-only actions may cover all loaded hubs.
6. **Rate limits are a hard contract** (spec): at most 3 calls per sensor per minute (else HTTP 429, 7 min block), more than 5 invalid calls per IP in 15 min block the IP (HTTP 403). Every request, from polls, flows, services and the migration, goes through `MobileAlertsClient` and the one `RateLimitState` shared by the whole Home Assistant instance ([ADR-009](docs/DECISIONS.md)). Validate IDs locally before any request.

## 4. Coding standards

- **Python 3.14 (required by Home Assistant 2026.3+), typed.** `mypy --strict` must pass for the whole integration (`api/` and HA layer). No new `Any` in public signatures, no bare `# type: ignore` (always with error code).
- **Async:** no blocking I/O in the event loop. Use HA's shared session via `async_get_clientsession`. Never create unmanaged tasks.
- **Entities:**
  - `_attr_has_entity_name = True` on every entity; names come from `translation_key`. No hard-coded English names or labels (selectors use `translation_key` too).
  - Describe entities with frozen `EntityDescription` dataclasses (`key`, `translation_key`, `device_class`, `state_class`, `entity_category`, `entity_registry_enabled_default`, `value_fn`).
  - Icons come from `icons.json` (including state icons), never `icon=` or `_attr_icon`.
  - Diagnostic entities set `entity_category`; duplicates and rarely used values are disabled by default (new installations only).
  - `PARALLEL_UPDATES = 0` on every platform (one coordinator per hub).
  - No raw API JSON in `extra_state_attributes` (use diagnostics).
  - Availability follows the coordinator and the sensor's own `lastseen` ([ADR-005](docs/DECISIONS.md)).
  - Sentinels `43530` (not connected), `65295` (out of range) and `65296` (seen in live data) become `None`.
- **Errors:** user-facing exceptions (`HomeAssistantError`, `ServiceValidationError`, `ConfigEntryNotReady`) use `translation_domain=DOMAIN` + `translation_key`; texts live in `strings.json` → `exceptions`. Catch `api/` exceptions at the HA boundary.
- **Config flow:** every field has `data_description`; specific errors (`cannot_connect`, `rate_limited`, `ip_blocked`, `device_not_found`, `invalid_device_id_format`, `unknown`); reconfigure uses `async_update_and_abort` with the update listener as the only reload owner.
- **Repairs:** `issue_registry` only for problems the user must fix outside the existing flows, deleted once resolved.
- **Diagnostics:** sensor IDs and phone IDs are credentials (anyone who knows a sensor ID can read its data). Diagnostics redact phone IDs and replace every sensor ID, including short forms in titles and rate-limit keys, with a consistent alias (`sensor_1`); a test proves no ID remains. API error messages show only the last four characters (`mask_id`), and log messages use the sensor's title or `mask_id`, never the full ID. `dump_raw_response` stays unredacted for local debugging.
- **Style:** `ruff check` and `ruff format` (config in `pyproject.toml`). Docstrings on public functions. Log with `%s` formatting, never f-strings.
- **Verify HA APIs in the installed HA source**, never from memory or older docs. An earlier version of `docs/HA_INTEGRATION_STANDARDS.md` listed deprecations that do not exist.

## 5. Protection rules (never break these)

1. **Stable identifiers.** Never change entity `unique_id`s (`{device_id}_{channel_key}`), device identifiers (`(mobilealerts_cloud, device_id)`, `hub_{entry_id}`) or subentry unique IDs (`device_id`) without a registry migration and a test proving it.
2. **Domain isolation.** Never use the legacy domain `mobile_alerts`, its identifiers, unique IDs, services or events in normal operation. The event `mobilealerts_cloud_key_press` is public API.
3. **`migration.py`** changes only with green `tests/test_migration.py` and `tests/test_coexistence.py`, and with a new test for the changed behaviour. Keep its safety invariants ([standards §5](docs/HA_INTEGRATION_STANDARDS.md)).
4. **Rate limits:** never loosen the guard in `api/client.py`, never send a request around it.
5. **Translations:** `strings.json` is the source; all 7 locales (`en`, `de`, `es`, `fr`, `it`, `nl`, `pl`) stay key-identical and placeholder-identical (`scripts/check_translations.py`).
6. **Public service schemas and event payloads** change only in a backwards-compatible way, or with a CHANGELOG entry under "Breaking changes".
7. **README** contains no third-party attribution/credit sections or links to legacy forks. Call HACS on HA 2026.11+ "Home Assistant Marketplace".
8. **Secrets:** never commit real sensor IDs, phone IDs or raw API dumps; fixtures are anonymised.

## 6. Testing

```bash
pip install -r requirements_test.txt   # Python 3.14; pins HA core, pytest, syrupy, ruff
pytest                     # whole suite
pytest tests/test_migration.py tests/test_coexistence.py   # guard suites
```

- Every feature or bug fix comes with a test. Config flow: every step and every error path, 100 % coverage of `config_flow.py`, and every error test continues until the flow creates the entry (the flow must recover). Platforms: setup, state updates from a poll, unavailability.
- Tests run against real Home Assistant (`pytest-homeassistant-custom-component`, [ADR-002](docs/DECISIONS.md)). Fixtures in `tests/conftest.py`: `mock_api` (mock API on a free localhost port), `mock_config_entry`, `init_integration`, `mock_setup_entry`, `entity_registry_enabled_by_default`; helpers `hub_entry()` and `entity_id_for()`. Import the integration as `custom_components.mobilealerts_cloud`.
- **Snapshots (syrupy):** `tests/snapshots/` holds entity and device registry snapshots per platform. Regenerate only intentionally with `pytest --snapshot-update` and review the `.ambr` diff like code.
- Use `tests/mock_api_server.py` and `tests/fixtures/live_responses.json` for API behaviour; extend the fixture rather than mocking internals.
- Look entities up by unique ID, never by hard-coded entity ID.
- Coverage: > 95 % line and branch coverage for the integration package; `fail_under = 95` in `pyproject.toml` fails CI below that.

## 7. Quality gate

A change is done only when all checks that are active pass (the `/check-quality` skill runs them and summarises):

```bash
ruff check custom_components tests scripts .claude/hooks
ruff format --check custom_components tests scripts .claude/hooks
pytest --cov --cov-report=term       # includes the coverage gate (fail_under = 95)
python3 scripts/check_translations.py
mypy
```

CI (`.github/workflows/validate.yml`) calls the shared Mycelium HA workflow ([mycelium-ha/.github](https://github.com/mycelium-ha/.github)): the checks above plus the tests on the minimum Home Assistant (`requirements_test_min_ha.txt`), Hassfest and HACS validation; `ha-beta.yml` tests the newest HA pre-release weekly. `.pre-commit-config.yaml` runs ruff, mypy and the translation check locally. When the minimum HA changes, update `requirements_test_min_ha.txt` with it; Dependabot skips that file on purpose.

## 8. Workflow

- Small, single-purpose PRs on a feature branch; never push to `main` directly. Fill in `.github/pull_request_template.md`.
- Bug fixes start with a failing test. Raw dumps never enter the repository, a PR or an issue comment.
- Review comments: human reviewers first. Bot suggestions are accepted when they prevent a bug, fix a rule in this file or clearly help maintainers; otherwise answer why not. Agents stop and report after 3 failed attempts at the same CI failure.
- Add user-visible changes to `CHANGELOG.md` under `[Unreleased]`.
- Keep this file, `docs/`, `quality_scale.yaml` and the code consistent in the same PR.
- Releases follow the `/release` skill; versions stay in sync across `manifest.json`, `pyproject.toml`, `CHANGELOG.md` and the `vX.Y.Z` tag. No tag or release without the maintainer's explicit go.

## 9. Claude Code skills, agents and hooks

- `/check-quality` — run the quality gate and report (forked into `quality-runner`).
- `/review` — senior review of a branch, PR or range by `ha-reviewer`; read-only.
- `/add-profile` — add or change a sensor profile, measurement key, entity or action end to end.
- `/sync-translations` — reconcile `strings.json` with all 7 locales.
- `/release` — prepare a release (only when the maintainer starts it).
- `/issue-analysis` — analyse an issue and draft a reply for approval.
- `/reproduce-from-dump` — anonymise a user's dump into fixture devices and a failing test.
- `/bump-ha` — move tests to a new HA release; raise the minimum HA with an ADR when needed.
- `/quality-scale-audit` — check every quality scale rule against the code, with evidence.
- `protection-rules` — not a command: loads automatically when a file guarded by section 5 is edited.

**Agents** (`.claude/agents/`), tiered by model: `quality-runner` (Haiku) runs commands and reports; `translator` (Sonnet) edits locale files; `ha-reviewer` (Opus) reviews every change before merge and never edits. Routine work goes to the cheapest agent that can do it reliably; judgement (review, design, release) stays with Opus. Workers report to the caller; `/review` is the final check, once per PR.

**Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`) enforce what must not depend on memory: no push to `main`, no plain `--force`, tags and releases ask the human, commits with sensor-ID-shaped values that are not fixture IDs are rejected (`allowed_ids.txt` lists reviewed exceptions), Python files are formatted after every edit. Hooks are stdlib-only and fail open; CI stays the gate.

Skill conventions: frontmatter with `description` (when to use) and `argument-hint`; `allowed-tools` pre-approves, `disallowed-tools` enforces read-only; `disable-model-invocation: true` for skills with outward effects; `context: fork` with an `agent` for self-contained tasks; under ~100 lines, longer checklists in the skill's `references/`; a step that posts, tags or publishes waits for the maintainer.

## 10. Quality scale status

`quality_scale.yaml` records the honest current state: `done`, `todo` (with what is missing) or `exempt` (with a reason). A change that breaks a rule must fix it in the same PR or set the rule back to `todo` with a comment; never let the file and the code drift apart.

## 11. References

- Integration Quality Scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
- Fetching data, coordinator: https://developers.home-assistant.io/docs/integration_fetching_data
- Config flow, subentries, reconfigure: https://developers.home-assistant.io/docs/config_entries_config_flow_handler
- Entities (`has_entity_name`, `EntityDescription`): https://developers.home-assistant.io/docs/core/entity
- Diagnostics: https://developers.home-assistant.io/docs/core/integration-diagnostics
- Repairs: https://developers.home-assistant.io/docs/core/platform/repairs
- Translations and icons: https://developers.home-assistant.io/docs/internationalization/core
- Testing: https://developers.home-assistant.io/docs/development_testing
- HACS publishing: https://hacs.xyz/docs/publish/integration/
