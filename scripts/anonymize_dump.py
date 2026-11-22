#!/usr/bin/env python3
"""Turn a Mobile Alerts dump into anonymised fixture devices.

Accepts the response of `mobilealerts_cloud.dump_raw_response`, a raw API response
(`{"devices": [...]}`), a diagnostics download or a plain list of device objects. Every
device object (a dict with `deviceid` and `measurement`) is copied with:

- `deviceid` replaced by a fixture ID that keeps the two-character profile prefix
  (`<prefix>0000AA<nnnn>`), because the prefix selects the device profile;
- `phoneid` and any other key that can identify the user removed.

Measurements and timestamps stay as they are: they are what the integration parses.
Usage: `python3 scripts/anonymize_dump.py raw.json -o anon.json`. A summary goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEVICE_ID_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
DROP_KEYS = {"phoneid", "phone_id"}


def _iter_devices(data: Any) -> list[dict[str, Any]]:
    """Return every device object found anywhere in the dump."""
    found: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if isinstance(data.get("deviceid"), str) and isinstance(data.get("measurement"), dict):
            found.append(data)
        else:
            for value in data.values():
                found.extend(_iter_devices(value))
    elif isinstance(data, list):
        for item in data:
            found.extend(_iter_devices(item))
    return found


def anonymize(data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return anonymised device objects and a summary of what was changed."""
    devices: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    dropped: set[str] = set()
    skipped = 0
    for raw in _iter_devices(data):
        raw_id = str(raw["deviceid"]).upper()
        if not DEVICE_ID_RE.match(raw_id):
            # Diagnostics already replaced the ID with an alias; the prefix is lost.
            skipped += 1
            continue
        if raw_id not in id_map:
            id_map[raw_id] = f"{raw_id[:2]}0000AA{len(id_map) + 1:04X}"
        clean = {k: v for k, v in raw.items() if k not in DROP_KEYS}
        dropped |= set(raw) & DROP_KEYS
        clean["deviceid"] = id_map[raw_id]
        devices.append(clean)
    summary = {
        "devices": len(devices),
        "prefixes": sorted({dev_id[:2] for dev_id in id_map}),
        "dropped_keys": sorted(dropped),
        "skipped_without_real_id": skipped,
    }
    return devices, summary


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", type=Path, help="raw dump JSON (keep it outside the repository)")
    parser.add_argument("-o", "--output", type=Path, help="output file (default: stdout)")
    args = parser.parse_args(argv)

    devices, summary = anonymize(json.loads(args.dump.read_text(encoding="utf-8")))
    output = json.dumps({"devices": devices}, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    print(json.dumps(summary), file=sys.stderr)
    return 0 if devices else 1


if __name__ == "__main__":
    raise SystemExit(main())
