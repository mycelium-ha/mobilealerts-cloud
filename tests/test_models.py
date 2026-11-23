"""Tests for Mobile Alerts Cloud device profiles, parser, validation, and sentinels."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.mobilealerts_cloud.api import (
    PROFILES_BY_PREFIX,
    is_invalid_measurement_value,
    is_valid_device_id,
    is_valid_phone_id,
    parse_device_ids_text,
    parse_device_reading,
    resolve_device_profile,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "live_responses.json"


def _load_fixtures() -> dict:
    with FIXTURES_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_all_twenty_documented_prefixes_supported() -> None:
    """Every prefix ID01..ID20 documented in the PDF resolves deterministically."""
    expected_prefixes = {
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09",
        "0A",
        "0B",
        "0E",
        "0F",
        "10",
        "11",
        "12",
        "15",
        "17",
        "18",
        "20",
    }
    assert set(PROFILES_BY_PREFIX) == expected_prefixes

    fixtures = _load_fixtures()["all_documented_profiles"]
    for prefix_key, raw in fixtures.items():
        reading = parse_device_reading(raw)
        profile = resolve_device_profile(reading.device_id, reading=reading)
        assert profile.profile_id == prefix_key


def test_only_rain_gauge_declares_transmit_interval() -> None:
    """The MA 10650 rain gauge transmits about every 2 hours (ADR-010); other profiles do not."""
    rain = resolve_device_profile("080000AABB08")
    assert rain.profile_id == "ID08"
    assert rain.transmit_interval_seconds == 7200
    assert resolve_device_profile("010000AABB01").transmit_interval_seconds is None
    dynamic = resolve_device_profile("990000AABB99")
    assert dynamic.transmit_interval_seconds is None


def test_id04_default_vs_cable_override() -> None:
    """ID04 defaults to water binary sensor and supports ID04_CABLE temperature override."""
    raw = _load_fixtures()["all_documented_profiles"]["ID04"]
    reading = parse_device_reading(raw)

    default_profile = resolve_device_profile(reading.device_id, reading=reading)
    assert default_profile.profile_id == "ID04"
    assert any(ch.key == "water" and ch.kind == "binary_sensor" for ch in default_profile.channels)

    cable_profile = resolve_device_profile(
        reading.device_id, model_override="ID04_CABLE", reading=reading
    )
    assert cable_profile.profile_id == "ID04_CABLE"
    assert any(
        ch.key == "t2" and ch.kind == "sensor" and ch.translation_key == "cable_temperature"
        for ch in cable_profile.channels
    )


def test_dynamic_fallback_ignores_metadata_and_alerts() -> None:
    """Unknown prefixes dynamically map measurement keys and ignore alert flags."""
    raw = {
        "deviceid": "990011223344",
        "lastseen": 1795118000,
        "lowbat": False,
        "measurement": {
            "idx": 500,
            "ts": 1795117990,
            "c": 1795118000,
            "sc": True,
            "t1": 22.5,
            "t1hi": True,
            "t1his": 20.0,
            "h": 48.0,
            "hhi": False,
            "ap": 1015.2,
            "apcr": False,
        },
    }
    reading = parse_device_reading(raw)
    profile = resolve_device_profile(reading.device_id, reading=reading)
    assert profile.profile_id == "DYNAMIC_99"
    assert [ch.key for ch in profile.channels] == ["t1", "h", "ap"]


def test_live_shapes_battery_and_last_seen_precedence() -> None:
    """Verify battery precedence (lowbat/lowbattery/lb) and max(lastseen, c) timestamps."""
    live = _load_fixtures()["live_captured_shapes"]

    # ID11 has top-level lowbattery only (no measurement.lb)
    r11 = parse_device_reading(live["ID11"])
    assert r11.low_battery is False
    assert r11.last_seen == 1795117842

    # ID08 has lastseen significantly newer than measurement.c
    r08 = parse_device_reading(live["ID08"])
    assert r08.last_seen == 1795114158
    assert r08.values["r"] == 1196.862
    assert r08.values["rf"] == 4639
    assert r08.values["rr"] == 0.258

    # Fallback to measurement.ts when both lastseen and c are absent
    r_ts = parse_device_reading(
        {
            "deviceid": "020000112233",
            "measurement": {"idx": 1, "ts": 1700000100, "lb": "true", "t1": 19.5},
        }
    )
    assert r_ts.last_seen == 1700000100
    assert r_ts.low_battery is True


def test_sentinel_filtering_and_bool_preservation() -> None:
    """Sentinels 43530/65295/65296 become None while booleans are never treated as sentinels."""
    assert is_invalid_measurement_value(43530) is True
    assert is_invalid_measurement_value(65295.0) is True
    assert is_invalid_measurement_value(65296) is True
    assert is_invalid_measurement_value(float("nan")) is True
    assert is_invalid_measurement_value(False) is False
    assert is_invalid_measurement_value(True) is False
    assert is_invalid_measurement_value(0) is False

    raw = _load_fixtures()["edge_cases"]["sentinels_and_lowbat"]
    reading = parse_device_reading(raw)
    assert reading.low_battery is True
    assert reading.values["t1"] == 21.0
    assert reading.values["t2"] is None
    assert reading.values["t3"] is None
    assert reading.values["t4"] is None
    assert reading.values["h1"] == 50.0
    assert reading.values["h2"] is None
    assert reading.values["h3"] is None
    assert reading.values["h4"] == 55.0


def test_validation_and_parse_device_ids_text() -> None:
    """Verify device_id/phone_id validators and multi-ID text parser."""
    assert is_valid_device_id(" 0100005b2e9d ") is True
    assert is_valid_device_id("014CB1C85C6") is False
    assert is_valid_device_id("ZZZZZZZZZZZZ") is False
    assert is_valid_phone_id("100000000001") is True
    assert is_valid_phone_id("123") is False

    valid, invalid = parse_device_ids_text("0100005b2e9d, 0800009D3B7E\n0100005B2E9D; badid 1234")
    assert valid == ["0100005B2E9D", "0800009D3B7E"]
    assert invalid == ["badid", "1234"]
