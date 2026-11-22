"""Sensor platform for Mobile Alerts Cloud."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import MobileAlertsCloudConfigEntry
from .api import WIND_DIRECTIONS, ChannelSpec, DeviceProfile, DeviceReading
from .coordinator import MobileAlertsCoordinator
from .entity import MobileAlertsEntity

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


def _parse_float(
    raw: Any,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    strict_min: bool = False,
) -> float | None:
    """Parse a finite float within optional range bounds."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        val = float(raw)
    except TypeError, ValueError:
        return None
    if not math.isfinite(val):
        return None
    if min_value is not None and ((val <= min_value) if strict_min else (val < min_value)):
        return None
    if max_value is not None and val > max_value:
        return None
    return val


def _parse_int(
    raw: Any, *, min_value: int | None = None, max_value: int | None = None
) -> int | None:
    """Parse an integer within optional bounds."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, float) and not math.isfinite(raw):
            return None
        val = int(raw)
    except TypeError, ValueError:
        return None
    if min_value is not None and val < min_value:
        return None
    if max_value is not None and val > max_value:
        return None
    return val


def _extract_wind_direction(reading: DeviceReading, channel: ChannelSpec) -> str | None:
    idx = _parse_int(reading.values.get(channel.api_key), min_value=0, max_value=15)
    return WIND_DIRECTIONS[idx] if idx is not None else None


def _extract_wind_degrees(reading: DeviceReading, channel: ChannelSpec) -> float | None:
    idx = _parse_int(reading.values.get(channel.api_key), min_value=0, max_value=15)
    return round(idx * 22.5, 1) if idx is not None else None


def _extract_last_seen(reading: DeviceReading, _channel: ChannelSpec) -> datetime | None:
    if reading.last_seen is None:
        return None
    try:
        return datetime.fromtimestamp(reading.last_seen, tz=UTC)
    except OverflowError, OSError, ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class MobileAlertsSensorEntityDescription(SensorEntityDescription):
    """Describe a Mobile Alerts numeric, enum, or timestamp sensor."""

    value_fn: Callable[[DeviceReading, ChannelSpec], StateType | datetime]


SENSOR_DESCRIPTIONS: dict[str, MobileAlertsSensorEntityDescription] = {
    "temperature": MobileAlertsSensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda r, ch: _parse_float(
            r.values.get(ch.api_key), min_value=-100.0, max_value=100.0
        ),
    ),
    "humidity": MobileAlertsSensorEntityDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda r, ch: _parse_float(
            r.values.get(ch.api_key), min_value=0.0, max_value=100.0
        ),
    ),
    "pressure": MobileAlertsSensorEntityDescription(
        key="pressure",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.HPA,
        suggested_display_precision=1,
        value_fn=lambda r, ch: _parse_float(
            r.values.get(ch.api_key), min_value=0.0, strict_min=True
        ),
    ),
    "co2": MobileAlertsSensorEntityDescription(
        key="co2",
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        suggested_display_precision=0,
        value_fn=lambda r, ch: _parse_float(r.values.get(ch.api_key), min_value=0.0),
    ),
    "rain": MobileAlertsSensorEntityDescription(
        key="rain",
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=3,
        value_fn=lambda r, ch: _parse_float(r.values.get(ch.api_key), min_value=0.0),
    ),
    "rain_flips": MobileAlertsSensorEntityDescription(
        key="rain_flips",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda r, ch: _parse_int(r.values.get(ch.api_key), min_value=0),
    ),
    "rain_last_increment": MobileAlertsSensorEntityDescription(
        key="rain_last_increment",
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        value_fn=lambda r, ch: _parse_float(r.values.get(ch.api_key), min_value=0.0),
    ),
    "wind_speed": MobileAlertsSensorEntityDescription(
        key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda r, ch: _parse_float(r.values.get(ch.api_key), min_value=0.0),
    ),
    "wind_gust": MobileAlertsSensorEntityDescription(
        key="wind_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda r, ch: _parse_float(r.values.get(ch.api_key), min_value=0.0),
    ),
    "wind_direction": MobileAlertsSensorEntityDescription(
        key="wind_direction",
        device_class=SensorDeviceClass.ENUM,
        options=list(WIND_DIRECTIONS),
        value_fn=_extract_wind_direction,
    ),
    "wind_direction_degrees": MobileAlertsSensorEntityDescription(
        key="wind_direction_degrees",
        device_class=SensorDeviceClass.WIND_DIRECTION,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=1,
        value_fn=_extract_wind_degrees,
    ),
    "key_press_count": MobileAlertsSensorEntityDescription(
        key="key_press_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r, ch: _parse_int(r.values.get(ch.api_key), min_value=0),
    ),
    "last_seen": MobileAlertsSensorEntityDescription(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_extract_last_seen,
    ),
}

_LAST_SEEN_CHANNEL = ChannelSpec(
    key="last_seen",
    kind="sensor",
    sensor_type="last_seen",
    translation_key="last_seen",
    api_key="last_seen",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MobileAlertsCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mobile Alerts Cloud sensor entities for each sensor subentry."""
    runtime = config_entry.runtime_data

    for sensor in runtime.sensors:
        device_id, profile = sensor.device_id, sensor.profile

        entities: list[MobileAlertsSensorEntity] = []
        for channel in profile.channels:
            if channel.kind != "sensor":
                continue
            base_desc = SENSOR_DESCRIPTIONS.get(channel.sensor_type)
            if base_desc is None:
                _LOGGER.debug(
                    "Skipping unmapped sensor type %s on sensor %s",
                    channel.sensor_type,
                    sensor.title,
                )
                continue
            entities.append(
                MobileAlertsSensorEntity(
                    runtime.coordinator,
                    device_id=device_id,
                    device_title=sensor.title,
                    name_prefix=runtime.name_prefix,
                    profile=profile,
                    channel=channel,
                    description=base_desc,
                )
            )

        # Always expose a diagnostic Last Seen timestamp sensor per device
        entities.append(
            MobileAlertsSensorEntity(
                runtime.coordinator,
                device_id=device_id,
                device_title=sensor.title,
                name_prefix=runtime.name_prefix,
                profile=profile,
                channel=_LAST_SEEN_CHANNEL,
                description=SENSOR_DESCRIPTIONS["last_seen"],
            )
        )

        async_add_entities(entities, config_subentry_id=sensor.subentry_id)


class MobileAlertsSensorEntity(MobileAlertsEntity, SensorEntity):
    """Representation of a Mobile Alerts numeric, enum, or diagnostic timestamp sensor."""

    entity_description: MobileAlertsSensorEntityDescription

    def __init__(
        self,
        coordinator: MobileAlertsCoordinator,
        *,
        device_id: str,
        device_title: str,
        name_prefix: str,
        profile: DeviceProfile,
        channel: ChannelSpec,
        description: MobileAlertsSensorEntityDescription,
    ) -> None:
        """Initialize the sensor entity with channel-specific description metadata."""
        super().__init__(
            coordinator,
            device_id=device_id,
            device_title=device_title,
            name_prefix=name_prefix,
            profile=profile,
            channel_key=channel.key,
        )
        self._channel = channel
        self._available_when_stale = channel.key == _LAST_SEEN_CHANNEL.key
        self.entity_description = replace(
            description,
            key=channel.key,
            translation_key=channel.translation_key,
            entity_registry_enabled_default=channel.enabled_default,
        )

    @property
    def native_value(self) -> StateType | datetime:
        """Return the parsed measurement value for this channel."""
        reading = self._reading
        if reading is None:
            return None
        return self.entity_description.value_fn(reading, self._channel)
