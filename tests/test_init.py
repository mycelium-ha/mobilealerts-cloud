"""Setup, unload, reload cache, device removal and actions (real Home Assistant)."""

from __future__ import annotations

import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from mock_api_server import MockMobileAlertsServer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobilealerts_cloud import (
    async_remove_config_entry_device,
    cache_readings,
)
from custom_components.mobilealerts_cloud.api import parse_device_reading
from custom_components.mobilealerts_cloud.const import (
    DOMAIN,
    SERVICE_DUMP_RAW_RESPONSE,
    SERVICE_FORCE_REFRESH,
    SERVICE_MIGRATE_FROM_LEGACY,
)

from .conftest import DOCUMENTED, hub_entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The hub loads with one batched request and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_DUMP_RAW_RESPONSE)
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    # Actions are registered in async_setup and stay after the last hub unloads.
    assert hass.services.has_service(DOMAIN, SERVICE_DUMP_RAW_RESPONSE)
    assert hass.services.has_service(DOMAIN, SERVICE_FORCE_REFRESH)
    assert hass.services.has_service(DOMAIN, SERVICE_MIGRATE_FROM_LEGACY)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_FORCE_REFRESH, {}, blocking=True)
    assert err.value.translation_key == "no_loaded_entries"


async def test_one_request_for_all_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """All sensors of a hub are fetched in a single form-encoded request."""
    assert len(mock_api.requests) == 1
    assert len(mock_api.batches[0].split(",")) == len(init_integration.subentries)


async def test_setup_retries_on_api_error(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """An API failure during the first refresh puts the entry into setup retry."""
    mock_api.next_status = 500
    entry = hub_entry(["080000AABB08"])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_without_sensors_makes_no_request(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A hub without sensors loads without calling the API."""
    entry = hub_entry([])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert mock_api.requests == []


async def test_setup_fetches_only_uncached_sensors(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Readings younger than 60 s are served from the reload cache."""
    cache_readings(hass, {"010000AABB01": parse_device_reading(DOCUMENTED["ID01"])})
    entry = hub_entry(["010000AABB01", "080000AABB08"])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert mock_api.batches == ["080000AABB08"]


async def test_setup_partial_cache_api_error_retries(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """An API failure for the uncached sensors still puts the entry into setup retry."""
    cache_readings(hass, {"010000AABB01": parse_device_reading(DOCUMENTED["ID01"])})
    mock_api.next_status = 500
    entry = hub_entry(["010000AABB01", "080000AABB08"])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_fully_cached_makes_no_request(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A reload right after a flow does not query the API again."""
    cache_readings(hass, {"010000AABB01": parse_device_reading(DOCUMENTED["ID01"])})
    entry = hub_entry(["010000AABB01"])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert mock_api.requests == []


async def test_remove_config_entry_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, device_registry: dr.DeviceRegistry
) -> None:
    """Active sensors and the hub cannot be removed; stale devices can."""
    entry_id = init_integration.entry_id
    sensor = device_registry.async_get_device_by_identifier((DOMAIN, "080000AABB08"), entry_id)
    hub = device_registry.async_get_device_by_identifier((DOMAIN, f"hub_{entry_id}"), entry_id)
    stale = device_registry.async_get_or_create(
        config_entry_id=entry_id, identifiers={(DOMAIN, "990000AABB99")}
    )
    assert sensor is not None
    assert hub is not None
    assert not await async_remove_config_entry_device(hass, init_integration, sensor)
    assert not await async_remove_config_entry_device(hass, init_integration, hub)
    assert await async_remove_config_entry_device(hass, init_integration, stale)


async def test_dump_raw_response(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """dump_raw_response returns cached payloads without a new request unless asked."""
    response = await hass.services.async_call(
        DOMAIN, SERVICE_DUMP_RAW_RESPONSE, {}, blocking=True, return_response=True
    )
    assert response is not None
    assert response["success"] is True
    assert response["entries_count"] == 1
    data = response["data"][init_integration.entry_id]
    assert len(data["devices"]) == len(init_integration.subentries)
    assert "rate_limit_status" in data
    assert len(mock_api.requests) == 1

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DUMP_RAW_RESPONSE,
        {"config_entry_id": init_integration.entry_id, "refresh": True},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    assert len(mock_api.requests) == 2


async def test_force_refresh(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """force_refresh polls the API through the rate-limit guard."""
    await hass.services.async_call(DOMAIN, SERVICE_FORCE_REFRESH, {}, blocking=True)
    await hass.async_block_till_done()
    assert len(mock_api.requests) == 2


@pytest.mark.parametrize("status", [500, 429])
async def test_force_refresh_failure_raises(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: MockMobileAlertsServer,
    status: int,
) -> None:
    """A poll that gets no new data (API error or rate limit) raises a translated error."""
    mock_api.next_status = status
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_FORCE_REFRESH, {}, blocking=True)
    assert err.value.translation_key == "refresh_failed"
    assert err.value.translation_placeholders == {"hubs": init_integration.title}

    if status == 500:
        # The next poll works again and the action succeeds. (A 429 keeps the 7 minute block.)
        await hass.services.async_call(DOMAIN, SERVICE_FORCE_REFRESH, {}, blocking=True)


async def test_dump_with_refresh_failure_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """dump_raw_response with refresh raises when the poll fails instead of dumping old data."""
    mock_api.next_status = 500
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DUMP_RAW_RESPONSE,
            {"refresh": True},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "refresh_failed"


async def test_force_refresh_skips_hub_without_sensors(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A hub without sensors makes no request and does not fail."""
    await _setup(hass, hub_entry([], entry_id="empty_hub"))
    await hass.services.async_call(DOMAIN, SERVICE_FORCE_REFRESH, {}, blocking=True)
    assert mock_api.requests == []


async def test_actions_reject_unknown_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An entry ID that is not loaded raises a translated validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_FORCE_REFRESH, {"config_entry_id": "missing_hub"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_loaded"


async def test_actions_reject_entry_id_field(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """Actions select a hub only through `config_entry_id`; an `entry_id` field is rejected."""
    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_FORCE_REFRESH, {"entry_id": init_integration.entry_id}, blocking=True
        )
    assert len(mock_api.requests) == 1


async def test_migrate_requires_target_with_several_hubs(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """With several hubs loaded, migrate_from_legacy needs an explicit entry."""
    second = hub_entry([], entry_id="second_hub")
    await _setup(hass, second)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_MIGRATE_FROM_LEGACY, {}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "multiple_entries_loaded"
    # Read-only actions cover all loaded hubs.
    response = await hass.services.async_call(
        DOMAIN, SERVICE_DUMP_RAW_RESPONSE, {}, blocking=True, return_response=True
    )
    assert response is not None
    assert response["entries_count"] == 2


async def test_migrate_rejects_invalid_sensor_ids(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """Invalid IDs in the migration filter fail locally, without echoing the IDs."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MIGRATE_FROM_LEGACY,
            {"device_ids": "010000AABB01, nothex"},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "invalid_device_ids"
    assert err.value.translation_placeholders == {"count": "1"}
    assert len(mock_api.requests) == 1


async def test_setup_retry_is_translated(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A failed first refresh reports a translated reason."""
    mock_api.next_status = 500
    entry = hub_entry(["080000AABB08"])
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None
    assert "Mobile Alerts API" in entry.reason


async def test_sensor_missing_from_api_raises_and_clears_repair_issue(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A sensor the API has not returned for 24 h gets a repair issue, deleted on return."""
    entry = hub_entry(["010000AABB01", "080000AABB08"])
    await _setup(hass, entry)
    coordinator = entry.runtime_data.coordinator
    issue_id = "sensor_missing_sub_080000aabb08"
    payload = mock_api.devices.pop("080000AABB08")

    await coordinator.async_refresh()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    freezer.tick(24 * 3600 + 60)
    await coordinator.async_refresh()
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "sensor_missing"
    assert issue.translation_placeholders == {"sensor": "Sensor 080000AABB08", "hub": entry.title}

    mock_api.devices["080000AABB08"] = payload
    await coordinator.async_refresh()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_orphaned_repair_issue_deleted_on_setup(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, issue_registry: ir.IssueRegistry
) -> None:
    """Issues of sensors no hub contains any more are removed when a hub sets up."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "sensor_missing_sub_gone",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="sensor_missing",
        translation_placeholders={"sensor": "x", "hub": "y"},
    )
    await _setup(hass, hub_entry(["010000AABB01"]))
    assert issue_registry.async_get_issue(DOMAIN, "sensor_missing_sub_gone") is None


async def test_deleting_sensor_subentry_removes_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Removing a sensor from the hub removes its device and entities (stale-devices)."""
    entry_id = init_integration.entry_id
    assert device_registry.async_get_device_by_identifier((DOMAIN, "080000AABB08"), entry_id)
    assert hass.config_entries.async_remove_subentry(init_integration, "sub_080000aabb08")
    await hass.async_block_till_done()
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, "080000AABB08"), entry_id) is None
    )
    assert not [
        e
        for e in er.async_entries_for_config_entry(entity_registry, entry_id)
        if e.unique_id.startswith("080000AABB08_")
    ]
    assert init_integration.state is ConfigEntryState.LOADED
