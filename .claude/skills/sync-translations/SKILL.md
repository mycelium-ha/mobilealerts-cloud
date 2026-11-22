---
name: sync-translations
description: Reconcile strings.json with all 7 locale files (de, en, es, fr, it, nl, pl) of mobilealerts_cloud, add missing translations, remove orphaned keys and fix placeholder mismatches.
allowed-tools: Bash, Read, Edit, Write
context: fork
agent: translator
background: false
---

# /sync-translations

`custom_components/mobilealerts_cloud/strings.json` is the source. The locales live in `custom_components/mobilealerts_cloud/translations/{de,en,es,fr,it,nl,pl}.json`.

## Steps

1. Run `python3 scripts/check_translations.py --json` and read the findings per locale. All sections count: `config`, `config_subentries`, `options`, `entity` (names and `state` values, for example wind directions and event types), `services`, `exceptions` and `issues`.
2. **Missing keys:** add them in the same position as in `strings.json`.
   - `en.json`: resolved English text. Where `strings.json` uses `[%key:common::...%]`, write the English text HA uses for that common key.
   - Other locales: translate the English text. Reuse the terminology already used in that file (read neighbouring keys first) and the manufacturer's terms (Mobile Alerts / TFA WeatherHub apps, for example German "Sensor-ID", "Regenmesser", "Windmesser"). Keep the tone of HA's own translations: short labels, sentence case, no trailing period in names.
3. **Orphaned keys:** delete them from the locale.
4. **Placeholders:** every `{placeholder}` of the English text must appear unchanged in each translation; never translate placeholder names.
5. Keep JSON formatting: 2-space indent, UTF-8 (`ensure_ascii=False`), trailing newline, key order identical to `strings.json`. When many keys change, edit with a small script that loads and dumps the JSON, then check that `git diff` shows only the intended keys.
6. Never edit `strings.json` in this skill unless the user asked for a source change; if a source string is wrong, report it.
7. Verify: `python3 scripts/check_translations.py` must print `OK`, and `pytest -q tests/test_coexistence.py` must pass.

## Report

List per locale how many keys were added, removed and fixed, and quote any translation you were unsure about so the maintainer can check it.
