"""Diagnostics support for Mobile Alerts Cloud."""

from __future__ import annotations

import re
from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api import normalize_device_id
from .const import CONF_DEVICE_ID, CONF_PHONE_ID, DOMAIN

TO_REDACT: set[str] = {CONF_PHONE_ID, "phoneid"}


def _sensor_aliases(entry: ConfigEntry[Any]) -> dict[str, str]:
    """Map every sensor ID of the hub to a stable alias (`sensor_1`, `sensor_2`, ...)."""
    ids = sorted(
        {
            normalize_device_id(str(sub.data[CONF_DEVICE_ID]))
            for sub in entry.subentries.values()
            if sub.data.get(CONF_DEVICE_ID)
        }
    )
    return {dev_id: f"sensor_{index}" for index, dev_id in enumerate(ids, start=1)}


def _anonymize_dict(data: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    """Return a diagnostics dict with every sensor ID replaced by its alias."""
    return cast(dict[str, Any], _anonymize(data, aliases))


def _anonymize(data: Any, aliases: dict[str, str]) -> Any:
    """Replace sensor IDs everywhere, consistently, with their aliases.

    Sensor IDs are credentials (anyone who knows one can read the sensor's data). Full IDs
    are replaced in every key and value; the short forms that titles (`MA 10100 BB01`) and
    rate-limit keys (`...BB01`) use are replaced as well, so support can still tell which
    entries belong together.
    """
    if isinstance(data, dict):
        return {_anonymize(k, aliases): _anonymize(v, aliases) for k, v in data.items()}
    if isinstance(data, list):
        return [_anonymize(item, aliases) for item in data]
    if isinstance(data, str):
        for dev_id, alias in aliases.items():
            data = re.sub(re.escape(dev_id), alias, data, flags=re.IGNORECASE)
            data = data.replace(f"...{dev_id[-4:]}", f"...{alias}")
            data = re.sub(rf"(?<=\s){re.escape(dev_id[-4:])}$", alias, data)
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[Any]
) -> dict[str, Any]:
    """Return redacted diagnostics for a Mobile Alerts Cloud hub config entry."""
    runtime = entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    subentries_info = []
    for subentry in entry.subentries.values():
        profile = runtime.subentry_profiles.get(subentry.subentry_id) if runtime else None
        subentries_info.append(
            {
                "subentry_id": subentry.subentry_id,
                "subentry_type": subentry.subentry_type,
                "title": subentry.title,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
                "resolved_profile": (
                    {
                        "profile_id": profile.profile_id,
                        "model_code": profile.model_code,
                        "manufacturer": profile.manufacturer,
                        "channels": [ch.key for ch in profile.channels],
                    }
                    if profile is not None
                    else None
                ),
            }
        )

    aliases = _sensor_aliases(entry)
    if runtime is None:
        return _anonymize_dict(
            {
                "entry": {
                    "entry_id": entry.entry_id,
                    "title": entry.title,
                    "data": async_redact_data(dict(entry.data), TO_REDACT),
                    "options": dict(entry.options),
                },
                "subentries": subentries_info,
                "status": "not_loaded",
            },
            aliases,
        )

    raw_readings = [
        async_redact_data(reading.raw, TO_REDACT)
        for reading in (runtime.coordinator.data or {}).values()
    ]

    return _anonymize_dict(
        {
            "entry": {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "data": async_redact_data(dict(entry.data), TO_REDACT),
                "options": dict(entry.options),
            },
            "hub": {
                "api_url": runtime.client.api_url,
                "device_count": len(runtime.coordinator.device_ids),
                "last_update_success": runtime.coordinator.last_update_success,
                "rate_limit_status": runtime.client.rate_limit_status(),
            },
            "subentries": subentries_info,
            "raw_readings": raw_readings,
        },
        aliases,
    )


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[Any], device: DeviceEntry
) -> dict[str, Any]:
    """Return redacted diagnostics scoped to a single Mobile Alerts sensor device."""
    sensor_device_id: str | None = None
    for domain, ident in device.identifiers:
        if domain == DOMAIN and not ident.startswith("hub_"):
            sensor_device_id = normalize_device_id(ident)
            break

    if sensor_device_id is None:
        return await async_get_config_entry_diagnostics(hass, entry)

    runtime = entry.runtime_data if entry.state is ConfigEntryState.LOADED else None
    matching_subentry = next(
        (
            sub
            for sub in entry.subentries.values()
            if sub.data.get(CONF_DEVICE_ID)
            and normalize_device_id(str(sub.data[CONF_DEVICE_ID])) == sensor_device_id
        ),
        None,
    )
    profile = (
        runtime.subentry_profiles.get(matching_subentry.subentry_id)
        if runtime is not None and matching_subentry is not None
        else None
    )
    reading = runtime.coordinator.get_reading(sensor_device_id) if runtime is not None else None

    return _anonymize_dict(
        {
            "subentry": (
                {
                    "subentry_id": matching_subentry.subentry_id,
                    "title": matching_subentry.title,
                    "data": async_redact_data(dict(matching_subentry.data), TO_REDACT),
                }
                if matching_subentry is not None
                else None
            ),
            "resolved_profile": (
                {
                    "profile_id": profile.profile_id,
                    "model_code": profile.model_code,
                    "manufacturer": profile.manufacturer,
                    "channels": [ch.key for ch in profile.channels],
                }
                if profile is not None
                else None
            ),
            "rate_limit_status": (
                runtime.client.rate_limit_status() if runtime is not None else None
            ),
            "raw_reading": (
                async_redact_data(reading.raw, TO_REDACT) if reading is not None else None
            ),
        },
        _sensor_aliases(entry),
    )
