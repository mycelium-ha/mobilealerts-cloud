"""Legacy `mobile_alerts` discovery, setup import and `migrate_from_legacy` (guard suite).

Runs against real Home Assistant registries. These tests protect the safety invariants in
docs/HA_INTEGRATION_STANDARDS.md section 8; AGENTS.md section 5.3 requires them to stay green.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from mock_api_server import MockMobileAlertsServer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobilealerts_cloud import get_rate_limit_state
from custom_components.mobilealerts_cloud.const import (
    CONF_DEVICE_IDS,
    CONF_IMPORT_LEGACY,
    CONF_MODEL_AUTO,
    CONF_MODEL_OVERRIDE,
    CONF_NAME,
    CONF_NAME_PREFIX,
    DOMAIN,
    LEGACY_DOMAIN,
    SERVICE_MIGRATE_FROM_LEGACY,
)
from custom_components.mobilealerts_cloud.migration import (
    _build_import_client,
    _lookup_device,
    parse_legacy_unique_id,
    suggest_model_override,
)

from .conftest import entity_id_for, hub_entry


def _legacy_entry(
    hass: HomeAssistant, device_id: str, title: str, model_id: str
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title=title,
        unique_id=device_id,
        data={"device_id": device_id, "model_id": model_id},
        entry_id=f"leg_{device_id.lower()}",
    )
    entry.add_to_hass(hass)
    return entry


def _legacy_device(
    hass: HomeAssistant, entry: MockConfigEntry, device_id: str, **updates: Any
) -> dr.DeviceEntry:
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(LEGACY_DOMAIN, device_id)},
        name=entry.title,
    )
    if updates:
        device = dev_reg.async_update_device(device.id, **updates) or device
    return device


def _legacy_entity(
    hass: HomeAssistant,
    unique_id: str,
    object_id: str,
    *,
    entry: MockConfigEntry | None = None,
    device: dr.DeviceEntry | None = None,
    options: dict[str, dict[str, Any]] | None = None,
    **updates: Any,
) -> str:
    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device.id if device else None,
        suggested_object_id=object_id,
    )
    if updates:
        ent_reg.async_update_entity(reg_entry.entity_id, **updates)
    for opt_domain, values in (options or {}).items():
        ent_reg.async_update_entity_options(reg_entry.entity_id, opt_domain, values)
    return reg_entry.entity_id


async def _setup_hub(
    hass: HomeAssistant, device_ids: list[str], *, name_prefix: str = ""
) -> MockConfigEntry:
    entry = hub_entry(device_ids, name_prefix=name_prefix)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _migrate(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN, SERVICE_MIGRATE_FROM_LEGACY, data, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    assert response is not None
    return dict(response)


def test_parse_legacy_unique_id_and_model_override() -> None:
    """Legacy unique IDs across all platforms and the MA10300 override parse accurately."""
    assert parse_legacy_unique_id("110000C4E8A1t1") == ("110000C4E8A1", "sensor", "t1")
    assert parse_legacy_unique_id("0800009d3b7erf") == ("0800009D3B7E", "sensor", "rf")
    assert parse_legacy_unique_id("0800009D3B7E_last_seen") == (
        "0800009D3B7E",
        "sensor",
        "last_seen",
    )
    assert parse_legacy_unique_id("0800009D3B7E_battery") == (
        "0800009D3B7E",
        "binary_sensor",
        "battery",
    )
    assert parse_legacy_unique_id("040000AABB04water") == (
        "040000AABB04",
        "binary_sensor",
        "water",
    )
    assert parse_legacy_unique_id("100000AABB10w") == ("100000AABB10", "binary_sensor", "contact")
    assert parse_legacy_unique_id("170000AABB17ac_power") == (
        "170000AABB17",
        "binary_sensor",
        "ac_power",
    )
    assert parse_legacy_unique_id("150000AABB15kp2t") == ("150000AABB15", "event", "button_2")
    assert parse_legacy_unique_id("150000AABB15kp2c") == ("150000AABB15", "sensor", "kp2c")
    assert parse_legacy_unique_id("short") is None
    assert parse_legacy_unique_id("ZZZZZZZZZZZZt1") is None
    assert parse_legacy_unique_id("0100005B2E9Dunknown_key") is None

    assert suggest_model_override("040000AABB04", "MA10300") == "ID04_CABLE"
    assert suggest_model_override("040000AABB04", "MA10350") == CONF_MODEL_AUTO
    assert suggest_model_override("0100005B2E9D", "MA10101") == CONF_MODEL_AUTO


async def test_config_flow_imports_legacy_devices(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Hub setup offers legacy sensors, validates them in one batch and keeps legacy intact."""
    leg_01 = _legacy_entry(hass, "010000AABB01", "Wireless Thermometer", "MA10101")
    _legacy_device(hass, leg_01, "010000AABB01", name_by_user="TFA-Bad-Nord")
    _legacy_entry(hass, "040000AABB04", "Cable Probe Legacy", "MA10300")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert CONF_IMPORT_LEGACY in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Mobile Alerts Cloud", CONF_IMPORT_LEGACY: True, CONF_NAME_PREFIX: "Test "},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert len(mock_api.requests) == 1
    assert set(mock_api.batches[0].split(",")) == {"010000AABB01", "040000AABB04"}
    by_id = {sub.unique_id: sub for sub in result["result"].subentries.values()}
    assert by_id["010000AABB01"].title == "TFA-Bad-Nord"
    assert by_id["040000AABB04"].title == "Cable Probe Legacy"
    assert by_id["040000AABB04"].data[CONF_MODEL_OVERRIDE] == "ID04_CABLE"
    assert len(hass.config_entries.async_entries(LEGACY_DOMAIN)) == 2


async def test_dry_run_and_side_by_side_metadata_sync(
    hass: HomeAssistant,
    mock_api: MockMobileAlertsServer,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """dry_run changes nothing; cutover=False copies metadata and keeps legacy entries."""
    area = ar.async_get(hass).async_create("Bad")
    outdoor = lr.async_get(hass).async_create("outdoor")
    climate = lr.async_get(hass).async_create("climate")
    leg_01 = _legacy_entry(hass, "010000AABB01", "Wireless Thermometer", "MA10101")
    leg_dev = _legacy_device(
        hass,
        leg_01,
        "010000AABB01",
        name_by_user="TFA-Bad-Nord",
        area_id=area.id,
        labels={outdoor.label_id},
    )
    _legacy_entity(
        hass,
        "010000AABB01t1",
        "badezimmer",
        entry=leg_01,
        device=leg_dev,
        name="Temperatur Bad",
        icon="mdi:thermometer",
        area_id=area.id,
        aliases={"Temperatur Bad"},
        labels={climate.label_id},
        disabled_by=er.RegistryEntryDisabler.USER,
        hidden_by=er.RegistryEntryHider.USER,
        options={
            "conversation": {"should_expose": True},
            "sensor": {"suggested_display_precision": 1, "display_precision": 2},
        },
    )
    _legacy_entity(
        hass,
        "010000AABB01_battery",
        "badezimmer_battery",
        entry=leg_01,
        device=leg_dev,
        name="Batterie Bad",
        disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY,
        options={"conversation": {"should_expose": False}},
    )
    hub = await _setup_hub(hass, ["010000AABB01"], name_prefix="Test ")
    ng_t1 = entity_id_for(hass, "sensor", "010000AABB01_t1")
    requests_before = len(mock_api.requests)

    preview = await _migrate(hass, dry_run=True, cutover=True)
    assert preview["success"] is True
    assert preview["dry_run"] is True
    assert preview["summary"]["same_domain_entities"] == 1
    assert preview["summary"]["domain_changed_entities"] == 1
    assert preview["summary"]["legacy_entries_to_remove"] == 1
    assert preview["entity_migrations"][0]["will_rename_entity_id"] is True
    assert preview["entity_migrations"][0]["history_preserved"] is True
    assert any("name_prefix" in w for w in preview["warnings"])
    assert any("domain" in w for w in preview["warnings"])
    # Strictly read-only: no registry change, no API call, legacy entry still there.
    assert entity_registry.async_get(ng_t1).name is None
    assert len(mock_api.requests) == requests_before
    assert hass.config_entries.async_get_entry(leg_01.entry_id) is not None

    sync = await _migrate(hass, dry_run=False, cutover=False)
    assert sync["success"] is True
    assert sync["cutover"] is False
    assert hass.config_entries.async_get_entry(leg_01.entry_id) is not None

    ng_dev = device_registry.async_get_device_by_identifier((DOMAIN, "010000AABB01"), hub.entry_id)
    assert ng_dev is not None
    assert ng_dev.name_by_user == "TFA-Bad-Nord"
    assert ng_dev.area_id == area.id
    assert ng_dev.labels == {outdoor.label_id}

    t1 = entity_registry.async_get(ng_t1)
    assert t1 is not None
    assert t1.entity_id == ng_t1  # no rename without cutover
    assert t1.name == "Temperatur Bad"
    assert t1.icon == "mdi:thermometer"
    assert "Temperatur Bad" in t1.aliases
    assert t1.labels == {climate.label_id}
    assert t1.disabled_by is er.RegistryEntryDisabler.USER
    assert t1.hidden_by is er.RegistryEntryHider.USER
    assert t1.options["conversation"] == {"should_expose": True}
    assert t1.options["sensor"]["display_precision"] == 2
    assert "suggested_display_precision" not in t1.options.get("sensor", {}) or (
        t1.options["sensor"]["suggested_display_precision"] != 1
    )

    battery = entity_registry.async_get(
        entity_id_for(hass, "binary_sensor", "010000AABB01_battery")
    )
    assert battery is not None
    assert battery.name == "Batterie Bad"
    # A disabled_by that the user did not set ("config_entry") is not copied.
    assert battery.disabled_by is None


async def test_cutover_imports_missing_and_renames_entity_ids(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, entity_registry: er.EntityRegistry
) -> None:
    """Cutover imports missing sensors, removes legacy entries and takes over entity IDs."""
    leg_01 = _legacy_entry(hass, "010000AABB01", "Bad Nord", "MA10101")
    leg_08 = _legacy_entry(hass, "080000AABB08", "Rain Legacy", "MA10650")
    _legacy_entity(
        hass,
        "010000AABB01t1",
        "badezimmer",
        entry=leg_01,
        name="Temperatur Bad",
        options={"conversation": {"should_expose": True}},
    )
    _legacy_entity(
        hass,
        "010000AABB01t2",
        "aussentemperatur_nord",
        entry=leg_01,
        name="Aussentemperatur Nord",
        options={"cloud.google_assistant": {"should_expose": True}},
    )
    _legacy_entity(
        hass,
        "080000AABB08r",
        "regensensor",
        entry=leg_08,
        name="Regenmenge gesamt",
        options={"sensor": {"suggested_display_precision": 0}},
    )
    # Orphaned YAML entities of an ID15 switch without a config entry.
    _legacy_entity(
        hass,
        "150000AABB15kp1t",
        "yaml_switch_press_type",
        name="Schalter Taste 1",
        options={"cloud.google_assistant": {"should_expose": True}},
    )
    _legacy_entity(hass, "150000AABB15kp1c", "yaml_switch_counter", name="Schalter Zähler 1")

    hub = await _setup_hub(hass, ["010000AABB01"])
    requests_before = len(mock_api.requests)

    result = await _migrate(hass, dry_run=False, cutover=True)
    assert result["success"] is True
    assert result["summary"]["imported_devices"] == 2
    assert result["summary"]["removed_legacy_entries"] == 2
    assert len(mock_api.requests) == requests_before + 1
    assert set(mock_api.batches[-1].split(",")) == {"080000AABB08", "150000AABB15"}
    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)
    assert {sub.unique_id for sub in hub.subentries.values()} == {
        "010000AABB01",
        "080000AABB08",
        "150000AABB15",
    }

    t1 = entity_registry.async_get("sensor.badezimmer")
    assert t1 is not None
    assert t1.platform == DOMAIN
    assert t1.unique_id == "010000AABB01_t1"
    assert t1.name == "Temperatur Bad"
    assert t1.options["conversation"] == {"should_expose": True}

    t2 = entity_registry.async_get("sensor.aussentemperatur_nord")
    assert t2 is not None
    assert t2.platform == DOMAIN
    assert t2.options["cloud.google_assistant"] == {"should_expose": True}

    rain = entity_registry.async_get("sensor.regensensor")
    assert rain is not None
    assert rain.platform == DOMAIN
    # The legacy precision default is dropped, so the Mobile Alerts Cloud default applies.
    assert "suggested_display_precision" not in rain.options.get("sensor", {}) or (
        rain.options["sensor"]["suggested_display_precision"] != 0
    )

    counter = entity_registry.async_get("sensor.yaml_switch_counter")
    assert counter is not None
    assert counter.platform == DOMAIN

    # The domain-changed button event keeps its own entity ID but gets name and options.
    button = entity_registry.async_get(entity_id_for(hass, "event", "150000AABB15_button_1"))
    assert button is not None
    assert button.name == "Schalter Taste 1"
    assert button.options["cloud.google_assistant"] == {"should_expose": True}
    # Domain-changed orphans (kp1t sensor -> event) are kept for manual cleanup;
    # only same-domain orphans are removed.
    assert entity_registry.async_get("sensor.yaml_switch_press_type") is not None


async def test_cutover_keeps_legacy_entry_when_import_fails(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """A legacy sensor the API does not return is not removed."""
    _legacy_entry(hass, "990000AABB99", "Offline Sensor", "MA10100")
    await _setup_hub(hass, ["010000AABB01"])

    result = await _migrate(hass, dry_run=False, cutover=True)
    assert result["success"] is False
    assert result["summary"]["failed_imports"] == 1
    assert result["summary"]["removed_legacy_entries"] == 0
    assert hass.config_entries.async_get_entry("leg_990000aabb99") is not None


async def test_cutover_api_error_is_translated_and_keeps_legacy(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """An API failure while importing raises a translated error without sensor IDs."""
    _legacy_entry(hass, "080000AABB08", "Regen", "MA10650")
    await _setup_hub(hass, ["010000AABB01"])
    mock_api.next_status = 500

    with pytest.raises(HomeAssistantError) as err:
        await _migrate(hass, dry_run=False, cutover=True)
    assert err.value.translation_key == "legacy_import_failed"
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["count"] == "1"
    assert "080000AABB08" not in str(err.value.translation_placeholders)
    assert hass.config_entries.async_get_entry("leg_080000aabb08") is not None


async def test_selective_filter_and_entity_id_conflict(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, entity_registry: er.EntityRegistry
) -> None:
    """device_ids limits the migration; an entity ID held by another integration is kept."""
    leg_01 = _legacy_entry(hass, "010000AABB01", "Bad Nord", "MA10101")
    _legacy_entry(hass, "080000AABB08", "Rain", "MA10650")
    _legacy_entity(hass, "010000AABB01t1", "taken_target", entry=leg_01, name="Temperatur Bad")
    await _setup_hub(hass, ["010000AABB01"])
    ng_t1 = entity_id_for(hass, "sensor", "010000AABB01_t1")

    with pytest.raises(ServiceValidationError):
        await _migrate(hass, dry_run=True, device_ids="NOT_VALID")

    # Another integration grabs the target entity ID right after the legacy entry is removed.
    original_remove = hass.config_entries.async_remove

    async def _remove_and_reclaim(entry_id: str) -> Any:
        result = await original_remove(entry_id)
        entity_registry.async_get_or_create(
            "sensor", "other_integration", "other_1", suggested_object_id="taken_target"
        )
        return result

    hass.config_entries.async_remove = _remove_and_reclaim  # type: ignore[method-assign]

    result = await _migrate(hass, dry_run=False, cutover=True, device_ids="010000AABB01")
    assert result["summary"]["removed_legacy_entries"] == 1
    assert result["summary"]["conflicts"] == 1
    assert hass.config_entries.async_get_entry("leg_080000aabb08") is not None
    holder = entity_registry.async_get("sensor.taken_target")
    assert holder is not None
    assert holder.platform == "other_integration"
    ng_entry = entity_registry.async_get(ng_t1)
    assert ng_entry is not None
    assert ng_entry.name == "Temperatur Bad"


async def test_lookup_device_with_real_registry(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Device lookups use the current registry APIs (deprecated calls raise in tests)."""
    entry_a = MockConfigEntry(domain=LEGACY_DOMAIN, entry_id="entry_a")
    entry_b = MockConfigEntry(domain=LEGACY_DOMAIN, entry_id="entry_b")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)
    dev_a = device_registry.async_get_or_create(
        config_entry_id="entry_a", identifiers={(LEGACY_DOMAIN, "010000AABB01")}
    )
    dev_b = device_registry.async_get_or_create(
        config_entry_id="entry_b", identifiers={(LEGACY_DOMAIN, "010000AABB01")}
    )

    def lookup(*args: Any, **kwargs: Any) -> Any:
        found = _lookup_device(device_registry, LEGACY_DOMAIN, *args, **kwargs)
        return found.id if found else None

    assert lookup("010000AABB01", config_entry_id="entry_b") == dev_b.id
    assert lookup("010000AABB01", config_entry_id="missing") is None
    assert lookup("010000AABB01") in {dev_a.id, dev_b.id}
    assert lookup("MISSING") is None
    assert lookup("MISSING", device_registry_id=dev_b.id) == dev_b.id


async def test_migration_refuses_invalid_device_ids_in_flow_text(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer
) -> None:
    """Invalid initial sensor IDs never reach the API, also with legacy import enabled."""
    _legacy_entry(hass, "010000AABB01", "Bad Nord", "MA10101")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IMPORT_LEGACY: True, CONF_DEVICE_IDS: "nope"}
    )
    assert result["errors"] == {CONF_DEVICE_IDS: "invalid_device_id_format"}
    assert mock_api.requests == []


async def test_preview_reports_skips_warnings_and_held_entity_ids(
    hass: HomeAssistant, mock_api: MockMobileAlertsServer, entity_registry: er.EntityRegistry
) -> None:
    """The preview lists unusable legacy data and entity IDs another integration holds."""
    # A legacy entry without a valid sensor ID is ignored.
    MockConfigEntry(domain=LEGACY_DOMAIN, title="Broken", data={"device_id": "nope"}).add_to_hass(
        hass
    )
    leg_01 = _legacy_entry(hass, "010000AABB01", "Bad Nord", "MA10101")
    leg_08 = _legacy_entry(hass, "080000AABB08", "Rain", "MA10650")
    _legacy_entity(hass, "010000AABB01t1", "bad_temperatur", entry=leg_01)
    _legacy_entity(hass, "garbage-unique-id", "garbage", entry=leg_01)
    _legacy_entity(hass, "080000AABB08t1", "regen_temperatur", entry=leg_08)
    # The rain sensor already lives on a second hub.
    second = hub_entry(["080000AABB08"], entry_id="second_hub")
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    hub = await _setup_hub(hass, [])
    await hass.async_block_till_done()

    result = await _migrate(
        hass,
        config_entry_id=hub.entry_id,
        dry_run=True,
        cutover=True,
        device_ids="010000AABB01,080000AABB08,0200000AABB2",
    )
    warnings = " ".join(result["warnings"])
    assert "another mobilealerts_cloud hub" in warnings
    assert "not found in legacy" in warnings
    assert [s["reason"] for s in result["skipped_entities"]] == ["unrecognized_legacy_unique_id"]
    assert result["summary"]["devices_to_import"] == 1
    assert hass.config_entries.async_get_entry(leg_01.entry_id) is not None


async def test_import_client_built_from_entry_data_when_hub_not_loaded(
    hass: HomeAssistant,
) -> None:
    """Without runtime data the import client is built from the entry and shares its guard."""
    entry = hub_entry([])
    entry.add_to_hass(hass)

    client = _build_import_client(hass, entry)

    assert client.rate_state is get_rate_limit_state(hass)
