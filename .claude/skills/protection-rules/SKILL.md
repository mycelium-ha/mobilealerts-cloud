---
name: protection-rules
description: Binding protection rules for mobilealerts_cloud files whose mistakes break users (migration, rate-limit guard, identifiers, translations, public action schemas). Loads automatically when such a file is touched.
user-invocable: false
paths:
  - custom_components/mobilealerts_cloud/migration.py
  - custom_components/mobilealerts_cloud/api/client.py
  - custom_components/mobilealerts_cloud/entity.py
  - custom_components/mobilealerts_cloud/__init__.py
  - custom_components/mobilealerts_cloud/api/models.py
  - custom_components/mobilealerts_cloud/services.py
  - custom_components/mobilealerts_cloud/services.yaml
  - custom_components/mobilealerts_cloud/strings.json
  - custom_components/mobilealerts_cloud/translations/*.json
  - tests/fixtures/*.json
---

# Protection rules (AGENTS.md section 5, condensed)

You are editing a file where a mistake breaks existing installations. Before you change it:

- **Identifiers** (`entity.py`, `__init__.py`, `api/models.py` `ChannelSpec.key`): entity `unique_id` `{device_id}_{channel_key}`, device identifiers `(mobilealerts_cloud, device_id)` and `hub_{entry_id}`, subentry unique ID `device_id` never change without a registry migration and a test proving it.
- **`migration.py`**: only with green `tests/test_migration.py` and `tests/test_coexistence.py` plus a new test for the changed behaviour; keep the safety invariants in docs/HA_INTEGRATION_STANDARDS.md section 8. No legacy `mobile_alerts` domain use outside it.
- **`api/client.py`**: never loosen the rate-limit guard or send a request around it. One `RateLimitState` per Home Assistant instance (ADR-009). Error messages and logs show sensor IDs only as `mask_id` (`...BB02`).
- **`services.py` / `services.yaml`**: fields may only be added as optional; renaming, removing or requiring a field is a breaking change with a CHANGELOG entry.
- **`strings.json` / `translations/`**: `strings.json` is the source; run `/sync-translations` so all 7 locales stay key- and placeholder-identical.
- **Fixtures**: only anonymised IDs (`<prefix>0000xxxxxx`, `scripts/anonymize_dump.py`); never a real sensor or phone ID. The commit hook rejects others.

Finish with `/check-quality` and `/review`.
