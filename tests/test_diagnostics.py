"""Config entry and device diagnostics (real Home Assistant)."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from mock_api_server import MockMobileAlertsServer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobilealerts_cloud.const import DOMAIN
from custom_components.mobilealerts_cloud.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

from .conftest import hub_entry, sensor_subentry

REDACTED = "**REDACTED**"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = hub_entry(["010000AABB01"], phone_id="100000000001", name_prefix="Test ")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_entry_diagnostics_redacts_ids(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Phone ID is redacted, sensor IDs become consistent aliases; profile telemetry stays."""
    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["phone_id"] == REDACTED
    assert diag["subentries"][0]["data"]["device_id"] == "sensor_1"
    assert diag["subentries"][0]["title"] == "Sensor sensor_1"
    assert diag["subentries"][0]["resolved_profile"]["profile_id"] == "ID01"
    assert diag["raw_readings"][0]["deviceid"] == "sensor_1"
    assert diag["hub"]["device_count"] == 1
    assert "100000000001" not in str(diag)


async def test_diagnostics_contain_no_sensor_ids(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """No sensor ID appears anywhere, not even in short forms of titles or rate-limit keys."""
    ids = ["010000AABB01", "080000AABB08"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hub",
        data={},
        options={},
        subentries_data=[
            sensor_subentry("010000AABB01", "MA 10101 BB01"),
            sensor_subentry("080000AABB08", "MA 10650 BB08"),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Fill the rate-limit telemetry, whose keys use the short `...BB01` form.
    await entry.runtime_data.coordinator.async_refresh()

    dumped = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for dev_id in ids:
        assert dev_id not in dumped
        assert dev_id.lower() not in dumped
        assert f"...{dev_id[-4:]}" not in dumped
        assert f" {dev_id[-4:]}" not in dumped
    assert "...sensor_1" in dumped
    assert "MA 10650 sensor_2" in dumped


async def test_config_entry_diagnostics_not_loaded(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Diagnostics of an unloaded entry report its status without runtime data."""
    entry = hub_entry(["010000AABB01"], phone_id="100000000001")
    entry.add_to_hass(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["status"] == "not_loaded"
    assert diag["entry"]["data"]["phone_id"] == REDACTED


async def test_device_diagnostics(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, device_registry: dr.DeviceRegistry
) -> None:
    """Sensor device diagnostics are scoped to the sensor; the hub returns the entry view."""
    entry = await _setup(hass)
    sensor = device_registry.async_get_device_by_identifier(
        (DOMAIN, "010000AABB01"), entry.entry_id
    )
    assert sensor is not None
    dev_diag = await async_get_device_diagnostics(hass, entry, sensor)
    assert dev_diag["subentry"]["data"]["device_id"] == "sensor_1"
    assert dev_diag["raw_reading"]["deviceid"] == "sensor_1"
    assert "010000AABB01" not in str(dev_diag)
    assert dev_diag["resolved_profile"]["profile_id"] == "ID01"

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"hub_{entry.entry_id}"), entry.entry_id
    )
    assert hub is not None
    hub_diag = await async_get_device_diagnostics(hass, entry, hub)
    assert hub_diag["hub"]["device_count"] == 1
