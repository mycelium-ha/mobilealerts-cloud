"""The dump anonymiser used by /reproduce-from-dump."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "anonymize_dump.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anonymize_dump", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_anonymize_service_response(tmp_path: Path) -> None:
    """IDs keep their prefix, repeat consistently, and phone IDs disappear."""
    dump = {
        "success": True,
        "data": {
            "hub": {
                "devices": [
                    {
                        "deviceid": "0b1234567890",
                        "phoneid": "100000000001",
                        "measurement": {"ws": 1},
                    },
                    {"deviceid": "0B1234567890", "measurement": {"ws": 2}},
                    {"deviceid": "sensor_1", "measurement": {"t1": 3}},
                ]
            }
        },
    }
    raw = tmp_path / "raw.json"
    out = tmp_path / "anon.json"
    raw.write_text(json.dumps(dump))
    assert _load().main([str(raw), "-o", str(out)]) == 0

    text = out.read_text()
    devices = json.loads(text)["devices"]
    assert [d["deviceid"] for d in devices] == ["0B0000AA0001", "0B0000AA0001"]
    assert devices[0]["measurement"] == {"ws": 1}
    assert "1234567890" not in text
    assert "100000000001" not in text


def test_anonymize_without_devices_fails(tmp_path: Path) -> None:
    """A file without device objects returns a non-zero exit code."""
    raw = tmp_path / "raw.json"
    raw.write_text("{}")
    assert _load().main([str(raw)]) == 1
