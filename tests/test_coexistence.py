"""Tests verifying metadata integrity, localization parity and domain isolation."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import yaml

from custom_components.mobilealerts_cloud.api import (
    PROFILES_BY_ID,
    WIND_DIRECTIONS,
)
from custom_components.mobilealerts_cloud.const import (
    DOMAIN,
    EVENT_KEY_PRESS,
    SERVICE_DUMP_RAW_RESPONSE,
    SERVICE_FORCE_REFRESH,
    SERVICE_MIGRATE_FROM_LEGACY,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
COMP_DIR = ROOT_DIR / "custom_components" / "mobilealerts_cloud"


def test_manifest_hacs_and_brand_assets() -> None:
    """Verify manifest.json key order, hacs.json, and brand PNG files."""
    manifest = json.loads((COMP_DIR / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT_DIR / "hacs.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == "mobilealerts_cloud"
    assert list(manifest.keys()) == [
        "domain",
        "name",
        *sorted(k for k in manifest if k not in ("domain", "name")),
    ]
    assert manifest["integration_type"] == "hub"
    assert manifest["categories"] == ["weather_alerts"]
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    assert hacs["render_readme"] is True

    for png in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
        path = COMP_DIR / "brand" / png
        assert path.is_file()
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_translations_and_services_yaml_parity() -> None:
    """Verify strings.json, all 7 translations, and services.yaml are valid and complete."""
    services = yaml.safe_load((COMP_DIR / "services.yaml").read_text(encoding="utf-8"))
    assert set(services) == {
        SERVICE_DUMP_RAW_RESPONSE,
        SERVICE_FORCE_REFRESH,
        SERVICE_MIGRATE_FROM_LEGACY,
    }

    expected_sensor_keys = {
        ch.translation_key
        for profile in PROFILES_BY_ID.values()
        for ch in profile.channels
        if ch.kind == "sensor"
    } | {"last_seen"}
    expected_binary_keys = {
        ch.translation_key
        for profile in PROFILES_BY_ID.values()
        for ch in profile.channels
        if ch.kind == "binary_sensor"
    } | {"battery"}
    expected_event_keys = {
        ch.translation_key
        for profile in PROFILES_BY_ID.values()
        for ch in profile.channels
        if ch.kind == "event"
    }

    strings_data = json.loads((COMP_DIR / "strings.json").read_text(encoding="utf-8"))

    for rel in (
        "strings.json",
        "translations/en.json",
        "translations/de.json",
        "translations/nl.json",
        "translations/fr.json",
        "translations/it.json",
        "translations/es.json",
        "translations/pl.json",
    ):
        data = json.loads((COMP_DIR / rel).read_text(encoding="utf-8"))
        assert set(data) == set(strings_data), f"Top-level mismatch in {rel}"

        sub_sensor = data["config_subentries"]["sensor"]
        assert isinstance(sub_sensor["entry_type"], str) and sub_sensor["entry_type"]
        assert "user" in sub_sensor["initiate_flow"]
        assert "reconfigure" in sub_sensor["initiate_flow"]

        sensor_trans = data["entity"]["sensor"]
        assert expected_sensor_keys.issubset(sensor_trans), f"Missing sensor keys in {rel}"
        assert set(sensor_trans["wind_direction"]["state"]) == set(WIND_DIRECTIONS)

        binary_trans = data["entity"]["binary_sensor"]
        assert expected_binary_keys.issubset(binary_trans), f"Missing binary keys in {rel}"

        event_trans = data["entity"]["event"]
        assert expected_event_keys.issubset(event_trans), f"Missing event keys in {rel}"
        for evt_key in expected_event_keys:
            states = event_trans[evt_key]["state_attributes"]["event_type"]["state"]
            assert set(states) == {"short_press", "double_press", "long_press"}

        for svc_name, svc_def in services.items():
            assert svc_name in data["services"]
            for field_name in svc_def.get("fields", {}):
                assert field_name in data["services"][svc_name]["fields"]


def test_api_layer_has_no_homeassistant_imports() -> None:
    """Keep api/ pure Python with zero homeassistant imports."""
    for py_file in (COMP_DIR / "api").glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("homeassistant")
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("homeassistant")


def test_no_collision_with_legacy_mobile_alerts() -> None:
    """Ensure domain, identifiers, events, and modules never collide with mobile_alerts."""
    assert DOMAIN == "mobilealerts_cloud"
    assert DOMAIN != "mobile_alerts"
    assert EVENT_KEY_PRESS != "mobile_alerts_event"

    for module_name in (
        "custom_components.mobilealerts_cloud",
        "custom_components.mobilealerts_cloud.api",
        "custom_components.mobilealerts_cloud.config_flow",
        "custom_components.mobilealerts_cloud.const",
        "custom_components.mobilealerts_cloud.binary_sensor",
        "custom_components.mobilealerts_cloud.coordinator",
        "custom_components.mobilealerts_cloud.diagnostics",
        "custom_components.mobilealerts_cloud.entity",
        "custom_components.mobilealerts_cloud.event",
        "custom_components.mobilealerts_cloud.migration",
        "custom_components.mobilealerts_cloud.sensor",
    ):
        assert importlib.import_module(module_name) is not None
