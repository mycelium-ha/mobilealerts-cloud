# Mobile Alerts Cloud — Integration Standards & Patterns

Detailed patterns behind the rules in [AGENTS.md](../AGENTS.md). Binding decisions are in [DECISIONS.md](DECISIONS.md). This document describes the current implementation.

Verify every Home Assistant API mentioned here against the installed HA source before relying on it. The previous version of this file contained a "deprecation tracker" with entries that do not exist in Home Assistant (for example `native_*_temperature` for climate entities); it was removed.

---

## 1. Lifecycle

1. `async_setup` registers the domain services once (`services.py`). Services resolve their target from `config_entry_id` and raise a translated `ServiceValidationError` if it is missing, ambiguous or not loaded.
2. `async_setup_entry`:
   - create `MobileAlertsClient` with `async_get_clientsession(hass)` and the instance-wide `RateLimitState` (ADR-009)
   - collect sensor IDs from the `sensor` subentries, resolve each profile (prefix, override, dynamic fallback)
   - first refresh: serve readings younger than 60 s from the reload cache, fetch the rest; API errors → translated `ConfigEntryNotReady`
   - remove entities a profile change made obsolete (`_prune_stale_entities`)
   - register the hub device (`DeviceEntryType.SERVICE`, `hub_{entry_id}`), keep its id on `runtime.hub_device_id`
   - store `MobileAlertsCloudRuntime` on `entry.runtime_data`, forward platforms
3. `async_unload_entry` unloads the platforms; services stay registered (they belong to `async_setup`).
4. Reloads have one owner: the update listener calls `hass.config_entries.async_schedule_reload`; flows use `async_update_and_abort`. `runtime.suppress_reload` prevents reload storms while the migration adds many subentries.
5. `async_remove_config_entry_device` refuses to remove the hub and devices that still have a subentry.

## 2. Hub, subentries, devices

| Level | Identifier | Name | Linked via |
|---|---|---|---|
| Hub (service device) | `hub_{entry_id}` | entry title | — |
| Sensor device | `(mobilealerts_cloud, device_id)` | subentry title (+ optional name prefix) | hub (`via_device_id`) |

- One subentry per sensor (`SUBENTRY_TYPE_SENSOR`, `unique_id` = sensor ID, unique across all hubs, ADR-004). Entities are added with `config_subentry_id`, so deleting a subentry removes its device and entities.
- Entity `unique_id`: `{device_id}_{channel_key}`, plus `{device_id}_battery` and `{device_id}_last_seen` for every sensor. Never change these (AGENTS.md §5.1).
- Profiles (`api/models.py`): `DeviceProfile` with `ChannelSpec`s (`key`, `kind`, `sensor_type`, `translation_key`, `api_key`, `enabled_default`, `inverted`). The prefix of the sensor ID selects the profile; a subentry may override it (`ID04_CABLE`); unknown prefixes get a dynamic profile from the measurement keys.
- Device info: `model` = product name of the profile (`display_name`), `model_id` = model code (`model_code`).

## 3. API layer

- `api/` imports only stdlib and `aiohttp`; an AST test enforces this.
- Transport per spec: `POST`, `application/x-www-form-urlencoded`, `deviceids` comma-separated, all sensors of a hub in one request.
- Local validation before any request: sensor IDs `^[0-9A-F]{12}$`; invalid input never reaches the server (IP block after more than 5 invalid calls in 15 minutes).
- Rate-limit guard per sensor: sliding 60 s window, at most 3 calls; HTTP 429 → 7 min cooldown for the queried sensors; HTTP 403 → 15 min IP cooldown. One `RateLimitState` guards the whole Home Assistant instance (every hub, flow, action and the migration) and survives reloads (ADR-007, ADR-009). A phone ID the API rejected with HTTP 400 is remembered by value and no longer sent.
- The endpoint is fixed (`DEFAULT_API_URL`); only tests pass another URL.
- Exceptions: `MobileAlertsConnectionError` (network, timeout, invalid JSON), `MobileAlertsApiError` (HTTP status or `success: false`), `MobileAlertsRateLimitError` / `MobileAlertsBlockedError` (with `retry_after`), `MobileAlertsValidationError` (local input).
- Parser: `lastseen`, `measurement.ts` and `measurement.c` → `last_seen = max(lastseen, c)`; battery from `lowbat`, `lowbattery` or `lb`; sentinels 43530, 65295 and 65296 → `None`.
- The client requires an injected `aiohttp.ClientSession` (HA's shared session); it never creates its own.

## 4. Coordinator and availability

- One `DataUpdateCoordinator` per hub; default interval 10 min (1–1440). Sensors transmit about every 7 min, so shorter intervals rarely add data.
- On a rate-limit cooldown the coordinator keeps the last readings (entities stay available until the stale-sensor rule applies) and logs one warning, then one info when polls work again. Without any data yet, and for every other API error, it raises a translated `UpdateFailed` (with `retry_after` for rate limits and IP blocks).
- A sensor omitted from one response keeps its last reading. Entities become unavailable (ADR-005) when `last_seen` is older than max(60 min, 3 × interval); `last_seen` itself stays available. After 24 h without data a `sensor_missing` repair issue is raised and deleted again once the sensor reports.

## 5. Entities

- `sensor`: temperature, humidity (incl. ID12 averages), pressure, CO₂, rain (`TOTAL_INCREASING`), rain flips, wind speed and gust, wind direction (enum and degrees), key press counters, diagnostic `last_seen`.
- `binary_sensor`: diagnostic battery, water (moisture), contact (opening), AC power (power, inverted `t2`), smoke 1–4.
- `event`: ID15 buttons; an event fires only when the running counter `kpXc` changes after a first baseline, never on a reset to 0 after a battery change. The bus event `mobilealerts_cloud_key_press` is public API.
- Icons in `icons.json`, `SensorDeviceClass.WIND_DIRECTION` for degrees, disabled by default for duplicates and rarely used values (new installations only).
- Never put raw API JSON into `extra_state_attributes`; use diagnostics and `dump_raw_response`.

## 6. Errors, translations, icons, repairs

- `strings.json` sections: `config`, `config_subentries`, `options`, `entity`, `services`, `exceptions` and `issues`. All 7 locales mirror it exactly (`scripts/check_translations.py`).
- Exceptions: `raise HomeAssistantError(translation_domain=DOMAIN, translation_key="...", translation_placeholders={...})`.
- Repair issues only for persistent problems the user must act on, deleted once resolved. Today: `sensor_missing` (a sensor not returned by the API for 24 h).

## 7. Diagnostics

- Config entry and device diagnostics redact sensor IDs and phone IDs, including partial IDs in titles and rate-limit keys, with consistent aliases (`sensor_1`); a test asserts that no real ID remains.
- `dump_raw_response` returns unredacted payloads for local debugging only.

## 8. Legacy migration (`migrate_from_legacy`)

Recorder history, statistics, dashboards and helpers bind to `entity_id`. Invariants, covered by `tests/test_migration.py`:

1. `dry_run: true` is strictly read-only and makes no API calls.
2. `cutover: false` imports missing sensors and copies device `name_by_user`, areas, labels, entity names, icons, aliases, user-set `disabled_by`/`hidden_by`, Assist/Google/Alexa exposure and explicit display precision, without touching the legacy entries.
3. `cutover: true` imports missing sensors first and removes a legacy device only after its Mobile Alerts Cloud subentry and device exist; it renames same-domain Mobile Alerts Cloud entities onto the legacy `entity_id`s and applies entity registry options **after** renaming, using the new `entity_id`.
4. Only `disabled_by`/`hidden_by` with value `user` are copied.
5. Never rename across domains (`sensor.*` → `binary_sensor.*`/`event.*`); copy metadata and warn instead.
6. `runtime.suppress_reload = True` while adding many subentries, then one reload.

## 9. Packaging (Hassfest, HACS / Marketplace)

- `manifest.json` key order: `domain`, `name`, then alphabetical. `version` is SemVer and matches `pyproject.toml`, `CHANGELOG.md` and the `vX.Y.Z` tag. `integration_type: hub`, `iot_class: cloud_polling`, `categories: ["weather_alerts"]`, no `requirements`.
- `hacs.json`: `name`, `render_readme: true`, `homeassistant` = minimum version (ADR-001).
- `brand/` holds `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png`.
- From HA 2026.11, HACS is part of Core as **Home Assistant Marketplace**; document both install paths.
