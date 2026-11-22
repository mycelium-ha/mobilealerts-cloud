"""Value parsing of the platforms and the payload parser (pure functions, no HA instance)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.mobilealerts_cloud.api import ChannelSpec, parse_device_reading
from custom_components.mobilealerts_cloud.binary_sensor import _extract_water, _parse_binary_state
from custom_components.mobilealerts_cloud.event import _extract_counter_and_type
from custom_components.mobilealerts_cloud.sensor import (
    _extract_last_seen,
    _extract_wind_degrees,
    _extract_wind_direction,
    _parse_float,
    _parse_int,
)

WATER = ChannelSpec("water", "binary_sensor", "water", "water", "t2")
WIND = ChannelSpec("wd", "sensor", "wind_direction", "wind_direction", "wd")


def _reading(measurement: dict[str, Any], **top: Any) -> Any:
    return parse_device_reading({"deviceid": "040000AABB04", "measurement": measurement, **top})


@pytest.mark.parametrize(
    ("raw", "inverted", "expected"),
    [
        (None, False, None),
        (True, False, True),
        (0, False, False),
        (2.5, False, True),
        (" Yes ", False, True),
        ("off", False, False),
        ("maybe", False, None),
        ([1], False, None),
        (0, True, True),
        ("on", True, False),
    ],
)
def test_parse_binary_state(raw: Any, inverted: bool, expected: bool | None) -> None:
    """Binary values accept bool, numbers and common strings; anything else is unknown."""
    assert _parse_binary_state(raw, inverted=inverted) is expected


def test_extract_water_falls_back_to_w() -> None:
    """Water uses t2, falls back to `w`, and is unknown when neither exists."""
    assert _extract_water(_reading({"t2": 1}), WATER) is True
    assert _extract_water(_reading({"w": 0}), WATER) is False
    assert _extract_water(_reading({"t1": 20.0}), WATER) is None


@pytest.mark.parametrize(
    ("raw", "kwargs", "expected"),
    [
        (None, {}, None),
        (True, {}, None),
        ("abc", {}, None),
        ([1], {}, None),
        (float("nan"), {}, None),
        ("21.5", {}, 21.5),
        (-0.5, {"min_value": 0.0}, None),
        (0.0, {"min_value": 0.0, "strict_min": True}, None),
        (101.0, {"max_value": 100.0}, None),
    ],
)
def test_parse_float(raw: Any, kwargs: dict[str, Any], expected: float | None) -> None:
    """Floats must be finite and inside the bounds."""
    assert _parse_float(raw, **kwargs) == expected


@pytest.mark.parametrize(
    ("raw", "kwargs", "expected"),
    [
        (None, {}, None),
        (False, {}, None),
        (float("inf"), {}, None),
        ("x", {}, None),
        ("7", {}, 7),
        (-1, {"min_value": 0}, None),
        (16, {"max_value": 15}, None),
    ],
)
def test_parse_int(raw: Any, kwargs: dict[str, Any], expected: int | None) -> None:
    """Integers must parse and stay inside the bounds."""
    assert _parse_int(raw, **kwargs) == expected


def test_wind_direction_out_of_range_is_unknown() -> None:
    """Wind direction indexes outside 0..15 are unknown, in both representations."""
    reading = _reading({"wd": 16})
    assert _extract_wind_direction(reading, WIND) is None
    assert _extract_wind_degrees(reading, WIND) is None
    assert _extract_wind_degrees(_reading({"wd": 4}), WIND) == 90.0


def test_extract_last_seen() -> None:
    """lastseen becomes an aware datetime; missing or absurd values are unknown."""
    assert _extract_last_seen(_reading({}, lastseen=1795114158), WIND) == datetime(
        2026, 11, 19, 18, 49, 18, tzinfo=UTC
    )
    assert _extract_last_seen(_reading({}), WIND) is None
    assert _extract_last_seen(_reading({}, lastseen=10**20), WIND) is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"kp1c": 3, "kp1t": 2}, (3, 2)),
        ({"kp1c": "4", "kp1t": "1"}, (4, 1)),
        ({"kp1c": True, "kp1t": False}, (None, None)),
        ({"kp1c": "x", "kp1t": [1]}, (None, None)),
        ({}, (None, None)),
    ],
)
def test_extract_counter_and_type(values: dict[str, Any], expected: tuple[Any, Any]) -> None:
    """Counter and press type are integers; booleans and garbage are ignored."""
    assert _extract_counter_and_type(values, "kp1") == expected


@pytest.mark.parametrize(
    ("top", "measurement", "expected"),
    [
        ({"lowbat": "no"}, {}, False),
        ({"lowbattery": 1}, {}, True),
        ({}, {"lb": "true"}, True),
        ({"lowbat": "unclear"}, {"lowbattery": 0.0}, False),
        ({"lowbat": [1]}, {}, None),
    ],
)
def test_low_battery_sources(
    top: dict[str, Any], measurement: dict[str, Any], expected: bool | None
) -> None:
    """Low battery is read from the first parsable of lowbat, lowbattery, lb."""
    assert _reading(measurement, **top).low_battery is expected


@pytest.mark.parametrize(
    ("top", "measurement", "expected"),
    [
        ({"lastseen": 100}, {"c": 200}, 200),
        ({"lastseen": "300"}, {}, 300),
        ({}, {"c": 400.0}, 400),
        ({}, {"ts": 500}, 500),
        ({"lastseen": True}, {"ts": "abc"}, None),
        ({"lastseen": -5}, {"c": float("nan")}, None),
        ({"lastseen": "-1"}, {}, None),
    ],
)
def test_last_seen_sources(
    top: dict[str, Any], measurement: dict[str, Any], expected: int | None
) -> None:
    """lastseen is the newer of lastseen and c, else ts; invalid timestamps are ignored."""
    assert _reading(measurement, **top).last_seen == expected
