"""Event platform for Mobile Alerts Cloud (ID15 / MA 10880 4-channel wireless switch)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MobileAlertsCloudConfigEntry
from .api import KEY_PRESS_TYPES, ChannelSpec, DeviceProfile
from .const import EVENT_KEY_PRESS
from .coordinator import MobileAlertsCoordinator
from .entity import MobileAlertsEntity

PARALLEL_UPDATES = 0
EVENT_TYPES: list[str] = ["short_press", "double_press", "long_press"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MobileAlertsCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mobile Alerts Cloud button event entities for ID15 wireless switches."""
    runtime = config_entry.runtime_data

    for sensor in runtime.sensors:
        device_id, profile = sensor.device_id, sensor.profile

        entities = [
            MobileAlertsButtonEventEntity(
                runtime.coordinator,
                device_id=device_id,
                device_title=sensor.title,
                name_prefix=runtime.name_prefix,
                profile=profile,
                channel=channel,
            )
            for channel in profile.channels
            if channel.kind == "event"
        ]
        if entities:
            async_add_entities(entities, config_subentry_id=sensor.subentry_id)


def _extract_counter_and_type(
    values: dict[str, Any], api_prefix: str
) -> tuple[int | None, int | None]:
    """Read running press counter (`kpXc`) and press type (`kpXt`) integers."""
    raw_count = values.get(f"{api_prefix}c")
    raw_type = values.get(f"{api_prefix}t")

    count: int | None = None
    if raw_count is not None and not isinstance(raw_count, bool):
        try:
            count = int(raw_count)
        except TypeError, ValueError:
            count = None

    press_code: int | None = None
    if raw_type is not None and not isinstance(raw_type, bool):
        try:
            press_code = int(raw_type)
        except TypeError, ValueError:
            press_code = None

    return count, press_code


class MobileAlertsButtonEventEntity(MobileAlertsEntity, EventEntity):
    """EventEntity representing a single button channel on an MA 10880 switch."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        coordinator: MobileAlertsCoordinator,
        *,
        device_id: str,
        device_title: str,
        name_prefix: str,
        profile: DeviceProfile,
        channel: ChannelSpec,
    ) -> None:
        """Initialize the button event entity without firing a startup event."""
        super().__init__(
            coordinator,
            device_id=device_id,
            device_title=device_title,
            name_prefix=name_prefix,
            profile=profile,
            channel_key=channel.key,
        )
        self._channel = channel
        self._attr_translation_key = channel.translation_key
        self._last_count: int | None = None

        reading = self._reading
        if reading is not None:
            initial_count, _ = _extract_counter_and_type(reading.values, self._channel.api_key)
            self._last_count = initial_count

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire a button event only when the channel's running counter changes."""
        reading = self._reading
        if reading is None:
            self.async_write_ha_state()
            return

        new_count, press_code = _extract_counter_and_type(reading.values, self._channel.api_key)
        if new_count is None:
            self.async_write_ha_state()
            return

        if self._last_count is None:
            # First observed counter establishes the baseline without firing an event.
            self._last_count = new_count
            self.async_write_ha_state()
            return

        if new_count != self._last_count:
            self._last_count = new_count
            # Do not emit a phantom button press when a battery swap resets the counter to 0.
            if new_count > 0:
                press_type = KEY_PRESS_TYPES.get(press_code) if press_code is not None else None
                if press_type is not None:
                    self._trigger_event(
                        press_type,
                        {
                            "count": new_count,
                            "channel": self._channel_key,
                        },
                    )
                    self.hass.bus.async_fire(
                        EVENT_KEY_PRESS,
                        {
                            "device_id": self._device_id,
                            "channel": self._channel_key,
                            "press_type": press_type,
                            "count": new_count,
                        },
                    )

        self.async_write_ha_state()
