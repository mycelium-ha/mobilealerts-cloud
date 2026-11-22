#!/usr/bin/env python3
"""Check `strings.json` against all locale files in `translations/`.

Reports, per locale:
- missing keys (present in strings.json, absent in the locale)
- orphaned keys (present in the locale, absent in strings.json)
- placeholder mismatches (`{name}` sets differ from the English source)
- untranslated values (identical to English in a non-English locale; warning only)

Exit code 1 if any missing/orphaned key or placeholder mismatch is found.
Usage: python3 scripts/check_translations.py [--json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

COMP_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "mobilealerts_cloud"
LOCALES = ("en", "de", "es", "fr", "it", "nl", "pl")
PLACEHOLDER = re.compile(r"(?<!\{)\{([a-zA-Z0-9_]+)\}(?!\})")
KEY_REF = re.compile(r"^\[%key:[^%]+%\]$")


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested translation dicts into dotted keys."""
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    else:
        out[prefix] = str(data)
    return out


def placeholders(value: str) -> set[str]:
    return set(PLACEHOLDER.findall(value))


def main() -> int:
    as_json = "--json" in sys.argv
    source = flatten(json.loads((COMP_DIR / "strings.json").read_text(encoding="utf-8")))
    english = flatten(
        json.loads((COMP_DIR / "translations" / "en.json").read_text(encoding="utf-8"))
    )
    report: dict[str, dict[str, list[str]]] = {}
    failed = False

    for locale in LOCALES:
        path = COMP_DIR / "translations" / f"{locale}.json"
        if not path.is_file():
            report[locale] = {"missing_file": [str(path)]}
            failed = True
            continue
        data = flatten(json.loads(path.read_text(encoding="utf-8")))
        missing = sorted(set(source) - set(data))
        orphaned = sorted(set(data) - set(source))
        mismatched = []
        untranslated = []
        for key in sorted(set(source) & set(data)):
            reference = english.get(key, source[key])
            if KEY_REF.match(source[key]) and locale != "en":
                reference = english.get(key, "")
            if placeholders(reference) != placeholders(data[key]):
                mismatched.append(key)
            if (
                locale != "en"
                and data[key] == english.get(key)
                and not PLACEHOLDER.fullmatch(data[key])
                and len(data[key]) > 3
            ):
                untranslated.append(key)
        report[locale] = {
            "missing": missing,
            "orphaned": orphaned,
            "placeholder_mismatch": mismatched,
            "untranslated_warning": untranslated,
        }
        failed |= bool(missing or orphaned or mismatched)

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for locale, findings in report.items():
            counts = ", ".join(f"{k}={len(v)}" for k, v in findings.items())
            print(f"[{locale}] {counts}")
            for kind, keys in findings.items():
                if kind == "untranslated_warning":
                    continue
                for key in keys:
                    print(f"    {kind}: {key}")
        print("FAIL" if failed else "OK: all locales in sync with strings.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
