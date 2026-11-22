"""Shared base entity class for Mobile Alerts Cloud."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceProfile, DeviceReading
from .const import ATTRIBUTION, DOMAIN
from .coordinator import MobileAlertsCoordinator


class MobileAlertsEntity(CoordinatorEntity[MobileAlertsCoordinator]):
    """Base CoordinatorEntity associating a channel with its Mobile Alerts device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: MobileAlertsCoordinator,
        *,
        device_id: str,
        device_title: str,
        name_prefix: str,
        profile: DeviceProfile,
        channel_key: str,
    ) -> None:
        """Initialize shared entity metadata and DeviceInfo."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel_key = channel_key
        self._attr_unique_id = f"{device_id}_{channel_key}"

        display_name = f"{name_prefix}{device_title}".strip() or device_title
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=display_name,
            manufacturer=profile.manufacturer,
            model=profile.display_name,
            model_id=profile.model_code,
            serial_number=device_id,
            via_device_id=coordinator.config_entry.runtime_data.hub_device_id,
        )

    @property
    def _reading(self) -> DeviceReading | None:
        """Return the latest DeviceReading for this device."""
        return self.coordinator.get_reading(self._device_id)

    # The diagnostic last_seen sensor stays available while the sensor is stale (ADR-005).
    _available_when_stale = False

    @property
    def available(self) -> bool:
        """Return True when the poll succeeded and the sensor reported recently (ADR-005)."""
        if not super().available or self._reading is None:
            return False
        return self._available_when_stale or not self.coordinator.is_stale(self._device_id)
