# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-22

First release of Mobile Alerts Cloud, a Home Assistant integration for Technoline Mobile Alerts and TFA Dostmann WeatherHub sensors, built on the official Mobile Alerts REST API. Requires Home Assistant 2026.9.0 or newer.

### Added
- **Setup in the UI:** one hub with a sensor entry per Mobile Alerts sensor; options, reconfiguration, and adding or removing sensors without a restart. Several hubs are possible, for example with different polling intervals; a sensor ID can belong to only one hub.
- **All documented sensor types** (`ID01` to `ID20`), detected by their ID prefix, with a per-sensor profile override and a fallback for unknown prefixes.
- **Native entities:** temperature, humidity and humidity averages, pressure, CO₂, rain (total, increment, flip counter), wind speed, gust and direction, and `last_seen`; binary sensors for battery, water, contact, mains power and smoke; event entities for the buttons of the 4-channel wireless switch, plus the `mobilealerts_cloud_key_press` event.
- **Reliable polling:** one batched request per poll for all sensors of a hub. The client enforces the API limits for the whole Home Assistant instance (3 requests per sensor per minute, local ID validation, automatic waiting during a rate-limit or IP block).
- **Availability:** entities become unavailable when a sensor stops reporting (after 60 minutes or three poll intervals); `last_seen` stays available. A repair issue appears when the API has not returned a sensor for 24 hours and clears itself when it returns.
- **Switching from `mobile_alerts`:** runs side by side with the `mobile_alerts` integration, imports its sensors during setup, and the `migrate_from_legacy` action takes over names, areas, settings and history ([migration guide](docs/MIGRATION.md)).
- **Actions:** `force_refresh`, `dump_raw_response` and `migrate_from_legacy`, each selecting a hub with `config_entry_id`.
- **Diagnostics that are safe to share:** phone IDs are removed and every sensor ID is replaced by an alias; log and error messages never contain full sensor IDs.
- **Translations** in English, German, Spanish, French, Italian, Dutch and Polish, including entity names, errors and repair issues.
- **Documentation:** setup fields and options, how data is updated, use cases and automation examples, every action, known limitations, troubleshooting and removal.
