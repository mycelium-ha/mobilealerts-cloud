"""Device profiles, validation helpers, and API payload parser for Mobile Alerts Cloud."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

DEVICE_ID_RE = re.compile(r"^[0-9A-F]{12}$")
PHONE_ID_RE = re.compile(r"^\d{6,20}$")

# Special sentinel values from Mobile Alerts Public Server API specification (page 3)
SPECIAL_VALUE_NOT_CONNECTED = 43530
SPECIAL_VALUE_OUT_OF_RANGE = 65295
INVALID_MEASUREMENT_VALUES: frozenset[int | float] = frozenset(
    {
        SPECIAL_VALUE_NOT_CONNECTED,
        float(SPECIAL_VALUE_NOT_CONNECTED),
        SPECIAL_VALUE_OUT_OF_RANGE,
        float(SPECIAL_VALUE_OUT_OF_RANGE),
        65296,
    }
)

WIND_DIRECTIONS: tuple[str, ...] = (
    "n",
    "nne",
    "ne",
    "ene",
    "e",
    "ese",
    "se",
    "sse",
    "s",
    "ssw",
    "sw",
    "wsw",
    "w",
    "wnw",
    "nw",
    "nnw",
)

KEY_PRESS_TYPES: dict[int, str] = {
    1: "short_press",
    2: "double_press",
    3: "long_press",
}

_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "idx",
        "ts",
        "c",
        "lb",
        "lowbattery",
        "sc",
    }
)

_EXACT_ALERT_AND_STATUS_KEYS: frozenset[str] = frozenset(
    {
        "rb",
        "wds",
        "wo",
        "woa",
        "wc",
        "wca",
        "wot",
        "wota",
        "wots",
        "wct",
        "wcta",
        "wcts",
        "apcr",
        "apcrdt",
        "apcrt",
        "apcf",
        "apcfdt",
        "apcft",
        "a1",
        "a2",
        "a3",
        "a4",
        "b",
    }
)

_ALERT_KEY_SUFFIXES: tuple[str, ...] = (
    "hi",
    "lo",
    "hise",
    "lose",
    "hiee",
    "loee",
    "his",
    "los",
    "hist",
    "lost",
    "aactive",
    "active",
    "nsm",
    "gt",
    "gr",
    "as",
)


def normalize_device_id(raw: str) -> str:
    """Normalize a Mobile Alerts sensor ID to trimmed uppercase."""
    return raw.strip().upper()


def is_valid_device_id(raw: str) -> bool:
    """Return True if raw represents a valid 12-character hex sensor ID."""
    return bool(DEVICE_ID_RE.match(normalize_device_id(raw)))


def normalize_phone_id(raw: str) -> str:
    """Normalize a Mobile Alerts phone ID."""
    return raw.strip()


def is_valid_phone_id(raw: str) -> bool:
    """Return True if raw represents a valid numeric Mobile Alerts phone ID."""
    return bool(PHONE_ID_RE.match(normalize_phone_id(raw)))


def parse_device_ids_text(raw: str) -> tuple[list[str], list[str]]:
    """Split comma/whitespace/semicolon-separated device IDs into valid and invalid lists."""
    valid_ids: list[str] = []
    invalid_tokens: list[str] = []
    seen_valid: set[str] = set()
    seen_invalid: set[str] = set()

    for token in re.split(r"[,;\s]+", raw.strip()):
        if not token:
            continue
        normalized = normalize_device_id(token)
        if is_valid_device_id(normalized):
            if normalized not in seen_valid:
                seen_valid.add(normalized)
                valid_ids.append(normalized)
        else:
            if token not in seen_invalid:
                seen_invalid.add(token)
                invalid_tokens.append(token)

    return valid_ids, invalid_tokens


def is_invalid_measurement_value(value: Any) -> bool:
    """Return True if value is a Mobile Alerts error sentinel or non-finite float."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, (int, float)):
        return value in INVALID_MEASUREMENT_VALUES
    return False


@dataclass(frozen=True)
class ChannelSpec:
    """Definition of a single entity channel exposed by a Mobile Alerts device."""

    key: str
    kind: Literal["sensor", "binary_sensor", "event"]
    sensor_type: str
    translation_key: str
    api_key: str
    enabled_default: bool = True
    inverted: bool = False


@dataclass(frozen=True)
class DeviceProfile:
    """Specification of a Mobile Alerts / TFA WeatherHub hardware device family."""

    profile_id: str
    prefix: str | None
    model_code: str
    display_name: str
    manufacturer: str
    channels: tuple[ChannelSpec, ...]


@dataclass(frozen=True)
class DeviceReading:
    """Normalized measurement snapshot returned by the Mobile Alerts REST API."""

    device_id: str
    last_seen: int | None
    low_battery: bool | None
    values: dict[str, Any]
    raw: dict[str, Any]


def _ch_temp(key: str, translation_key: str) -> ChannelSpec:
    return ChannelSpec(
        key=key,
        kind="sensor",
        sensor_type="temperature",
        translation_key=translation_key,
        api_key=key,
    )


def _ch_hum(key: str, translation_key: str) -> ChannelSpec:
    return ChannelSpec(
        key=key,
        kind="sensor",
        sensor_type="humidity",
        translation_key=translation_key,
        api_key=key,
    )


_ALL_PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(
        profile_id="ID01",
        prefix="01",
        model_code="MA 10101",
        display_name="Wireless Thermometer with Cable Sensor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "cable_temperature"),
        ),
    ),
    DeviceProfile(
        profile_id="ID02",
        prefix="02",
        model_code="MA 10100",
        display_name="Wireless Thermometer",
        manufacturer="Mobile Alerts",
        channels=(_ch_temp("t1", "temperature"),),
    ),
    DeviceProfile(
        profile_id="ID03",
        prefix="03",
        model_code="MA 10200 / TFA 30.3303.02",
        display_name="Wireless Thermo-Hygrometer",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            _ch_hum("h", "humidity"),
        ),
    ),
    DeviceProfile(
        profile_id="ID04",
        prefix="04",
        model_code="MA 10350",
        display_name="Wireless Thermo-Hygrometer with Water Detector",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            _ch_hum("h", "humidity"),
            ChannelSpec(
                key="water",
                kind="binary_sensor",
                sensor_type="water",
                translation_key="water",
                api_key="t2",
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID04_CABLE",
        prefix=None,
        model_code="MA 10300 / MA 10350 (Cable Temp)",
        display_name="Thermo-Hygrometer with Cable Temperature Probe",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "cable_temperature"),
            _ch_hum("h", "humidity"),
        ),
    ),
    DeviceProfile(
        profile_id="ID05",
        prefix="05",
        model_code="MA 10402",
        display_name="Wireless CO2 Monitor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "external_temperature"),
            _ch_hum("h", "humidity"),
            ChannelSpec(
                key="ppm",
                kind="sensor",
                sensor_type="co2",
                translation_key="co2",
                api_key="ppm",
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID06",
        prefix="06",
        model_code="MA 10700",
        display_name="Wireless Thermo-Hygrometer with Pool Sensor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "pool_temperature"),
            _ch_hum("h", "humidity"),
        ),
    ),
    DeviceProfile(
        profile_id="ID07",
        prefix="07",
        model_code="MA 10410",
        display_name="Wireless Weather Station",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "external_temperature"),
            _ch_hum("h", "humidity_1"),
            _ch_hum("h2", "humidity_2"),
        ),
    ),
    DeviceProfile(
        profile_id="ID08",
        prefix="08",
        model_code="MA 10650",
        display_name="Wireless Rain Gauge",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            ChannelSpec(
                key="r",
                kind="sensor",
                sensor_type="rain",
                translation_key="rain",
                api_key="r",
            ),
            ChannelSpec(
                key="rf",
                kind="sensor",
                sensor_type="rain_flips",
                translation_key="rain_flips",
                api_key="rf",
                enabled_default=False,
            ),
            ChannelSpec(
                key="rr",
                kind="sensor",
                sensor_type="rain_last_increment",
                translation_key="rain_last_increment",
                api_key="rr",
                enabled_default=False,
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID09",
        prefix="09",
        model_code="MA 10300 / MA 10320",
        display_name="Wireless Thermo-Hygrometer with Cable Sensor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "cable_temperature"),
            _ch_hum("h", "humidity"),
        ),
    ),
    DeviceProfile(
        profile_id="ID0A",
        prefix="0A",
        model_code="MA 10860",
        display_name="Acoustic Smoke Detector Monitor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            ChannelSpec("smoke_1", "binary_sensor", "smoke", "smoke_1", "a1"),
            ChannelSpec("smoke_2", "binary_sensor", "smoke", "smoke_2", "a2"),
            ChannelSpec("smoke_3", "binary_sensor", "smoke", "smoke_3", "a3"),
            ChannelSpec("smoke_4", "binary_sensor", "smoke", "smoke_4", "a4"),
        ),
    ),
    DeviceProfile(
        profile_id="ID0B",
        prefix="0B",
        model_code="MA 10660",
        display_name="Wireless Anemometer",
        manufacturer="Mobile Alerts",
        channels=(
            ChannelSpec("ws", "sensor", "wind_speed", "wind_speed", "ws"),
            ChannelSpec("wg", "sensor", "wind_gust", "wind_gust", "wg"),
            ChannelSpec("wd", "sensor", "wind_direction", "wind_direction", "wd"),
            ChannelSpec(
                "wd_degrees",
                "sensor",
                "wind_direction_degrees",
                "wind_direction_degrees",
                "wd",
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID0E",
        prefix="0E",
        model_code="MA 10241 / MA 10250PRO",
        display_name="Wireless Thermo-Hygrometer",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            _ch_hum("h", "humidity"),
        ),
    ),
    DeviceProfile(
        profile_id="ID0F",
        prefix="0F",
        model_code="MA 10101 PRO",
        display_name="Pro Thermometer with Cable Sensor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "cable_temperature"),
        ),
    ),
    DeviceProfile(
        profile_id="ID10",
        prefix="10",
        model_code="MA 10800",
        display_name="Wireless Contact Sensor",
        manufacturer="Mobile Alerts",
        channels=(ChannelSpec("contact", "binary_sensor", "contact", "contact", "w"),),
    ),
    DeviceProfile(
        profile_id="ID11",
        prefix="11",
        model_code="TFA 30.3060.01",
        display_name="KLIMA@HOME Thermo-Hygrometer with 3 External Sensors",
        manufacturer="TFA Dostmann",
        channels=(
            _ch_temp("t1", "temperature_t1"),
            _ch_temp("t2", "temperature_t2"),
            _ch_temp("t3", "temperature_t3"),
            _ch_temp("t4", "temperature_t4"),
            _ch_hum("h1", "humidity_1"),
            _ch_hum("h2", "humidity_2"),
            _ch_hum("h3", "humidity_3"),
            _ch_hum("h4", "humidity_4"),
        ),
    ),
    DeviceProfile(
        profile_id="ID12",
        prefix="12",
        model_code="MA 10230",
        display_name="Wireless Room Climate Station",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            _ch_hum("h", "humidity"),
            _ch_hum("h3havg", "humidity_3h_avg"),
            _ch_hum("h24havg", "humidity_24h_avg"),
            _ch_hum("h7davg", "humidity_7d_avg"),
            _ch_hum("h30davg", "humidity_30d_avg"),
        ),
    ),
    DeviceProfile(
        profile_id="ID15",
        prefix="15",
        model_code="MA 10880",
        display_name="Wireless 4-Channel Switch",
        manufacturer="Mobile Alerts",
        channels=(
            ChannelSpec("button_1", "event", "button", "button_1", "kp1"),
            ChannelSpec("button_2", "event", "button", "button_2", "kp2"),
            ChannelSpec("button_3", "event", "button", "button_3", "kp3"),
            ChannelSpec("button_4", "event", "button", "button_4", "kp4"),
            ChannelSpec(
                "kp1c",
                "sensor",
                "key_press_count",
                "key_press_1_count",
                "kp1c",
                enabled_default=False,
            ),
            ChannelSpec(
                "kp2c",
                "sensor",
                "key_press_count",
                "key_press_2_count",
                "kp2c",
                enabled_default=False,
            ),
            ChannelSpec(
                "kp3c",
                "sensor",
                "key_press_count",
                "key_press_3_count",
                "kp3c",
                enabled_default=False,
            ),
            ChannelSpec(
                "kp4c",
                "sensor",
                "key_press_count",
                "key_press_4_count",
                "kp4c",
                enabled_default=False,
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID17",
        prefix="17",
        model_code="MA 10870",
        display_name="Wireless Voltage Monitor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            ChannelSpec(
                key="ac_power",
                kind="binary_sensor",
                sensor_type="ac_power",
                translation_key="ac_power",
                api_key="t2",
                inverted=True,
            ),
        ),
    ),
    DeviceProfile(
        profile_id="ID18",
        prefix="18",
        model_code="MA 10238",
        display_name="Wireless Air Pressure Monitor",
        manufacturer="Mobile Alerts",
        channels=(
            _ch_temp("t1", "temperature"),
            _ch_hum("h", "humidity"),
            ChannelSpec("ap", "sensor", "pressure", "pressure", "ap"),
        ),
    ),
    DeviceProfile(
        profile_id="ID20",
        prefix="20",
        model_code="MA 10120",
        display_name="Wireless Thermometer",
        manufacturer="Mobile Alerts",
        channels=(_ch_temp("t1", "temperature"),),
    ),
)

PROFILES_BY_ID: dict[str, DeviceProfile] = {p.profile_id: p for p in _ALL_PROFILES}
PROFILES_BY_PREFIX: dict[str, DeviceProfile] = {
    p.prefix: p for p in _ALL_PROFILES if p.prefix is not None
}

_DYNAMIC_CHANNEL_SPECS: dict[str, tuple[ChannelSpec, ...]] = {
    "t1": (_ch_temp("t1", "temperature_t1"),),
    "t2": (_ch_temp("t2", "temperature_t2"),),
    "t3": (_ch_temp("t3", "temperature_t3"),),
    "t4": (_ch_temp("t4", "temperature_t4"),),
    "h": (_ch_hum("h", "humidity"),),
    "h1": (_ch_hum("h1", "humidity_1"),),
    "h2": (_ch_hum("h2", "humidity_2"),),
    "h3": (_ch_hum("h3", "humidity_3"),),
    "h4": (_ch_hum("h4", "humidity_4"),),
    "h3havg": (_ch_hum("h3havg", "humidity_3h_avg"),),
    "h24havg": (_ch_hum("h24havg", "humidity_24h_avg"),),
    "h7davg": (_ch_hum("h7davg", "humidity_7d_avg"),),
    "h30davg": (_ch_hum("h30davg", "humidity_30d_avg"),),
    "ap": (ChannelSpec("ap", "sensor", "pressure", "pressure", "ap"),),
    "ppm": (ChannelSpec("ppm", "sensor", "co2", "co2", "ppm"),),
    "r": (ChannelSpec("r", "sensor", "rain", "rain", "r"),),
    "rf": (ChannelSpec("rf", "sensor", "rain_flips", "rain_flips", "rf", enabled_default=False),),
    "rr": (
        ChannelSpec(
            "rr",
            "sensor",
            "rain_last_increment",
            "rain_last_increment",
            "rr",
            enabled_default=False,
        ),
    ),
    "ws": (ChannelSpec("ws", "sensor", "wind_speed", "wind_speed", "ws"),),
    "wg": (ChannelSpec("wg", "sensor", "wind_gust", "wind_gust", "wg"),),
    "wd": (
        ChannelSpec("wd", "sensor", "wind_direction", "wind_direction", "wd"),
        ChannelSpec(
            "wd_degrees",
            "sensor",
            "wind_direction_degrees",
            "wind_direction_degrees",
            "wd",
        ),
    ),
    "w": (ChannelSpec("contact", "binary_sensor", "contact", "contact", "w"),),
    "kp1c": (
        ChannelSpec("button_1", "event", "button", "button_1", "kp1"),
        ChannelSpec(
            "kp1c",
            "sensor",
            "key_press_count",
            "key_press_1_count",
            "kp1c",
            enabled_default=False,
        ),
    ),
    "kp2c": (
        ChannelSpec("button_2", "event", "button", "button_2", "kp2"),
        ChannelSpec(
            "kp2c",
            "sensor",
            "key_press_count",
            "key_press_2_count",
            "kp2c",
            enabled_default=False,
        ),
    ),
    "kp3c": (
        ChannelSpec("button_3", "event", "button", "button_3", "kp3"),
        ChannelSpec(
            "kp3c",
            "sensor",
            "key_press_count",
            "key_press_3_count",
            "kp3c",
            enabled_default=False,
        ),
    ),
    "kp4c": (
        ChannelSpec("button_4", "event", "button", "button_4", "kp4"),
        ChannelSpec(
            "kp4c",
            "sensor",
            "key_press_count",
            "key_press_4_count",
            "kp4c",
            enabled_default=False,
        ),
    ),
}


def _is_alert_or_metadata_key(key: str) -> bool:
    """Return True if a measurement key is metadata or an alert-flag field."""
    if key in _METADATA_KEYS or key in _EXACT_ALERT_AND_STATUS_KEYS:
        return True
    return any(key.endswith(suffix) for suffix in _ALERT_KEY_SUFFIXES)


def _parse_bool_flag(value: Any) -> bool | None:
    """Parse a boolean flag from bool, int/float, or string representation."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _to_valid_timestamp(value: Any) -> int | None:
    """Convert an epoch timestamp candidate into a positive integer, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(value) and value > 0:
            return int(value)
        return None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if math.isfinite(parsed) and parsed > 0:
            return int(parsed)
    return None


def parse_device_reading(raw: dict[str, Any]) -> DeviceReading:
    """Parse a single device JSON object from the Mobile Alerts REST API."""
    device_id = normalize_device_id(str(raw.get("deviceid", "")))
    measurement_raw = raw.get("measurement")
    measurement = measurement_raw if isinstance(measurement_raw, dict) else {}

    low_battery: bool | None = None
    for source, key in (
        (raw, "lowbat"),
        (raw, "lowbattery"),
        (measurement, "lb"),
        (measurement, "lowbattery"),
    ):
        if key in source and source[key] is not None:
            parsed_flag = _parse_bool_flag(source[key])
            if parsed_flag is not None:
                low_battery = parsed_flag
                break

    top_lastseen = _to_valid_timestamp(raw.get("lastseen"))
    meas_c = _to_valid_timestamp(measurement.get("c"))
    meas_ts = _to_valid_timestamp(measurement.get("ts"))

    if top_lastseen is not None and meas_c is not None:
        last_seen: int | None = max(top_lastseen, meas_c)
    elif top_lastseen is not None:
        last_seen = top_lastseen
    elif meas_c is not None:
        last_seen = meas_c
    else:
        last_seen = meas_ts

    values: dict[str, Any] = {}
    for key, val in measurement.items():
        if key in {"idx", "ts", "c"}:
            continue
        values[key] = None if is_invalid_measurement_value(val) else val

    return DeviceReading(
        device_id=device_id,
        last_seen=last_seen,
        low_battery=low_battery,
        values=values,
        raw=raw,
    )


def resolve_device_profile(
    device_id: str,
    model_override: str | None = None,
    reading: DeviceReading | None = None,
) -> DeviceProfile:
    """Resolve the hardware profile for a device ID using prefix-first matching."""
    if model_override and model_override != "auto" and model_override in PROFILES_BY_ID:
        return PROFILES_BY_ID[model_override]

    normalized = normalize_device_id(device_id)
    prefix = normalized[:2] if len(normalized) >= 2 else ""

    if prefix in PROFILES_BY_PREFIX:
        return PROFILES_BY_PREFIX[prefix]

    # Dynamic fallback for undocumented hardware prefixes
    channels: list[ChannelSpec] = []
    if reading is not None:
        for key in reading.values:
            if _is_alert_or_metadata_key(key):
                continue
            if key in _DYNAMIC_CHANNEL_SPECS:
                channels.extend(_DYNAMIC_CHANNEL_SPECS[key])

    return DeviceProfile(
        profile_id=f"DYNAMIC_{prefix or 'UNKNOWN'}",
        prefix=prefix or None,
        model_code=f"ID{prefix}" if prefix else "Unknown",
        display_name="Mobile Alerts Sensor",
        manufacturer="Mobile Alerts",
        channels=tuple(channels),
    )
