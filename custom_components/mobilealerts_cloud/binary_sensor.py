"""Binary sensor platform for Mobile Alerts Cloud."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MobileAlertsCloudConfigEntry
from .api import ChannelSpec, DeviceProfile, DeviceReading
from .coordinator import MobileAlertsCoordinator
from .entity import MobileAlertsEntity

PARALLEL_UPDATES = 0


def _parse_binary_state(raw: Any, *, inverted: bool = False) -> bool | None:
    """Convert bool/int/float/str API values to bool, preserving None for sentinels."""
    if raw is None:
        return None

    parsed: bool | None
    if isinstance(raw, bool):
        parsed = raw
    elif isinstance(raw, (int, float)):
        parsed = raw != 0
    elif isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            parsed = True
        elif lowered in {"false", "0", "no", "off"}:
            parsed = False
        else:
            parsed = None
    else:
        parsed = None

    if parsed is None:
        return None
    return (not parsed) if inverted else parsed


def _extract_water(reading: DeviceReading, channel: ChannelSpec) -> bool | None:
    """Extract water detection state (`t2`, with fallback to `w` if present)."""
    if channel.api_key in reading.values:
        return _parse_binary_state(reading.values.get(channel.api_key))
    if "w" in reading.values:
        return _parse_binary_state(reading.values.get("w"))
    return None


@dataclass(frozen=True, kw_only=True)
class MobileAlertsBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a Mobile Alerts binary sensor."""

    value_fn: Callable[[DeviceReading, ChannelSpec], bool | None]


BINARY_SENSOR_DESCRIPTIONS: dict[str, MobileAlertsBinarySensorEntityDescription] = {
    "battery": MobileAlertsBinarySensorEntityDescription(
        key="battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r, _ch: r.low_battery,
    ),
    "water": MobileAlertsBinarySensorEntityDescription(
        key="water",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=_extract_water,
    ),
    "contact": MobileAlertsBinarySensorEntityDescription(
        key="contact",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda r, ch: _parse_binary_state(r.values.get(ch.api_key)),
    ),
    "ac_power": MobileAlertsBinarySensorEntityDescription(
        key="ac_power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda r, ch: _parse_binary_state(r.values.get(ch.api_key), inverted=ch.inverted),
    ),
    "smoke": MobileAlertsBinarySensorEntityDescription(
        key="smoke",
        device_class=BinarySensorDeviceClass.SMOKE,
        value_fn=lambda r, ch: _parse_binary_state(r.values.get(ch.api_key)),
    ),
}

_BATTERY_CHANNEL = ChannelSpec(
    key="battery",
    kind="binary_sensor",
    sensor_type="battery",
    translation_key="battery",
    api_key="lowbat",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MobileAlertsCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mobile Alerts Cloud binary sensor entities for each sensor subentry."""
    runtime = config_entry.runtime_data

    for sensor in runtime.sensors:
        device_id, profile = sensor.device_id, sensor.profile

        entities: list[MobileAlertsBinarySensorEntity] = []
        for channel in profile.channels:
            if channel.kind != "binary_sensor":
                continue
            base_desc = BINARY_SENSOR_DESCRIPTIONS.get(channel.sensor_type)
            if base_desc is None:
                continue
            entities.append(
                MobileAlertsBinarySensorEntity(
                    runtime.coordinator,
                    device_id=device_id,
                    device_title=sensor.title,
                    name_prefix=runtime.name_prefix,
                    profile=profile,
                    channel=channel,
                    description=base_desc,
                )
            )

        # Every Mobile Alerts sensor exposes a standard HA diagnostic battery binary sensor
        entities.append(
            MobileAlertsBinarySensorEntity(
                runtime.coordinator,
                device_id=device_id,
                device_title=sensor.title,
                name_prefix=runtime.name_prefix,
                profile=profile,
                channel=_BATTERY_CHANNEL,
                description=BINARY_SENSOR_DESCRIPTIONS["battery"],
            )
        )

        async_add_entities(entities, config_subentry_id=sensor.subentry_id)


class MobileAlertsBinarySensorEntity(MobileAlertsEntity, BinarySensorEntity):
    """Representation of a Mobile Alerts binary sensor."""

    entity_description: MobileAlertsBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MobileAlertsCoordinator,
        *,
        device_id: str,
        device_title: str,
        name_prefix: str,
        profile: DeviceProfile,
        channel: ChannelSpec,
        description: MobileAlertsBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(
            coordinator,
            device_id=device_id,
            device_title=device_title,
            name_prefix=name_prefix,
            profile=profile,
            channel_key=channel.key,
        )
        self._channel = channel
        self.entity_description = replace(
            description,
            key=channel.key,
            translation_key=channel.translation_key,
            entity_registry_enabled_default=channel.enabled_default,
        )

    @property
    def is_on(self) -> bool | None:
        """Return the boolean state for this binary sensor, or None if unknown."""
        reading = self._reading
        if reading is None:
            return None
        return self.entity_description.value_fn(reading, self._channel)
