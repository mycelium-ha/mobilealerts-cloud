"""Sensor, binary sensor and event entities against real Home Assistant and the mock API."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from mock_api_server import MockMobileAlertsServer
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.mobilealerts_cloud import _transmit_intervals, binary_sensor, event, sensor
from custom_components.mobilealerts_cloud.const import EVENT_KEY_PRESS
from custom_components.mobilealerts_cloud.coordinator import stale_after_seconds

from .conftest import SENTINEL_ID, entity_id_for, hub_entry, sensor_subentry


def _state(hass: HomeAssistant, platform: str, unique_id: str) -> Any:
    state = hass.states.get(entity_id_for(hass, platform, unique_id))
    assert state is not None
    return state


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one coordinator refresh against the mock API."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_values(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Sensors report parsed values with units; raw JSON never lands in attributes."""
    assert _state(hass, "sensor", "080000AABB08_t1").state == "10.6"
    rain = _state(hass, "sensor", "080000AABB08_r")
    assert rain.state == "1196.862"
    assert rain.attributes["unit_of_measurement"] == "mm"
    assert rain.attributes["state_class"] == "total_increasing"
    assert _state(hass, "sensor", "080000AABB08_rf").state == "4639"

    # The API reports m/s; Home Assistant shows wind speed in km/h on metric systems.
    wind = _state(hass, "sensor", "0B0000AABB0B_ws")
    assert (wind.state, wind.attributes["unit_of_measurement"]) == ("15.12", "km/h")
    assert _state(hass, "sensor", "0B0000AABB0B_wg").state == "31.32"
    assert _state(hass, "sensor", "0B0000AABB0B_wd").state == "se"
    assert _state(hass, "sensor", "0B0000AABB0B_wd_degrees").state == "135.0"

    pressure = _state(hass, "sensor", "180000AABB18_ap")
    assert pressure.state == "1019.8"
    assert pressure.attributes["unit_of_measurement"] == "hPa"
    co2 = _state(hass, "sensor", "050000AABB05_ppm")
    assert co2.state == "815.0"
    assert co2.attributes["unit_of_measurement"] == "ppm"

    assert _state(hass, "sensor", "080000AABB08_last_seen").state == "2026-11-19T18:49:18+00:00"

    # Sentinels become unknown while the entity stays available.
    for key in ("t2", "t3", "t4", "h2", "h3"):
        assert _state(hass, "sensor", f"{SENTINEL_ID}_{key}").state == STATE_UNKNOWN
    assert _state(hass, "sensor", f"{SENTINEL_ID}_h1").state == "50.0"

    attributes = set(rain.attributes)
    assert "measurement" not in attributes
    assert "raw" not in attributes


@pytest.mark.parametrize(
    "unique_id",
    ["080000AABB08_rr", "080000AABB08_rf", "150000AABB15_kp1c", "150000AABB15_kp4c"],
)
async def test_rarely_used_and_duplicate_sensors_disabled_by_default(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    unique_id: str,
) -> None:
    """Rain increment, raw tip counter and press counters (duplicates of events) start disabled."""
    entry = entity_registry.async_get(entity_id_for(hass, "sensor", unique_id))
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_binary_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """Water, contact, inverted AC power, smoke and battery follow the API values."""
    assert _state(hass, "binary_sensor", "040000AABB04_water").state == STATE_ON
    assert _state(hass, "binary_sensor", "100000AABB10_contact").state == STATE_ON
    # ID17 t2 == 0.0 means AC is on.
    assert _state(hass, "binary_sensor", "170000AABB17_ac_power").state == STATE_ON
    assert _state(hass, "binary_sensor", "0A0000AABB0A_smoke_1").state == STATE_OFF
    assert _state(hass, "binary_sensor", "0A0000AABB0A_smoke_2").state == STATE_ON
    assert _state(hass, "binary_sensor", f"{SENTINEL_ID}_battery").state == STATE_ON

    ac = mock_api.devices["170000AABB17"]
    ac["measurement"]["t2"] = 1.0
    ac["lowbat"] = True
    smoke = mock_api.devices["0A0000AABB0A"]
    smoke["measurement"] = {
        k: v for k, v in smoke["measurement"].items() if k not in ("a1", "a2", "a3", "a4")
    }
    await _poll(hass, init_integration)
    assert _state(hass, "binary_sensor", "170000AABB17_ac_power").state == STATE_OFF
    assert _state(hass, "binary_sensor", "170000AABB17_battery").state == STATE_ON
    # Missing alert flags are unknown, not off.
    assert _state(hass, "binary_sensor", "0A0000AABB0A_smoke_2").state == STATE_UNKNOWN

    ac["measurement"]["t2"] = 43530
    await _poll(hass, init_integration)
    assert _state(hass, "binary_sensor", "170000AABB17_ac_power").state == STATE_UNKNOWN


async def test_button_events_fire_only_on_counter_change(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """ID15 events never fire on startup or unchanged counters, only when kpXc changes."""
    bus_events = async_capture_events(hass, EVENT_KEY_PRESS)
    button_1 = entity_id_for(hass, "event", "150000AABB15_button_1")
    button_2 = entity_id_for(hass, "event", "150000AABB15_button_2")
    assert hass.states.get(button_1).state == STATE_UNKNOWN

    await _poll(hass, init_integration)  # unchanged counters
    assert hass.states.get(button_1).state == STATE_UNKNOWN
    assert bus_events == []

    measurement = mock_api.devices["150000AABB15"]["measurement"]
    measurement["kp1c"] = 23
    measurement["kp1t"] = 1
    await _poll(hass, init_integration)

    state = hass.states.get(button_1)
    assert state.attributes["event_type"] == "short_press"
    assert state.attributes["count"] == 23
    assert hass.states.get(button_2).state == STATE_UNKNOWN
    assert [e.data for e in bus_events] == [
        {
            "device_id": "150000AABB15",
            "channel": "button_1",
            "press_type": "short_press",
            "count": 23,
        }
    ]


async def test_battery_swap_counter_reset_fires_no_event(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """A counter reset to 0 after a battery change does not produce a phantom press."""
    bus_events = async_capture_events(hass, EVENT_KEY_PRESS)
    measurement = mock_api.devices["150000AABB15"]["measurement"]
    for key in ("kp1c", "kp2c", "kp3c", "kp4c"):
        measurement[key] = 0
    await _poll(hass, init_integration)
    assert bus_events == []
    assert hass.states.get(entity_id_for(hass, "event", "150000AABB15_button_1")).state == (
        STATE_UNKNOWN
    )


async def test_entities_per_subentry(
    hass: HomeAssistant, init_integration: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    """Every entity belongs to its sensor's subentry; platforms are push-free (no parallel)."""
    by_subentry: dict[tuple[str, str | None], int] = {}
    for entry in er.async_entries_for_config_entry(entity_registry, init_integration.entry_id):
        key = (entry.domain, entry.config_subentry_id)
        by_subentry[key] = by_subentry.get(key, 0) + 1
    # ID04: t1, h, last_seen sensors + water, battery binary sensors
    assert by_subentry[("sensor", "sub_040000aabb04")] == 3
    assert by_subentry[("binary_sensor", "sub_040000aabb04")] == 2
    assert ("event", "sub_040000aabb04") not in by_subentry
    # ID15: kp1c..kp4c + last_seen sensors, battery, four button events
    assert by_subentry[("sensor", "sub_150000aabb15")] == 5
    assert by_subentry[("binary_sensor", "sub_150000aabb15")] == 1
    assert by_subentry[("event", "sub_150000aabb15")] == 4
    assert sensor.PARALLEL_UPDATES == 0
    assert binary_sensor.PARALLEL_UPDATES == 0
    assert event.PARALLEL_UPDATES == 0


async def test_rate_limit_keeps_cached_values(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """HTTP 429 keeps the last values instead of making entities unavailable."""
    mock_api.next_status = 429
    await _poll(hass, init_integration)
    assert init_integration.runtime_data.coordinator.last_update_success is True
    assert _state(hass, "sensor", "080000AABB08_t1").state == "10.6"
    # The cooldown is active now: the next poll keeps the cached data without a request.
    requests_before = len(mock_api.requests)
    await _poll(hass, init_integration)
    assert len(mock_api.requests) == requests_before
    assert _state(hass, "sensor", "080000AABB08_t1").state == "10.6"


async def test_api_error_makes_entities_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MockMobileAlertsServer
) -> None:
    """A failed poll (server error) marks entities unavailable until the next success."""
    mock_api.next_status = 500
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "080000AABB08_t1").state == STATE_UNAVAILABLE
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "080000AABB08_t1").state == "10.6"


async def test_name_prefix_applies_to_device_names(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """The optional name prefix is prepended to sensor device names."""
    entry = hub_entry(["080000AABB08"], name_prefix="Test ")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = _state(hass, "sensor", "080000AABB08_r")
    assert state.attributes["friendly_name"].startswith("Test Sensor 080000AABB08")


async def test_unknown_prefix_uses_dynamic_profile(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A sensor with an undocumented prefix gets entities from its measurement keys."""
    payload = copy.deepcopy(mock_api.devices["030000AABB03"])
    payload["deviceid"] = "990000AABB03"
    mock_api.devices["990000AABB03"] = payload
    entry = hub_entry(["990000AABB03"])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "990000AABB03_t1").state == payload["measurement"]["t1"].__str__()
    assert _state(hass, "sensor", "990000AABB03_h").state is not None


# ID08 (rain gauge, MA 10650) last reported at 18:49:18 UTC on 2026-11-19 (epoch 1795114158).
RAIN_GAUGE_LAST_SEEN = 1795114158


def test_stale_after_combines_poll_interval_and_transmit_interval() -> None:
    """The threshold is the largest of the 60 minute floor, 3 polls and 2.5 transmit intervals."""
    assert stale_after_seconds(10) == 3600
    assert stale_after_seconds(30) == 5400
    assert stale_after_seconds(10, 7200) == 18000
    assert stale_after_seconds(10, 300) == 3600
    assert stale_after_seconds(60, 7200) == 18000  # 3 x 60 min = 3 h < 2.5 x 2 h = 5 h


async def test_rain_gauge_stays_available_for_five_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: MockMobileAlertsServer,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The rain gauge transmits about every 2 hours, so it stays available up to 5 hours (ADR-010)."""
    coordinator = init_integration.runtime_data.coordinator
    assert coordinator.stale_after_for("080000AABB08") == 5 * 3600
    # 61 minutes after the last report: the 60 minute default would have marked it stale.
    freezer.move_to("2026-11-19T19:49:40+00:00")
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "080000AABB08_t1").state != STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "080000AABB08_battery").state != STATE_UNAVAILABLE

    # Beyond five hours the sensor counts as stale again, and the diagnostic last_seen stays on.
    freezer.move_to("2026-11-19T23:49:19+00:00")  # 5 hours and 1 second after the last report
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "080000AABB08_t1").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "080000AABB08_last_seen").state == "2026-11-19T18:49:18+00:00"


async def test_stale_sensor_becomes_unavailable_and_recovers(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: MockMobileAlertsServer,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A sensor whose lastseen is older than max(60 min, 3 x poll) is unavailable (ADR-005)."""
    # Default poll interval 10 min -> threshold 60 min. A 7-minute sensor (ID01) that last
    # reported at 18:49:18 is stale at 19:49:40; the rain gauge is not (see ADR-010).
    # lastseen is the max of lastseen and the measurement time c, so both must be old.
    mock_api.devices["010000AABB01"]["lastseen"] = RAIN_GAUGE_LAST_SEEN
    mock_api.devices["010000AABB01"]["measurement"]["c"] = RAIN_GAUGE_LAST_SEEN
    assert init_integration.runtime_data.coordinator.stale_after_for("010000AABB01") == 3600
    freezer.move_to("2026-11-19T19:49:40+00:00")
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "010000AABB01_t1").state == STATE_UNAVAILABLE
    # The diagnostic last_seen sensor stays available so users see when it last reported.
    assert _state(hass, "sensor", "010000AABB01_last_seen").state == "2026-11-19T18:49:18+00:00"

    await _poll(hass, init_integration)
    stale_warnings = [r for r in caplog.records if "has not reported" in r.getMessage()]
    assert len(stale_warnings) == 1
    # Logs name the sensor by its subentry title, never by a bare sensor ID.
    assert stale_warnings[0].getMessage().startswith("Sensor Sensor 010000AABB01 has not")

    mock_api.devices["010000AABB01"]["lastseen"] = 1795117700  # 19:48:20
    mock_api.devices["010000AABB01"]["measurement"]["c"] = 1795117700
    await _poll(hass, init_integration)
    assert _state(hass, "sensor", "010000AABB01_t1").state != STATE_UNAVAILABLE
    assert any("reports again" in r.getMessage() for r in caplog.records)


def test_transmit_interval_follows_model_override() -> None:
    """A profile override decides the transmit interval, as it decides the channels (ADR-010)."""
    subentries = [
        SimpleNamespace(data={"device_id": "010000AABB01", "model_override": "ID08"}),
        SimpleNamespace(data={"device_id": "080000AABB08"}),
        SimpleNamespace(data={"device_id": "010000AABB02", "model_override": "auto"}),
    ]
    assert _transmit_intervals(subentries) == {"010000AABB01": 7200, "080000AABB08": 7200}


async def test_model_override_to_rain_gauge_extends_stale_threshold(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, freezer: FrozenDateTimeFactory
) -> None:
    """A model override to ID08 on another prefix makes the entity stale only after 5 hours."""
    mock_api.devices["010000AABB01"]["lastseen"] = RAIN_GAUGE_LAST_SEEN
    mock_api.devices["010000AABB01"]["measurement"]["c"] = RAIN_GAUGE_LAST_SEEN
    template = hub_entry([])  # hub defaults; the one sensor subentry carries the override
    entry = MockConfigEntry(
        domain=template.domain,
        title=template.title,
        data=dict(template.data),
        options=dict(template.options),
        entry_id=template.entry_id,
        subentries_data=[sensor_subentry("010000AABB01", model_override="ID08")],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.coordinator.stale_after_for("010000AABB01") == 5 * 3600

    # 61 minutes after the last report: the ID01 default of 60 minutes no longer applies.
    freezer.move_to("2026-11-19T19:49:40+00:00")
    await _poll(hass, entry)
    assert _state(hass, "sensor", "010000AABB01_t1").state != STATE_UNAVAILABLE

    freezer.move_to("2026-11-19T23:49:19+00:00")  # 5 hours and 1 second after the last report
    await _poll(hass, entry)
    assert _state(hass, "sensor", "010000AABB01_t1").state == STATE_UNAVAILABLE


async def test_rain_gauge_stale_warning_names_five_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: MockMobileAlertsServer,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The stale warning for the rain gauge states its own 300 minute threshold."""

    def rain_gauge_warnings() -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if "has not reported" in r.getMessage() and "080000AABB08" in r.getMessage()
        ]

    # 61 minutes after the last report the rain gauge is still fresh: no warning yet.
    freezer.move_to("2026-11-19T19:49:40+00:00")
    await _poll(hass, init_integration)
    assert rain_gauge_warnings() == []

    # 5 hours and 1 second after the last report: exactly one warning with the 300 minute threshold.
    freezer.move_to("2026-11-19T23:49:19+00:00")
    await _poll(hass, init_integration)
    stale_warnings = rain_gauge_warnings()
    assert len(stale_warnings) == 1
    assert "more than 300 minutes" in stale_warnings[0]


async def test_stale_threshold_follows_poll_interval(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """With a 30 minute poll interval the threshold is 90 minutes."""
    entry = hub_entry(["010000AABB01"])
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "scan_interval": 30})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.coordinator.stale_after_for("010000AABB01") == 5400


async def test_rate_limit_warning_logged_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: MockMobileAlertsServer,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rate limit warns once, stays quiet while it lasts and logs its end."""
    mock_api.next_status = 429
    await _poll(hass, init_integration)
    await _poll(hass, init_integration)
    warnings = [r for r in caplog.records if "rate limit reached" in r.getMessage()]
    assert len(warnings) == 1
    freezer.tick(8 * 60)  # 429 blocks for 7 minutes
    await _poll(hass, init_integration)
    assert any("work again" in r.getMessage() for r in caplog.records)


async def test_button_events_baseline_and_unknown_values(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A switch first missing from the API sets its baseline later; odd values fire nothing."""
    bus_events = async_capture_events(hass, EVENT_KEY_PRESS)
    payload = mock_api.devices.pop("150000AABB15")
    entry = hub_entry(["010000AABB01", "150000AABB15"])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    button_1 = entity_id_for(hass, "event", "150000AABB15_button_1")
    assert hass.states.get(button_1).state == STATE_UNAVAILABLE

    measurement = payload["measurement"]
    counter = measurement.pop("kp1c")
    mock_api.devices["150000AABB15"] = payload
    await _poll(hass, entry)  # no counter yet
    measurement["kp1c"] = counter
    await _poll(hass, entry)  # first counter: baseline only
    measurement["kp1c"] = counter + 1
    measurement["kp1t"] = 99  # unknown press type
    await _poll(hass, entry)
    assert bus_events == []
    assert hass.states.get(button_1).state == STATE_UNKNOWN


async def test_rate_limit_without_cached_data_retries_setup(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A 429 on the very first poll has no cache to fall back on: the setup is retried."""
    mock_api.next_status = 429
    entry = hub_entry(["080000AABB08"])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
