"""Domain actions (services) for Mobile Alerts Cloud.

Registered once in `async_setup` (Quality Scale: action-setup). Every action resolves
its target among the *loaded* hub entries via `config_entry_id` and raises a translated `ServiceValidationError` when no entry
matches. `migrate_from_legacy` changes state and needs exactly one target; the read-only
actions cover all loaded hubs when no target is given.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import parse_device_ids_text
from .const import (
    DOMAIN,
    SERVICE_DUMP_RAW_RESPONSE,
    SERVICE_FORCE_REFRESH,
    SERVICE_MIGRATE_FROM_LEGACY,
)
from .migration import async_migrate_from_legacy

if TYPE_CHECKING:
    from . import MobileAlertsCloudConfigEntry

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

TARGET_SCHEMA = {
    probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
}


def _validation_error(key: str, **placeholders: str) -> ServiceValidationError:
    """Return a translated `ServiceValidationError`."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _requested_entry_id(call: ServiceCall) -> str | None:
    """Return the targeted entry ID from `config_entry_id`."""
    raw = call.data.get(ATTR_CONFIG_ENTRY_ID)
    return str(raw).strip() or None if raw else None


def _target_entries(hass: HomeAssistant, call: ServiceCall) -> list[MobileAlertsCloudConfigEntry]:
    """Return the targeted loaded hubs, or all loaded hubs when no target is given."""
    loaded: list[MobileAlertsCloudConfigEntry] = list(
        hass.config_entries.async_loaded_entries(DOMAIN)
    )
    if not loaded:
        raise _validation_error("no_loaded_entries")
    entry_id = _requested_entry_id(call)
    if entry_id is None:
        return loaded
    match = next((entry for entry in loaded if entry.entry_id == entry_id), None)
    if match is None:
        raise _validation_error("entry_not_loaded", entry_id=entry_id)
    return [match]


def _single_target(hass: HomeAssistant, call: ServiceCall) -> MobileAlertsCloudConfigEntry:
    """Return the one hub a state-changing action targets; never pick one silently."""
    entries = _target_entries(hass, call)
    if len(entries) > 1:
        raise _validation_error("multiple_entries_loaded")
    return entries[0]


async def _async_dump_raw_response(call: ServiceCall) -> dict[str, Any]:
    """Return cached or freshly fetched raw API payloads (unredacted, for local debugging)."""
    do_refresh = bool(call.data.get("refresh", False))
    results: dict[str, Any] = {}
    for entry in _target_entries(call.hass, call):
        runtime = entry.runtime_data
        if do_refresh:
            await _refresh_or_raise([entry])
        results[entry.entry_id] = {
            "title": entry.title,
            "devices": [reading.raw for reading in (runtime.coordinator.data or {}).values()],
            "rate_limit_status": runtime.client.rate_limit_status(),
        }
    return {
        "success": True,
        "timestamp": datetime.now(UTC).isoformat(),
        "entries_count": len(results),
        "data": results,
    }


async def _refresh_or_raise(entries: list[MobileAlertsCloudConfigEntry]) -> None:
    """Poll each hub now (through the rate-limit guard); raise if any poll got no new data."""
    failed: list[str] = []
    for entry in entries:
        coordinator = entry.runtime_data.coordinator
        if not coordinator.device_ids:
            continue
        await coordinator.async_refresh()
        if not coordinator.last_update_success or coordinator.rate_limited:
            failed.append(entry.title)
    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="refresh_failed",
            translation_placeholders={"hubs": ", ".join(failed)},
        )


async def _async_force_refresh(call: ServiceCall) -> None:
    """Poll the targeted hubs now; raise a translated error when a poll fails."""
    await _refresh_or_raise(_target_entries(call.hass, call))


async def _async_migrate_from_legacy(call: ServiceCall) -> dict[str, Any]:
    """Preview or execute the takeover from the legacy `mobile_alerts` integration."""
    entry = _single_target(call.hass, call)
    filter_ids: set[str] | None = None
    raw_device_ids = str(call.data.get("device_ids") or "").strip()
    if raw_device_ids:
        valid_ids, invalid_tokens = parse_device_ids_text(raw_device_ids)
        if invalid_tokens:
            raise _validation_error("invalid_device_ids", count=str(len(invalid_tokens)))
        filter_ids = set(valid_ids)
    return await async_migrate_from_legacy(
        call.hass,
        entry,
        dry_run=bool(call.data.get("dry_run", True)),
        cutover=bool(call.data.get("cutover", False)),
        device_ids_filter=filter_ids,
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the `mobilealerts_cloud.*` actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_RAW_RESPONSE,
        _async_dump_raw_response,
        schema=probatio.Schema(
            {probatio.Optional("refresh", default=False): cv.boolean, **TARGET_SCHEMA}
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_REFRESH,
        _async_force_refresh,
        schema=probatio.Schema(TARGET_SCHEMA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MIGRATE_FROM_LEGACY,
        _async_migrate_from_legacy,
        schema=probatio.Schema(
            {
                probatio.Optional("dry_run", default=True): cv.boolean,
                probatio.Optional("cutover", default=False): cv.boolean,
                probatio.Optional("device_ids"): cv.string,
                **TARGET_SCHEMA,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
