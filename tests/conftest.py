"""Shared fixtures for the Mobile Alerts Cloud test suite (real Home Assistant core)."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

TESTS_DIR = Path(__file__).parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from mock_api_server import FIXTURE_PATH, MockMobileAlertsServer  # noqa: E402

from custom_components.mobilealerts_cloud.api import client as api_client  # noqa: E402
from custom_components.mobilealerts_cloud.const import (  # noqa: E402
    CONF_DEVICE_ID,
    CONF_MODEL_AUTO,
    CONF_MODEL_OVERRIDE,
    CONF_NAME_PREFIX,
    CONF_PHONE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SEND_PHONE_ID,
    DOMAIN,
    SUBENTRY_TYPE_SENSOR,
)

ENTRY_ID = "01JMANGTESTENTRY000000000000"


def point_client_at(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point every `MobileAlertsClient` created without an explicit URL at `url`."""
    monkeypatch.setattr(api_client, "DEFAULT_API_URL", url)


def load_fixtures() -> dict[str, Any]:
    """Return the anonymised API payload fixtures."""
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


DOCUMENTED = load_fixtures()["all_documented_profiles"]
ALL_DOCUMENTED_IDS: list[str] = [record["deviceid"] for record in DOCUMENTED.values()]
SENTINEL_ID = load_fixtures()["edge_cases"]["sentinels_and_lowbat"]["deviceid"]


def sensor_subentry(
    device_id: str,
    title: str | None = None,
    *,
    model_override: str = CONF_MODEL_AUTO,
) -> ConfigSubentryDataWithId:
    """Return subentry data for one sensor with a deterministic subentry ID."""
    return ConfigSubentryDataWithId(
        data={CONF_DEVICE_ID: device_id, CONF_MODEL_OVERRIDE: model_override},
        subentry_id=f"sub_{device_id.lower()}",
        subentry_type=SUBENTRY_TYPE_SENSOR,
        title=title or f"Sensor {device_id}",
        unique_id=device_id,
    )


def hub_entry(
    device_ids: list[str],
    *,
    entry_id: str = ENTRY_ID,
    title: str = "Mobile Alerts Cloud",
    name_prefix: str = "",
    phone_id: str = "",
) -> MockConfigEntry:
    """Return a hub config entry with one sensor subentry per device ID."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={CONF_PHONE_ID: phone_id, CONF_SEND_PHONE_ID: False},
        options={CONF_SCAN_INTERVAL: 10, CONF_NAME_PREFIX: name_prefix},
        entry_id=entry_id,
        subentries_data=[sensor_subentry(dev_id) for dev_id in device_ids],
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load `custom_components/mobilealerts_cloud` in every test."""


# Fixture payloads report `lastseen` between 2026-11-19 19:50 and 19:53 UTC; ID08 (a captured
# live shape) reported at 18:49:18. Tests run at this fixed time so that every fixture sensor is
# fresh (ADR-005); staleness tests move the clock on.
FIXTURE_NOW = "2026-11-19T19:45:00+00:00"


@pytest.fixture(autouse=True)
def _freeze_at_fixture_time(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Freeze the clock at the time of the anonymised fixture payloads."""
    freezer.move_to(FIXTURE_NOW)
    return freezer


@pytest.fixture(autouse=True)
def _allow_localhost_sockets(socket_enabled: None) -> None:
    """Allow real sockets so tests can talk to the local mock API.

    `pytest-homeassistant-custom-component` still restricts connections to 127.0.0.1.
    """


@pytest.fixture
async def mock_api(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[MockMobileAlertsServer]:
    """Start the mock API on a free localhost port and point the integration at it.

    Timestamps come from the fixture, so states and snapshots are deterministic.
    """
    server = MockMobileAlertsServer(port=0, live_timestamps=False)
    await server.start()
    point_client_at(monkeypatch, server.url)
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def mock_config_entry(mock_api: MockMobileAlertsServer) -> MockConfigEntry:
    """Return a hub with every documented profile plus the sentinel edge case."""
    return hub_entry([*ALL_DOCUMENTED_IDS, SENTINEL_ID])


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration against the mock API and return the entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def entity_id_for(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """Return the entity ID for a unique ID; tests never hard-code entity IDs."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity for {unique_id}"
    return entity_id


@pytest.fixture
def mock_setup_entry() -> Generator[Any]:
    """Prevent the integration from being set up during config flow tests."""
    with patch(
        "custom_components.mobilealerts_cloud.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default, so snapshots cover all of them."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return snapshot assertion fixture with the Home Assistant extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
