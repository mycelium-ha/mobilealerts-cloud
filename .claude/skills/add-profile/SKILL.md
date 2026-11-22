---
name: add-profile
description: Add or change a Mobile Alerts sensor profile (IDxx prefix), measurement key, entity type or action in mobilealerts_cloud end to end (api profile, EntityDescription, translations, icons, tests, quality gate).
argument-hint: "<IDxx prefix, measurement key, platform or action>"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# /add-profile

Input: `$ARGUMENTS`. Read AGENTS.md sections 3–5, docs/DECISIONS.md and the device type in the official spec (https://mobile-alerts.eu/info/public_server_api_documentation.pdf) first. The spec has copy errors (for example "cable sensor" on devices without one); when spec and real data disagree, real anonymised data wins and the deviation is documented.

## 1. API layer (`custom_components/mobilealerts_cloud/api/models.py`)

1. Add or extend the `DeviceProfile` in `_ALL_PROFILES`: `profile_id`, `prefix`, `model_code`, `display_name`, `manufacturer`, and one `ChannelSpec` per entity (`key`, `kind`, `sensor_type`, `translation_key`, `api_key`, `enabled_default`, `inverted`).
2. A new measurement key that unknown prefixes may also send goes into `_DYNAMIC_CHANNEL_SPECS`; alert and metadata keys go into `_METADATA_KEYS`, `_EXACT_ALERT_AND_STATUS_KEYS` or `_ALERT_KEY_SUFFIXES` so the dynamic fallback ignores them.
3. A `ChannelSpec.key`, once released, never changes: it is part of the entity unique ID `{device_id}_{key}` (AGENTS.md section 5.1).
4. No `homeassistant` imports. Add an anonymised device payload to `tests/fixtures/live_responses.json` (invented 12-hex ID with the right prefix, realistic values).

## 2. HA platform

Verify every Home Assistant API you use (entity attributes, device classes, state classes, units, helper signatures) in the installed HA source (`python -c "import homeassistant, os; print(os.path.dirname(homeassistant.__file__))"`), not from memory or older docs.

1. New `sensor_type`: add a frozen description to `SENSOR_DESCRIPTIONS` (`sensor.py`) or `BINARY_SENSOR_DESCRIPTIONS` (`binary_sensor.py`) with `device_class`, `state_class`, unit, `suggested_display_precision`, `entity_category` and a `value_fn` that validates the range and returns `None` for invalid values. No `icon=` (icons come from `icons.json`).
2. New platform: add it to `PLATFORMS` (`const.py`), set `PARALLEL_UPDATES = 0`, use `AddConfigEntryEntitiesCallback`, add entities per subentry with `config_subentry_id`, subclass `MobileAlertsEntity`.
3. Event-like values from a polled API (counters) follow the `event.py` pattern: first value is a baseline, fire only on change, never on a reset to 0.
4. Duplicates and rarely used values get `enabled_default=False` (new installations only).

## 3. Actions (services), if the change adds one

Prefer an entity over an action. Register domain actions in `async_setup` (`services.py`), with a `config_entry_id` field (`config_entry` selector); actions that change state resolve exactly one entry. Add `services.yaml`, `strings.json` `services.<name>`, `icons.json` `services.<name>` and translated `exceptions`. Fields may be added as optional; renaming, removing or making a field required is a breaking change (AGENTS.md section 5.6). Any API request goes through `MobileAlertsClient` and its rate-limit guard.

## 4. Strings, translations, icons

1. `entity.<platform>.<translation_key>.name` (and `state` values for enums) in `strings.json`.
2. Run `/sync-translations` so all 7 locales get the keys.
3. Icon in `icons.json` under `entity.<platform>.<translation_key>`.

## 5. Tests

1. Profile resolution and parsing (`tests/test_models.py`): prefix, override, dynamic fallback, sentinels.
2. Entity values and unavailability from a poll against the mock API (`tests/test_entities.py`); look entities up by unique ID.
3. Update only the affected platform snapshot and review the `.ambr` diff; existing entries must not change unless intended.
4. If the device could exist in the legacy integration, extend `tests/test_migration.py` with the unique ID mapping.

## 6. Finish

1. README "Supported Hardware", `CHANGELOG.md` `[Unreleased]`, `quality_scale.yaml` if a rule's status changed.
2. Run `/check-quality`; everything active must be green.
3. Run `/review` and fix every `blocker` and `major` finding (then `/check-quality` again).
4. If the same check still fails after 3 fix attempts, stop and report the blocker with the failing output.
