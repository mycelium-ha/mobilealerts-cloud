"""Registry snapshots per platform and the device layout (real Home Assistant)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.mobilealerts_cloud.const import DOMAIN, PLATFORMS


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_platform_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    """Entity registry entries and states of each platform match the snapshot."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.mobilealerts_cloud.PLATFORMS", [platform]):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_device_registry_snapshot(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The hub device and one device per sensor match the snapshot."""
    devices = dr.async_entries_for_config_entry(device_registry, init_integration.entry_id)
    for device in sorted(devices, key=lambda d: sorted(d.identifiers)):
        identifier = next(iter(device.identifiers))[1]
        assert device == snapshot(name=identifier)
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"hub_{init_integration.entry_id}"), init_integration.entry_id
    )
    assert hub is not None
    sensors = [d for d in devices if d.id != hub.id]
    assert sensors
    assert all(d.via_device_id == hub.id for d in sensors)
