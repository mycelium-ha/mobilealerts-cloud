"""Hub config flow, reconfigure, options and the sensor subentry flow (real Home Assistant)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from mock_api_server import MockMobileAlertsServer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobilealerts_cloud.api import (
    MobileAlertsConnectionError,
    MobileAlertsError,
    MobileAlertsValidationError,
)
from custom_components.mobilealerts_cloud.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_IMPORT_LEGACY,
    CONF_MODEL_AUTO,
    CONF_MODEL_OVERRIDE,
    CONF_NAME,
    CONF_NAME_PREFIX,
    CONF_PHONE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SEND_PHONE_ID,
    DOMAIN,
    LEGACY_DOMAIN,
    SUBENTRY_TYPE_SENSOR,
)

from .conftest import entity_id_for, hub_entry, point_client_at


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_without_sensors(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """An empty hub is created without any API request."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Test Hub", CONF_NAME_PREFIX: "Test "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Hub"
    assert result["data"] == {CONF_PHONE_ID: "", CONF_SEND_PHONE_ID: False}
    assert result["options"] == {CONF_SCAN_INTERVAL: 10, CONF_NAME_PREFIX: "Test "}
    assert mock_api.requests == []
    await hass.async_block_till_done()


async def test_user_flow_with_initial_sensors_uses_one_request(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Initial sensors are validated in one batch; setup reuses the reload cache."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Hub",
            CONF_DEVICE_IDS: "010000aabb01,\n080000AABB08",
            CONF_SCAN_INTERVAL: 15,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert mock_api.batches == ["010000AABB01,080000AABB08"]
    assert mock_api.requests[0]["content_type"].startswith("application/x-www-form-urlencoded")
    entry = result["result"]
    assert sorted(sub.unique_id for sub in entry.subentries.values()) == [
        "010000AABB01",
        "080000AABB08",
    ]
    assert {sub.title for sub in entry.subentries.values()} == {"MA 10101 BB01", "MA 10650 BB08"}
    # The entry was set up from the reload cache: still exactly one API request.
    assert hass.states.get(entity_id_for(hass, "sensor", "080000AABB08_r")) is not None
    assert len(mock_api.requests) == 1


@pytest.mark.parametrize(
    ("user_input", "field", "error"),
    [
        ({CONF_DEVICE_IDS: "BAD_ID"}, CONF_DEVICE_IDS, "invalid_device_id_format"),
        ({CONF_PHONE_ID: "abc"}, CONF_PHONE_ID, "invalid_phone_id"),
        ({CONF_DEVICE_IDS: "AAAAAAAAAAAA"}, CONF_DEVICE_IDS, "device_not_found"),
    ],
)
async def test_user_flow_field_errors(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    user_input: dict[str, Any],
    field: str,
    error: str,
) -> None:
    """Invalid input is rejected with a field error; the flow can then finish."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}
    if error != "device_not_found":
        assert mock_api.requests == []

    # The form keeps the rejected value as default; clearing it lets the flow finish.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {field: ""})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, "rate_limited"), (403, "ip_blocked"), (500, "api_error")],
)
async def test_user_flow_api_errors(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    freezer: FrozenDateTimeFactory,
    status: int,
    error: str,
) -> None:
    """API failures map to translated base errors; the flow finishes once the API answers."""
    mock_api.next_status = status
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Rate limit (7 min) and IP block (15 min) end; then the same input succeeds.
    freezer.tick(timedelta(minutes=16))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real connection failure maps to cannot_connect."""
    point_client_at(monkeypatch, "http://127.0.0.1:1/api/pv1/device/lastmeasurement")
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (MobileAlertsConnectionError("down"), "cannot_connect"),
        (MobileAlertsValidationError("bad"), "invalid_device_id_format"),
        (MobileAlertsError("other"), "unknown"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_client_errors_recover(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    side_effect: Exception,
    error: str,
) -> None:
    """Client errors map to base errors, and the flow finishes on the next attempt."""
    result = await _start_user_flow(hass)
    with patch(
        "custom_components.mobilealerts_cloud.config_flow.MobileAlertsClient.fetch_measurements",
        side_effect=side_effect,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
        )
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_user_flow_rejects_sensor_configured_on_another_hub(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A sensor ID can exist on only one hub (ADR-004)."""
    hub_entry(["080000AABB08"], entry_id="other_hub").add_to_hass(hass)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "080000AABB08"}
    )
    assert result["errors"] == {CONF_DEVICE_IDS: "already_configured"}
    assert mock_api.requests == []

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "010000AABB01"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_reconfigure_flow(hass: HomeAssistant, mock_api: MockMobileAlertsServer) -> None:
    """Reconfigure validates and stores the phone ID settings."""
    entry = hub_entry([])
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == SOURCE_RECONFIGURE

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE_ID: "invalid", CONF_SEND_PHONE_ID: True}
    )
    assert result["errors"] == {CONF_PHONE_ID: "invalid_phone_id"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE_ID: "100000000001", CONF_SEND_PHONE_ID: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_PHONE_ID: "100000000001", CONF_SEND_PHONE_ID: True}


async def test_options_flow(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Options update the polling interval and name prefix."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_NAME_PREFIX: "Test "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_SCAN_INTERVAL: 5, CONF_NAME_PREFIX: "Test "}
    await hass.async_block_till_done()


async def _start_subentry_flow(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SENSOR), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result


async def _setup_hub(hass: HomeAssistant, device_ids: list[str]) -> MockConfigEntry:
    entry = hub_entry(device_ids)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_subentry_flow_adds_sensor(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Adding a sensor validates it once and creates its entities after the reload."""
    entry = await _setup_hub(hass, ["040000AABB04"])
    requests_before = len(mock_api.requests)
    result = await _start_subentry_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: "080000aabb08", CONF_NAME: "Garden Rain", CONF_MODEL_OVERRIDE: "auto"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garden Rain"
    await hass.async_block_till_done()

    assert len(mock_api.requests) == requests_before + 1
    subentry = next(s for s in entry.subentries.values() if s.unique_id == "080000AABB08")
    assert subentry.data[CONF_DEVICE_ID] == "080000AABB08"
    state = hass.states.get(entity_id_for(hass, "sensor", "080000AABB08_r"))
    assert state is not None
    assert state.state == "1196.862"


@pytest.mark.parametrize(
    ("device_id", "field", "error"),
    [
        ("XYZ", CONF_DEVICE_ID, "invalid_device_id_format"),
        ("AAAAAAAAAAAA", CONF_DEVICE_ID, "device_not_found"),
        ("030000AAAA00", "base", "device_offline_no_data"),
    ],
)
async def test_subentry_flow_errors(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    device_id: str,
    field: str,
    error: str,
) -> None:
    """Invalid, unknown and empty sensors are rejected."""
    mock_api.devices["030000AAAA00"] = {"deviceid": "030000AAAA00"}
    entry = await _setup_hub(hass, [])
    result = await _start_subentry_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "080000AABB08"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_subentry_flow_api_error(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, freezer: FrozenDateTimeFactory
) -> None:
    """An API failure shows a base error; the sensor is added once the block ends."""
    entry = await _setup_hub(hass, [])
    mock_api.next_status = 429
    result = await _start_subentry_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "080000AABB08"}
    )
    assert result["errors"] == {"base": "rate_limited"}

    freezer.tick(timedelta(minutes=8))
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "080000AABB08"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_subentry_flow_unexpected_error_recovers(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """An unexpected exception shows the generic error; the next attempt adds the sensor."""
    entry = await _setup_hub(hass, [])
    result = await _start_subentry_flow(hass, entry)
    with patch(
        "custom_components.mobilealerts_cloud.config_flow.MobileAlertsClient.fetch_measurements",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_DEVICE_ID: "080000AABB08"}
        )
    assert result["errors"] == {"base": "unknown"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "080000AABB08"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_subentry_flow_duplicate_aborts(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A sensor that is already configured aborts without an API request."""
    entry = await _setup_hub(hass, ["040000AABB04"])
    requests_before = len(mock_api.requests)
    result = await _start_subentry_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "040000aabb04"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(mock_api.requests) == requests_before


async def test_subentry_reconfigure_switches_profile_and_prunes_entities(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, entity_registry: er.EntityRegistry
) -> None:
    """Switching ID04 to the cable profile renames the device and removes the water entity."""
    entry = await _setup_hub(hass, ["040000AABB04"])
    water = entity_id_for(hass, "binary_sensor", "040000AABB04_water")
    subentry_id = "sub_040000aabb04"

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SENSOR),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"device_id": "040000AABB04"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_NAME: "Pool Cable", CONF_MODEL_OVERRIDE: "ID04_CABLE"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    subentry = entry.subentries[subentry_id]
    assert subentry.title == "Pool Cable"
    assert subentry.data[CONF_MODEL_OVERRIDE] == "ID04_CABLE"
    assert entry.runtime_data.subentry_profiles[subentry_id].profile_id == "ID04_CABLE"
    assert entity_registry.async_get(water) is None
    assert hass.states.get(entity_id_for(hass, "sensor", "040000AABB04_t2")) is not None
    assert entry.subentries[subentry_id].data[CONF_MODEL_OVERRIDE] != CONF_MODEL_AUTO


async def test_user_flow_legacy_sensor_unknown_to_api(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A legacy sensor the API does not know blocks the import with a base error."""
    MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="Gone",
        unique_id="990000AABB99",
        data={"device_id": "990000AABB99", "model_id": "MA10100"},
    ).add_to_hass(hass)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IMPORT_LEGACY: True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_not_found"}

    # Without the legacy import the hub can still be created.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IMPORT_LEGACY: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_ip_block_from_a_hub_poll_also_guards_the_setup_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """One rate-limit state per instance: an HTTP 403 seen by a hub stops every flow."""
    mock_api.next_status = 403
    await init_integration.runtime_data.coordinator.async_refresh()
    requests_after_block = len(mock_api.requests)

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_IDS: "0F0000FFFF0F"}
    )

    assert result["errors"] == {"base": "ip_blocked"}
    assert len(mock_api.requests) == requests_after_block
