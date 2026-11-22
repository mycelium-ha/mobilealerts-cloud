"""Migration helpers for importing devices, metadata, and history from legacy `mobile_alerts`."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    MobileAlertsClient,
    MobileAlertsError,
    is_valid_device_id,
    normalize_device_id,
    resolve_device_profile,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_MODEL_AUTO,
    CONF_MODEL_OVERRIDE,
    CONF_NAME_PREFIX,
    CONF_PHONE_ID,
    CONF_SEND_PHONE_ID,
    DEFAULT_NAME_PREFIX,
    DEFAULT_SEND_PHONE_ID,
    DOMAIN,
    LEGACY_DOMAIN,
    SUBENTRY_TYPE_SENSOR,
)

if TYPE_CHECKING:
    from . import MobileAlertsCloudRuntime

_LOGGER = logging.getLogger(__name__)

# Keys that keep the `sensor` entity domain when moving from legacy `mobile_alerts`
# to `mobilealerts_cloud`, allowing seamless `entity_id` + Recorder history continuity.
LEGACY_SENSOR_KEYS: frozenset[str] = frozenset(
    {
        "t1",
        "t2",
        "t3",
        "t4",
        "h",
        "h1",
        "h2",
        "h3",
        "h4",
        "h3havg",
        "h24havg",
        "h7davg",
        "h30davg",
        "r",
        "rf",
        "rr",
        "ws",
        "wg",
        "wd",
        "wd_degrees",
        "ap",
        "ppm",
        "kp1c",
        "kp2c",
        "kp3c",
        "kp4c",
        "last_seen",
    }
)

# Legacy `sensor.*` keys that move to `binary_sensor.*` or `event.*` in `mobilealerts_cloud`.
LEGACY_DOMAIN_CHANGE_MAP: dict[str, tuple[str, str]] = {
    "battery": ("binary_sensor", "battery"),
    "w": ("binary_sensor", "contact"),
    "contact": ("binary_sensor", "contact"),
    "water": ("binary_sensor", "water"),
    "ac_power": ("binary_sensor", "ac_power"),
    "kp1t": ("event", "button_1"),
    "kp2t": ("event", "button_2"),
    "kp3t": ("event", "button_3"),
    "kp4t": ("event", "button_4"),
}


@dataclass
class LegacyDeviceInfo:
    """Discovered device from legacy `mobile_alerts` config entries or entities."""

    device_id: str
    entry_id: str | None
    title: str
    model_id: str | None
    suggested_model_override: str
    device_name: str | None = None
    area_id: str | None = None
    labels: set[str] = field(default_factory=set)

    def as_summary(self) -> dict[str, Any]:
        """Return JSON-serializable summary representation."""
        return {
            "device_id": self.device_id,
            "legacy_entry_id": self.entry_id,
            "title": self.title,
            "model_id": self.model_id,
            "suggested_model_override": self.suggested_model_override,
            "device_name": self.device_name,
            "area_id": self.area_id,
            "labels": sorted(self.labels),
        }


@dataclass
class LegacyEntityInfo:
    """Mapped entity from legacy `mobile_alerts` entity registry."""

    entity_id: str
    unique_id: str
    device_id: str
    legacy_domain: str
    ng_domain: str
    ng_channel_key: str
    ng_unique_id: str
    name: str | None = None
    icon: str | None = None
    area_id: str | None = None
    aliases: set[Any] | None = None
    labels: set[str] = field(default_factory=set)
    disabled_by_user: bool = False
    hidden_by_user: bool = False
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    domain_changed: bool = False

    def as_summary(
        self,
        *,
        cutover: bool,
        current_ng_entity_id: str | None = None,
        target_entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Return JSON-serializable summary representation."""
        return {
            "legacy_entity_id": self.entity_id,
            "legacy_unique_id": self.unique_id,
            "device_id": self.device_id,
            "legacy_domain": self.legacy_domain,
            "ng_domain": self.ng_domain,
            "ng_channel_key": self.ng_channel_key,
            "ng_unique_id": self.ng_unique_id,
            "current_ng_entity_id": current_ng_entity_id,
            "target_entity_id": target_entity_id,
            "will_rename_entity_id": bool(
                cutover
                and not self.domain_changed
                and target_entity_id == self.entity_id
                and current_ng_entity_id != self.entity_id
            ),
            "history_preserved": bool(
                cutover and not self.domain_changed and target_entity_id == self.entity_id
            ),
            "domain_changed": self.domain_changed,
            "name": self.name,
            "area_id": self.area_id,
            "icon": self.icon,
            "aliases": sorted(a for a in (self.aliases or ()) if isinstance(a, str)),
            "labels": sorted(self.labels),
            "disabled_by_user": self.disabled_by_user,
            "hidden_by_user": self.hidden_by_user,
            "option_domains": sorted(self.options),
        }


def parse_legacy_unique_id(unique_id: str) -> tuple[str, str, str] | None:
    """Parse a legacy `mobile_alerts` unique_id into `(device_id, ng_domain, ng_channel_key)`."""
    raw = unique_id.strip()
    if len(raw) <= 12:
        return None
    prefix = raw[:12]
    if not is_valid_device_id(prefix):
        return None
    device_id = normalize_device_id(prefix)
    rest = raw[12:]
    key = (rest.removeprefix("_")).strip().lower()
    if not key:
        return None
    if key in LEGACY_SENSOR_KEYS:
        return device_id, "sensor", key
    if key in LEGACY_DOMAIN_CHANGE_MAP:
        ng_domain, ng_channel = LEGACY_DOMAIN_CHANGE_MAP[key]
        return device_id, ng_domain, ng_channel
    return None


def suggest_model_override(device_id: str, legacy_model_id: str | None) -> str:
    """Suggest an explicit Mobile Alerts Cloud profile override when disambiguating legacy models."""
    normalized_id = normalize_device_id(device_id)
    normalized_model = (legacy_model_id or "").strip().upper()
    if normalized_id.startswith("04") and normalized_model.startswith("MA10300"):
        return "ID04_CABLE"
    return CONF_MODEL_AUTO


def _is_user_reason(reason: Any) -> bool:
    """Return True only if disabled_by/hidden_by represents a user-initiated action."""
    if reason is None:
        return False
    value = getattr(reason, "value", reason)
    return str(value).strip().lower().endswith("user")


def _extract_entity_options(raw_options: Any) -> dict[str, dict[str, Any]]:
    """Extract user-meaningful entity options, dropping legacy precision defaults."""
    if not isinstance(raw_options, dict) and not hasattr(raw_options, "items"):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for opt_domain, opt_values in raw_options.items():
        if not isinstance(opt_domain, str) or not hasattr(opt_values, "items"):
            continue
        if opt_domain == LEGACY_DOMAIN:
            continue
        copied = {
            k: v
            for k, v in opt_values.items()
            if not (opt_domain == "sensor" and k == "suggested_display_precision")
        }
        if copied:
            cleaned[opt_domain] = copied
    return cleaned


def _extract_aliases(raw_aliases: Any) -> set[Any] | None:
    """Return alias set when user-defined string aliases exist, preserving computed markers."""
    if not raw_aliases:
        return None
    alias_items = set(raw_aliases)
    if any(isinstance(item, str) and item for item in alias_items):
        return alias_items
    return None


def _iter_entity_entries(ent_reg: er.EntityRegistry) -> list[er.RegistryEntry]:
    """Return all entity registry entries."""
    return list(ent_reg.entities.values())


def _lookup_device(
    dev_reg: dr.DeviceRegistry,
    domain: str,
    device_id: str,
    *,
    config_entry_id: str | None = None,
    device_registry_id: str | None = None,
) -> dr.AnyDeviceEntry | None:
    """Look up a DeviceEntry by registry ID, or by identifier (scoped to an entry if given)."""
    if device_registry_id and (direct := dev_reg.async_get(device_registry_id)) is not None:
        return direct
    identifier = (domain, device_id)
    if config_entry_id:
        return dev_reg.async_get_device_by_identifier(identifier, config_entry_id)
    return next(iter(dev_reg.async_get_devices(identifiers={identifier})), None)


def discover_legacy_devices(hass: HomeAssistant) -> list[LegacyDeviceInfo]:
    """Discover all devices configured under legacy `mobile_alerts` (UI and orphaned YAML)."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    discovered: dict[str, LegacyDeviceInfo] = {}

    for cfg_entry in hass.config_entries.async_entries(LEGACY_DOMAIN):
        raw_dev_id = cfg_entry.data.get("device_id") or cfg_entry.unique_id
        if not raw_dev_id or not is_valid_device_id(str(raw_dev_id)):
            continue
        dev_id = normalize_device_id(str(raw_dev_id))
        model_id = cfg_entry.data.get("model_id")
        model_str = str(model_id) if model_id else None
        dev_entry = _lookup_device(
            dev_reg,
            LEGACY_DOMAIN,
            dev_id,
            config_entry_id=cfg_entry.entry_id,
        )
        raw_name_by_user = dev_entry.name_by_user if dev_entry else None
        device_name = (
            str(raw_name_by_user).strip()
            if isinstance(raw_name_by_user, str) and raw_name_by_user.strip()
            else None
        )
        area_id = dev_entry.area_id if dev_entry else None
        labels = set(dev_entry.labels or ()) if dev_entry else set()
        title = str(cfg_entry.title or (dev_entry.name if dev_entry else None) or dev_id)
        discovered[dev_id] = LegacyDeviceInfo(
            device_id=dev_id,
            entry_id=cfg_entry.entry_id,
            title=title,
            model_id=model_str,
            suggested_model_override=suggest_model_override(dev_id, model_str),
            device_name=device_name,
            area_id=area_id if isinstance(area_id, str) and area_id else None,
            labels={str(lbl) for lbl in labels if lbl},
        )

    # Also discover orphaned / YAML `mobile_alerts` entities without a ConfigEntry.
    for ent_entry in _iter_entity_entries(ent_reg):
        if ent_entry.platform != LEGACY_DOMAIN:
            continue
        parsed = parse_legacy_unique_id(str(ent_entry.unique_id))
        if parsed is None:
            continue
        dev_id, _domain, _channel = parsed
        if dev_id in discovered:
            continue
        dev_entry = _lookup_device(
            dev_reg,
            LEGACY_DOMAIN,
            dev_id,
            config_entry_id=ent_entry.config_entry_id,
            device_registry_id=ent_entry.device_id,
        )
        raw_name_by_user = dev_entry.name_by_user if dev_entry else None
        device_name = (
            str(raw_name_by_user).strip()
            if isinstance(raw_name_by_user, str) and raw_name_by_user.strip()
            else None
        )
        area_id = dev_entry.area_id if dev_entry else None
        labels = set(dev_entry.labels or ()) if dev_entry else set()
        title = str((dev_entry.name if dev_entry else None) or dev_id)
        discovered[dev_id] = LegacyDeviceInfo(
            device_id=dev_id,
            entry_id=None,
            title=title,
            model_id=None,
            suggested_model_override=CONF_MODEL_AUTO,
            device_name=device_name,
            area_id=area_id if isinstance(area_id, str) and area_id else None,
            labels={str(lbl) for lbl in labels if lbl},
        )

    return list(discovered.values())


def collect_legacy_entities(
    hass: HomeAssistant, device_ids: set[str] | None = None
) -> tuple[list[LegacyEntityInfo], list[dict[str, Any]]]:
    """Collect and map legacy `mobile_alerts` entity registry entries."""
    ent_reg = er.async_get(hass)
    mapped: list[LegacyEntityInfo] = []
    skipped: list[dict[str, Any]] = []

    for ent_entry in _iter_entity_entries(ent_reg):
        if ent_entry.platform != LEGACY_DOMAIN:
            continue
        entity_id = str(ent_entry.entity_id)
        unique_id = str(ent_entry.unique_id)
        parsed = parse_legacy_unique_id(unique_id)
        if parsed is None:
            skipped.append(
                {
                    "entity_id": entity_id,
                    "unique_id": unique_id,
                    "reason": "unrecognized_legacy_unique_id",
                }
            )
            continue

        dev_id, ng_domain, ng_channel_key = parsed
        if device_ids is not None and dev_id not in device_ids:
            continue

        legacy_domain = str(
            ent_entry.domain or (entity_id.split(".", 1)[0] if "." in entity_id else "sensor")
        )
        raw_name = ent_entry.name
        raw_icon = ent_entry.icon
        raw_area = ent_entry.area_id
        raw_labels = ent_entry.labels or ()

        mapped.append(
            LegacyEntityInfo(
                entity_id=entity_id,
                unique_id=unique_id,
                device_id=dev_id,
                legacy_domain=legacy_domain,
                ng_domain=ng_domain,
                ng_channel_key=ng_channel_key,
                ng_unique_id=f"{dev_id}_{ng_channel_key}",
                name=str(raw_name) if isinstance(raw_name, str) and raw_name else None,
                icon=str(raw_icon) if isinstance(raw_icon, str) and raw_icon else None,
                area_id=str(raw_area) if isinstance(raw_area, str) and raw_area else None,
                aliases=_extract_aliases(ent_entry.aliases),
                labels={str(lbl) for lbl in raw_labels if lbl},
                disabled_by_user=_is_user_reason(ent_entry.disabled_by),
                hidden_by_user=_is_user_reason(ent_entry.hidden_by),
                options=_extract_entity_options(ent_entry.options),
                domain_changed=legacy_domain != ng_domain,
            )
        )

    return mapped, skipped


def _device_ids_by_hub(
    hass: HomeAssistant, hub_entry: ConfigEntry[Any]
) -> tuple[set[str], set[str]]:
    """Return `(present_on_target_hub, present_on_other_hubs)` normalized device IDs."""
    target_ids: set[str] = set()
    for sub in hub_entry.subentries.values():
        raw_id = sub.data.get(CONF_DEVICE_ID)
        if raw_id:
            target_ids.add(normalize_device_id(str(raw_id)))

    other_ids: set[str] = set()
    for cfg_entry in hass.config_entries.async_entries(DOMAIN):
        if cfg_entry.entry_id == hub_entry.entry_id:
            continue
        for sub in cfg_entry.subentries.values():
            raw_id = sub.data.get(CONF_DEVICE_ID)
            if raw_id:
                other_ids.add(normalize_device_id(str(raw_id)))

    return target_ids, other_ids


def _index_ng_entities(
    ent_reg: er.EntityRegistry, hub_entry_id: str
) -> dict[tuple[str, str], er.RegistryEntry]:
    """Index Mobile Alerts Cloud entity registry entries for a hub by `(domain, unique_id)`."""
    indexed: dict[tuple[str, str], er.RegistryEntry] = {}
    for ent_entry in er.async_entries_for_config_entry(ent_reg, hub_entry_id):
        entity_id = str(ent_entry.entity_id)
        ent_domain = str(
            ent_entry.domain or (entity_id.split(".", 1)[0] if "." in entity_id else "sensor")
        )
        unique_id = str(ent_entry.unique_id)
        if unique_id:
            indexed[(ent_domain, unique_id)] = ent_entry
    return indexed


def _build_import_client(hass: HomeAssistant, hub_entry: ConfigEntry[Any]) -> MobileAlertsClient:
    """Obtain the hub runtime client or construct an equivalent client from entry data."""
    runtime: MobileAlertsCloudRuntime | None = getattr(hub_entry, "runtime_data", None)
    if runtime is not None:
        return runtime.client
    from . import get_rate_limit_state

    phone_id = str(hub_entry.data.get(CONF_PHONE_ID, "")).strip() or None
    send_phone_id = bool(hub_entry.data.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID))
    return MobileAlertsClient(
        session=async_get_clientsession(hass),
        phone_id=phone_id,
        send_phone_id=send_phone_id,
        rate_state=get_rate_limit_state(hass),
    )


async def _import_missing_devices(
    hass: HomeAssistant,
    hub_entry: ConfigEntry[Any],
    devices_to_import: list[LegacyDeviceInfo],
) -> tuple[ConfigEntry[Any], list[dict[str, Any]], list[dict[str, str]]]:
    """Validate and add missing legacy devices to `hub_entry` as subentries."""
    from . import cache_readings

    missing_ids = [dev.device_id for dev in devices_to_import]
    client = _build_import_client(hass, hub_entry)
    try:
        readings = await client.fetch_measurements(missing_ids)
    except MobileAlertsError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="legacy_import_failed",
            translation_placeholders={"count": str(len(missing_ids)), "error": str(err)},
        ) from err

    cache_readings(hass, readings)

    imported: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    runtime: MobileAlertsCloudRuntime | None = getattr(hub_entry, "runtime_data", None)

    try:
        if runtime is not None:
            runtime.suppress_reload = True
        for dev in devices_to_import:
            reading = readings.get(dev.device_id)
            if reading is None:
                failed.append({"device_id": dev.device_id, "reason": "device_not_found"})
                continue
            profile = resolve_device_profile(
                dev.device_id,
                model_override=dev.suggested_model_override,
                reading=reading,
            )
            title = dev.device_name or dev.title or f"{profile.model_code} {dev.device_id[-4:]}"
            subentry = ConfigSubentry(
                data=MappingProxyType(
                    {
                        CONF_DEVICE_ID: dev.device_id,
                        CONF_MODEL_OVERRIDE: dev.suggested_model_override,
                    }
                ),
                subentry_type=SUBENTRY_TYPE_SENSOR,
                title=title,
                unique_id=dev.device_id,
            )
            hass.config_entries.async_add_subentry(hub_entry, subentry)
            imported.append(
                {
                    "device_id": dev.device_id,
                    "title": title,
                    "model_override": dev.suggested_model_override,
                }
            )
        if imported:
            await hass.config_entries.async_reload(hub_entry.entry_id)
    finally:
        if runtime is not None:
            runtime.suppress_reload = False

    refreshed_entry = hass.config_entries.async_get_entry(hub_entry.entry_id)
    return (refreshed_entry or hub_entry), imported, failed


def _transfer_device_metadata(
    dev_reg: dr.DeviceRegistry,
    ng_dev: dr.AnyDeviceEntry,
    legacy: LegacyDeviceInfo,
) -> dict[str, Any] | None:
    """Copy device name override, area, and labels onto a Mobile Alerts Cloud device entry."""
    kwargs: dict[str, Any] = {}
    if legacy.device_name:
        kwargs["name_by_user"] = legacy.device_name
    if legacy.area_id:
        kwargs["area_id"] = legacy.area_id
    if legacy.labels:
        existing_labels = set(ng_dev.labels or ())
        kwargs["labels"] = existing_labels | legacy.labels
    if not kwargs:
        return None
    dev_reg.async_update_device(ng_dev.id, **kwargs)
    return {
        "device_id": legacy.device_id,
        "ng_device_id": ng_dev.id,
        "name_by_user": kwargs.get("name_by_user"),
        "area_id": kwargs.get("area_id"),
        "labels": sorted(kwargs["labels"]) if "labels" in kwargs else [],
    }


def _apply_entity_migration(
    ent_reg: er.EntityRegistry,
    ng_ent: er.RegistryEntry,
    legacy_ent: LegacyEntityInfo,
    *,
    cutover: bool,
    has_conflict: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Copy metadata, options, and optionally `entity_id` onto a Mobile Alerts Cloud entity."""
    kwargs: dict[str, Any] = {}
    if legacy_ent.name is not None:
        kwargs["name"] = legacy_ent.name
    if legacy_ent.icon is not None:
        kwargs["icon"] = legacy_ent.icon
    if legacy_ent.area_id is not None:
        kwargs["area_id"] = legacy_ent.area_id
    if legacy_ent.aliases is not None:
        kwargs["aliases"] = legacy_ent.aliases
    if legacy_ent.labels:
        existing_labels = set(ng_ent.labels or ())
        kwargs["labels"] = existing_labels | legacy_ent.labels
    if legacy_ent.disabled_by_user:
        kwargs["disabled_by"] = er.RegistryEntryDisabler.USER
    if legacy_ent.hidden_by_user:
        kwargs["hidden_by"] = er.RegistryEntryHider.USER

    wants_rename = (
        cutover
        and not legacy_ent.domain_changed
        and not has_conflict
        and ng_ent.entity_id != legacy_ent.entity_id
    )
    if wants_rename:
        kwargs["new_entity_id"] = legacy_ent.entity_id

    conflict_entry: dict[str, Any] | None = None
    original_ng_entity_id = ng_ent.entity_id
    final_entity_id = original_ng_entity_id

    if kwargs:
        try:
            updated = ent_reg.async_update_entity(original_ng_entity_id, **kwargs)
            final_entity_id = (
                updated.entity_id or kwargs.get("new_entity_id") or original_ng_entity_id
            )
        except ValueError as err:
            if "new_entity_id" in kwargs:
                conflict_entry = {
                    "entity_id": legacy_ent.entity_id,
                    "ng_entity_id": original_ng_entity_id,
                    "reason": str(err),
                }
                fallback_kwargs = {k: v for k, v in kwargs.items() if k != "new_entity_id"}
                if fallback_kwargs:
                    updated = ent_reg.async_update_entity(original_ng_entity_id, **fallback_kwargs)
                    final_entity_id = updated.entity_id or original_ng_entity_id
            else:
                raise

    if legacy_ent.options:
        for opt_domain, opt_values in legacy_ent.options.items():
            ent_reg.async_update_entity_options(final_entity_id, opt_domain, opt_values)

    return (
        legacy_ent.as_summary(
            cutover=cutover,
            current_ng_entity_id=original_ng_entity_id,
            target_entity_id=final_entity_id,
        ),
        conflict_entry,
    )


async def async_migrate_from_legacy(
    hass: HomeAssistant,
    hub_entry: ConfigEntry[Any],
    *,
    dry_run: bool = True,
    cutover: bool = False,
    device_ids_filter: set[str] | None = None,
) -> dict[str, Any]:
    """Preview or execute migration from legacy `mobile_alerts` to `mobilealerts_cloud`."""
    filter_ids = {normalize_device_id(d) for d in device_ids_filter} if device_ids_filter else None
    all_legacy = discover_legacy_devices(hass)
    filtered_legacy = (
        [dev for dev in all_legacy if dev.device_id in filter_ids]
        if filter_ids is not None
        else all_legacy
    )

    warnings: list[str] = []
    if filter_ids is not None:
        found_ids = {dev.device_id for dev in filtered_legacy}
        missing_filter = sorted(filter_ids - found_ids)
        if missing_filter:
            warnings.append(
                f"Requested device IDs not found in legacy mobile_alerts: {', '.join(missing_filter)}"
            )

    present_ids, other_hub_ids = _device_ids_by_hub(hass, hub_entry)
    eligible_devices: list[LegacyDeviceInfo] = []
    devices_to_import: list[LegacyDeviceInfo] = []
    devices_already_present: list[LegacyDeviceInfo] = []

    for dev in filtered_legacy:
        if dev.device_id in other_hub_ids:
            warnings.append(
                f"Device {dev.device_id} is already configured on another mobilealerts_cloud hub; skipping."
            )
            continue
        eligible_devices.append(dev)
        if dev.device_id in present_ids:
            devices_already_present.append(dev)
        else:
            devices_to_import.append(dev)

    eligible_ids = {dev.device_id for dev in eligible_devices}
    legacy_entities, skipped_entities = collect_legacy_entities(
        hass, device_ids=eligible_ids if (filter_ids is not None or other_hub_ids) else None
    )

    merged_opts = {**hub_entry.data, **hub_entry.options}
    name_prefix = str(merged_opts.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX))
    if cutover and name_prefix:
        warnings.append(
            f"Hub currently has name_prefix='{name_prefix}'. Clear it in Hub Options after cutover if you no longer want prefixed device names."
        )
    if any(ent.domain_changed for ent in legacy_entities):
        warnings.append(
            "Entities changing domain from sensor to binary_sensor/event (battery, water, contact, ac_power, switch buttons) receive names/areas/options but cannot retain historical sensor.* Recorder graphs; update dashboards/automations to their new entity IDs."
        )

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    existing_by_eid = {str(e.entity_id): e for e in _iter_entity_entries(ent_reg)}
    ng_by_key = _index_ng_entities(ent_reg, hub_entry.entry_id)

    conflicts: list[dict[str, Any]] = []
    conflicting_eids: set[str] = set()
    for le in legacy_entities:
        if cutover and not le.domain_changed:
            holder = existing_by_eid.get(le.entity_id)
            if holder is not None and holder.platform not in (
                LEGACY_DOMAIN,
                DOMAIN,
            ):
                conflicts.append(
                    {
                        "entity_id": le.entity_id,
                        "reason": "held_by_other_integration",
                        "holder_platform": holder.platform,
                    }
                )
                conflicting_eids.add(le.entity_id)

    if dry_run:
        device_updates = [
            dev.as_summary()
            for dev in eligible_devices
            if dev.device_name or dev.area_id or dev.labels
        ]
        same_domain_plan: list[dict[str, Any]] = []
        changed_domain_plan: list[dict[str, Any]] = []
        for le in legacy_entities:
            ng_ent = ng_by_key.get((le.ng_domain, le.ng_unique_id))
            current_ng_eid = ng_ent.entity_id if ng_ent is not None else None
            planned_target = (
                le.entity_id
                if (cutover and not le.domain_changed and le.entity_id not in conflicting_eids)
                else current_ng_eid
            )
            summary_row = le.as_summary(
                cutover=cutover,
                current_ng_entity_id=current_ng_eid,
                target_entity_id=planned_target,
            )
            if le.domain_changed:
                changed_domain_plan.append(summary_row)
            else:
                same_domain_plan.append(summary_row)

        legacy_entries_to_remove = [
            {"entry_id": dev.entry_id, "device_id": dev.device_id, "title": dev.title}
            for dev in eligible_devices
            if cutover and dev.entry_id is not None
        ]
        return {
            "success": True,
            "dry_run": True,
            "cutover": cutover,
            "hub_entry_id": hub_entry.entry_id,
            "summary": {
                "discovered_legacy_devices": len(filtered_legacy),
                "eligible_devices": len(eligible_devices),
                "devices_to_import": len(devices_to_import),
                "devices_already_present": len(devices_already_present),
                "device_metadata_updates": len(device_updates),
                "same_domain_entities": len(same_domain_plan),
                "domain_changed_entities": len(changed_domain_plan),
                "skipped_entities": len(skipped_entities),
                "conflicts": len(conflicts),
                "legacy_entries_to_remove": len(legacy_entries_to_remove),
            },
            "devices_to_import": [d.as_summary() for d in devices_to_import],
            "devices_already_present": [d.as_summary() for d in devices_already_present],
            "device_metadata_updates": device_updates,
            "entity_migrations": same_domain_plan,
            "domain_changed_entities": changed_domain_plan,
            "skipped_entities": skipped_entities,
            "conflicts": conflicts,
            "legacy_entries_to_remove": legacy_entries_to_remove,
            "warnings": warnings,
        }

    # --- Execution mode (`dry_run=False`) ---
    imported_devices: list[dict[str, Any]] = []
    failed_imports: list[dict[str, str]] = []
    active_entry = hub_entry

    if devices_to_import:
        active_entry, imported_devices, failed_imports = await _import_missing_devices(
            hass, hub_entry, devices_to_import
        )
        failed_ids = {item["device_id"] for item in failed_imports}
        if failed_ids:
            warnings.append(
                f"Failed importing sensors (legacy entries left untouched): {', '.join(sorted(failed_ids))}"
            )
            eligible_devices = [d for d in eligible_devices if d.device_id not in failed_ids]
            eligible_ids -= failed_ids

    # Confirm Mobile Alerts Cloud device/subentry existence before deleting any legacy entries.
    ng_sub_ids: set[str] = set()
    for sub in active_entry.subentries.values():
        raw_id = sub.data.get(CONF_DEVICE_ID)
        if raw_id:
            ng_sub_ids.add(normalize_device_id(str(raw_id)))

    ng_devices_by_id: dict[str, Any] = {}
    confirmed_ids: set[str] = set()
    for dev in eligible_devices:
        ng_dev = _lookup_device(
            dev_reg,
            DOMAIN,
            dev.device_id,
            config_entry_id=active_entry.entry_id,
        )
        if ng_dev is not None:
            ng_devices_by_id[dev.device_id] = ng_dev
        if dev.device_id in ng_sub_ids or ng_dev is not None:
            confirmed_ids.add(dev.device_id)
        else:
            warnings.append(
                f"Mobile Alerts Cloud device/subentry missing for {dev.device_id}; skipping cutover and metadata transfer."
            )

    removed_legacy_entries: list[dict[str, Any]] = []
    if cutover:
        removed_entry_ids: set[str] = set()
        for dev in eligible_devices:
            if dev.device_id not in confirmed_ids or not dev.entry_id:
                continue
            if dev.entry_id in removed_entry_ids:
                continue
            await hass.config_entries.async_remove(dev.entry_id)
            removed_entry_ids.add(dev.entry_id)
            removed_legacy_entries.append(
                {"entry_id": dev.entry_id, "device_id": dev.device_id, "title": dev.title}
            )

        # Clean up orphaned/YAML legacy entities holding target entity_ids.
        post_remove_by_eid = {str(e.entity_id): e for e in _iter_entity_entries(ent_reg)}
        for le in legacy_entities:
            if le.device_id not in confirmed_ids or le.domain_changed:
                continue
            holder = post_remove_by_eid.get(le.entity_id)
            if holder is not None and holder.platform == LEGACY_DOMAIN:
                ent_reg.async_remove(le.entity_id)

    device_metadata_updates: list[dict[str, Any]] = []
    for dev in eligible_devices:
        if dev.device_id not in confirmed_ids:
            continue
        ng_dev = ng_devices_by_id.get(dev.device_id)
        if ng_dev is not None:
            updated_dev = _transfer_device_metadata(dev_reg, ng_dev, dev)
            if updated_dev is not None:
                device_metadata_updates.append(updated_dev)

    ng_by_key = _index_ng_entities(ent_reg, active_entry.entry_id)
    migrated_same_domain: list[dict[str, Any]] = []
    migrated_changed_domain: list[dict[str, Any]] = []
    missing_ng_entities: list[dict[str, Any]] = []

    for le in legacy_entities:
        if le.device_id not in confirmed_ids:
            continue
        ng_ent = ng_by_key.get((le.ng_domain, le.ng_unique_id))
        if ng_ent is None:
            missing_ng_entities.append(
                {
                    "legacy_entity_id": le.entity_id,
                    "ng_domain": le.ng_domain,
                    "ng_unique_id": le.ng_unique_id,
                    "reason": "ng_entity_not_created",
                }
            )
            continue

        row, conflict_entry = _apply_entity_migration(
            ent_reg,
            ng_ent,
            le,
            cutover=cutover,
            has_conflict=le.entity_id in conflicting_eids,
        )
        if conflict_entry is not None:
            conflicts.append(conflict_entry)
        if le.domain_changed:
            migrated_changed_domain.append(row)
        else:
            migrated_same_domain.append(row)

    return {
        "success": len(failed_imports) == 0,
        "dry_run": False,
        "cutover": cutover,
        "hub_entry_id": active_entry.entry_id,
        "summary": {
            "imported_devices": len(imported_devices),
            "failed_imports": len(failed_imports),
            "device_metadata_updates": len(device_metadata_updates),
            "same_domain_entities_migrated": len(migrated_same_domain),
            "domain_changed_entities_migrated": len(migrated_changed_domain),
            "missing_ng_entities": len(missing_ng_entities),
            "skipped_entities": len(skipped_entities),
            "conflicts": len(conflicts),
            "removed_legacy_entries": len(removed_legacy_entries),
        },
        "imported_devices": imported_devices,
        "failed_imports": failed_imports,
        "device_metadata_updates": device_metadata_updates,
        "entity_migrations": migrated_same_domain,
        "domain_changed_entities": migrated_changed_domain,
        "missing_ng_entities": missing_ng_entities,
        "skipped_entities": skipped_entities,
        "conflicts": conflicts,
        "removed_legacy_entries": removed_legacy_entries,
        "warnings": warnings,
    }
