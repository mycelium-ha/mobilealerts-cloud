"""Home Assistant integration setup for Mobile Alerts Cloud."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.hass_dict import HassKey

from .api import (
    DeviceProfile,
    DeviceReading,
    MobileAlertsClient,
    MobileAlertsError,
    RateLimitState,
    normalize_device_id,
    resolve_device_profile,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_MODEL_AUTO,
    CONF_MODEL_OVERRIDE,
    CONF_NAME_PREFIX,
    CONF_PHONE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SEND_PHONE_ID,
    DEFAULT_NAME_PREFIX,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEND_PHONE_ID,
    DOMAIN,
    PLATFORMS,
    RELOAD_CACHE_TTL_SECONDS,
    SUBENTRY_TYPE_SENSOR,
)
from .coordinator import MobileAlertsCoordinator, missing_issue_id
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class MobileAlertsCloudData:
    """Process-wide state outside `runtime_data` (ADR-007).

    Both survive config entry reloads on purpose: the rate-limit budget must not reset
    when a hub reloads, and it is shared by every hub and flow because the IP block
    applies to the whole IP, and the reload cache spares the API a second call right after
    a flow already fetched the sensors.
    """

    rate_limit_state: RateLimitState = field(default_factory=RateLimitState)
    reload_cache: dict[str, tuple[float, DeviceReading]] = field(default_factory=dict)


DATA_KEY: HassKey[MobileAlertsCloudData] = HassKey(DOMAIN)


@dataclass(frozen=True)
class SensorConfig:
    """One configured sensor of a hub: its subentry, ID, title and resolved profile."""

    subentry_id: str
    device_id: str
    title: str
    profile: DeviceProfile


@dataclass
class MobileAlertsCloudRuntime:
    """Runtime objects stored on `entry.runtime_data`."""

    coordinator: MobileAlertsCoordinator
    client: MobileAlertsClient
    name_prefix: str
    sensors: list[SensorConfig]
    hub_device_id: str
    suppress_reload: bool = False

    @property
    def subentry_profiles(self) -> dict[str, DeviceProfile]:
        """Return the resolved profile per sensor subentry ID."""
        return {sensor.subentry_id: sensor.profile for sensor in self.sensors}


type MobileAlertsCloudConfigEntry = ConfigEntry[MobileAlertsCloudRuntime]


def _domain_data(hass: HomeAssistant) -> MobileAlertsCloudData:
    """Return the process-wide state, creating it on first use."""
    data = hass.data.get(DATA_KEY)
    if data is None:
        data = hass.data[DATA_KEY] = MobileAlertsCloudData()
    return data


def get_rate_limit_state(hass: HomeAssistant) -> RateLimitState:
    """Return the one RateLimitState that every hub, flow and the migration share."""
    return _domain_data(hass).rate_limit_state


def cache_readings(hass: HomeAssistant, readings: dict[str, DeviceReading]) -> None:
    """Cache freshly fetched readings to avoid redundant API calls across Hub reloads."""
    cache = _domain_data(hass).reload_cache
    now = time.monotonic()
    for dev_id, reading in readings.items():
        cache[dev_id] = (now, reading)


def get_fresh_cached_reading(
    hass: HomeAssistant,
    device_id: str,
    max_age: float = RELOAD_CACHE_TTL_SECONDS,
) -> DeviceReading | None:
    """Return a cached DeviceReading if it is younger than max_age seconds."""
    entry = _domain_data(hass).reload_cache.get(device_id)
    if entry is None:
        return None
    cached_at, reading = entry
    if time.monotonic() - cached_at <= max_age:
        return reading
    return None


def _transmit_intervals(sensor_subentries: Sequence[ConfigSubentry]) -> dict[str, int]:
    """Return the transmit interval of every sensor whose profile declares one (ADR-010)."""
    intervals: dict[str, int] = {}
    for sub in sensor_subentries:
        dev_id = normalize_device_id(str(sub.data[CONF_DEVICE_ID]))
        profile = resolve_device_profile(
            dev_id, model_override=str(sub.data.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO))
        )
        if profile.transmit_interval_seconds is not None:
            intervals[dev_id] = profile.transmit_interval_seconds
    return intervals


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain actions once, independent of config entries."""
    async_setup_services(hass)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: MobileAlertsCloudConfigEntry) -> None:
    """Schedule a Hub config entry reload when options, data, or subentries change."""
    if entry.runtime_data.suppress_reload:
        return
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: MobileAlertsCloudConfigEntry) -> bool:
    """Set up a Mobile Alerts Cloud hub config entry and all its sensor subentries."""
    merged = {**entry.data, **entry.options}
    scan_interval = int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    name_prefix = str(merged.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX))
    phone_id = str(merged.get(CONF_PHONE_ID, "")).strip() or None
    send_phone_id = bool(merged.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID))

    rate_state = get_rate_limit_state(hass)
    session = async_get_clientsession(hass)
    client = MobileAlertsClient(
        session=session,
        phone_id=phone_id,
        send_phone_id=send_phone_id,
        rate_state=rate_state,
    )

    sensor_subentries = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_SENSOR and sub.data.get(CONF_DEVICE_ID)
    ]
    device_ids: list[str] = []
    seen_ids: set[str] = set()
    for sub in sensor_subentries:
        dev_id = normalize_device_id(str(sub.data[CONF_DEVICE_ID]))
        if dev_id not in seen_ids:
            seen_ids.add(dev_id)
            device_ids.append(dev_id)

    coordinator = MobileAlertsCoordinator(
        hass,
        entry,
        client,
        device_ids,
        scan_interval_minutes=scan_interval,
        on_readings=lambda readings: cache_readings(hass, readings),
        sensor_titles={
            normalize_device_id(str(sub.data[CONF_DEVICE_ID])): (sub.subentry_id, sub.title)
            for sub in sensor_subentries
        },
        transmit_intervals=_transmit_intervals(sensor_subentries),
    )
    _delete_orphaned_issues(hass)

    if not device_ids:
        coordinator.async_set_updated_data({})
    else:
        fresh_readings: dict[str, DeviceReading] = {}
        stale_ids: list[str] = []
        for dev_id in device_ids:
            cached = get_fresh_cached_reading(hass, dev_id)
            if cached is not None:
                fresh_readings[dev_id] = cached
            else:
                stale_ids.append(dev_id)

        if not stale_ids:
            coordinator.async_set_updated_data(fresh_readings)
        elif len(stale_ids) == len(device_ids):
            await coordinator.async_config_entry_first_refresh()
            if coordinator.data:
                cache_readings(hass, coordinator.data)
        else:
            try:
                fetched = await client.fetch_measurements(stale_ids)
            except MobileAlertsError as err:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="update_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
            cache_readings(hass, fetched)
            coordinator.async_set_updated_data({**fresh_readings, **fetched})

    sensors = [
        SensorConfig(
            subentry_id=sub.subentry_id,
            device_id=(dev_id := normalize_device_id(str(sub.data[CONF_DEVICE_ID]))),
            title=sub.title,
            profile=resolve_device_profile(
                dev_id,
                model_override=str(sub.data.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO)),
                reading=coordinator.get_reading(dev_id),
            ),
        )
        for sub in sensor_subentries
    ]

    _prune_stale_entities(hass, entry.entry_id, sensors)

    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"hub_{entry.entry_id}")},
        name=entry.title,
        manufacturer="Mobile Alerts",
        model="Mobile Alerts REST API Hub",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    entry.runtime_data = MobileAlertsCloudRuntime(
        coordinator=coordinator,
        client=client,
        name_prefix=name_prefix,
        sensors=sensors,
        hub_device_id=hub_device.id,
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _delete_orphaned_issues(hass: HomeAssistant) -> None:
    """Delete "sensor missing" repair issues of sensors that no hub contains any more."""
    active = {
        missing_issue_id(sub_id)
        for cfg_entry in hass.config_entries.async_entries(DOMAIN)
        for sub_id in cfg_entry.subentries
    }
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith("sensor_missing_") and issue_id not in active:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def _prune_stale_entities(hass: HomeAssistant, entry_id: str, sensors: list[SensorConfig]) -> None:
    """Remove orphaned entity registry entries when a subentry profile changes."""
    expected_by_subentry = {
        sensor.subentry_id: {f"{sensor.device_id}_{ch.key}" for ch in sensor.profile.channels}
        | {f"{sensor.device_id}_last_seen", f"{sensor.device_id}_battery"}
        for sensor in sensors
    }

    ent_reg = er.async_get(hass)
    for ent_entry in list(er.async_entries_for_config_entry(ent_reg, entry_id)):
        sub_id = ent_entry.config_subentry_id
        if (
            sub_id in expected_by_subentry
            and ent_entry.unique_id not in expected_by_subentry[sub_id]
        ):
            _LOGGER.debug(
                "Removing stale entity %s after profile change",
                ent_entry.entity_id,
            )
            ent_reg.async_remove(ent_entry.entity_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: MobileAlertsCloudConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device from HA only if it is no longer on this Hub."""
    active_identifiers: set[tuple[str, str]] = {(DOMAIN, f"hub_{config_entry.entry_id}")}
    for sub in config_entry.subentries.values():
        if sub.subentry_type == SUBENTRY_TYPE_SENSOR and sub.data.get(CONF_DEVICE_ID):
            active_identifiers.add((DOMAIN, normalize_device_id(str(sub.data[CONF_DEVICE_ID]))))
    return not any(ident in active_identifiers for ident in device_entry.identifiers)


async def async_unload_entry(hass: HomeAssistant, entry: MobileAlertsCloudConfigEntry) -> bool:
    """Unload a Mobile Alerts Cloud hub config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
