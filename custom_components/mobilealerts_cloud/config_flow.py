"""Config flow, options flow, and sensor subentry flow for Mobile Alerts Cloud."""

from __future__ import annotations

import logging
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import cache_readings, get_rate_limit_state
from .api import (
    PROFILES_BY_ID,
    MobileAlertsApiError,
    MobileAlertsBlockedError,
    MobileAlertsClient,
    MobileAlertsConnectionError,
    MobileAlertsError,
    MobileAlertsRateLimitError,
    MobileAlertsValidationError,
    is_valid_device_id,
    is_valid_phone_id,
    normalize_device_id,
    normalize_phone_id,
    parse_device_ids_text,
    resolve_device_profile,
)
from .const import (
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
    DEFAULT_HUB_NAME,
    DEFAULT_NAME_PREFIX,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEND_PHONE_ID,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    SUBENTRY_TYPE_SENSOR,
)
from .migration import LegacyDeviceInfo, discover_legacy_devices

_LOGGER = logging.getLogger(__name__)


def _model_override_selector() -> SelectSelector:
    """Build selector options for automatic vs. explicit profile selection."""
    # The "auto" label is translated (selector.model_override in strings.json); profile
    # labels are model codes and product names and stay as they are.
    options = [SelectOptionDict(value=CONF_MODEL_AUTO, label=CONF_MODEL_AUTO)]
    for profile_id, profile in sorted(PROFILES_BY_ID.items()):
        options.append(
            SelectOptionDict(
                value=profile_id,
                label=f"{profile_id}: {profile.model_code} — {profile.display_name}",
            )
        )
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key=CONF_MODEL_OVERRIDE,
        )
    )


def _hub_options_schema(defaults: dict[str, Any]) -> probatio.Schema:
    """Build schema for Hub polling and naming options."""
    return probatio.Schema(
        {
            probatio.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES),
            ): probatio.All(
                probatio.Coerce(int),
                probatio.Range(min=MIN_SCAN_INTERVAL_MINUTES, max=MAX_SCAN_INTERVAL_MINUTES),
            ),
            probatio.Optional(
                CONF_NAME_PREFIX,
                default=defaults.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX),
            ): str,
        }
    )


def _hub_connection_schema(defaults: dict[str, Any]) -> probatio.Schema:
    """Build schema for optional Hub Phone ID settings."""
    return probatio.Schema(
        {
            probatio.Optional(
                CONF_PHONE_ID,
                default=defaults.get(CONF_PHONE_ID, ""),
            ): str,
            probatio.Optional(
                CONF_SEND_PHONE_ID,
                default=defaults.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID),
            ): bool,
        }
    )


def _all_configured_device_ids(hass: HomeAssistant) -> set[str]:
    """Collect all normalized sensor IDs configured across every mobilealerts_cloud hub."""
    return {
        normalize_device_id(str(sub.data[CONF_DEVICE_ID]))
        for cfg_entry in hass.config_entries.async_entries(DOMAIN)
        for sub in cfg_entry.subentries.values()
        if sub.data.get(CONF_DEVICE_ID)
    }


def _discover_unconfigured_legacy(hass: HomeAssistant) -> dict[str, LegacyDeviceInfo]:
    """Return legacy `mobile_alerts` devices not yet configured on any Mobile Alerts Cloud hub."""
    configured_ids = _all_configured_device_ids(hass)
    return {
        dev.device_id: dev
        for dev in discover_legacy_devices(hass)
        if dev.device_id not in configured_ids
    }


def _map_api_error_key(err: MobileAlertsError) -> str:
    """Map an API client exception to a translation error key."""
    if isinstance(err, MobileAlertsRateLimitError):
        return "rate_limited"
    if isinstance(err, MobileAlertsBlockedError):
        return "ip_blocked"
    if isinstance(err, MobileAlertsValidationError):
        return "invalid_device_id_format"
    if isinstance(err, MobileAlertsConnectionError):
        return "cannot_connect"
    if isinstance(err, MobileAlertsApiError):
        return "api_error"
    return "unknown"


class MobileAlertsCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Hub config flow and reconfigure flow for Mobile Alerts Cloud."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry[Any],
    ) -> MobileAlertsCloudOptionsFlow:
        """Return the options flow handler for this Hub."""
        return MobileAlertsCloudOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry[Any]
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported config subentry flow handlers."""
        return {SUBENTRY_TYPE_SENSOR: SensorSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle initial Hub setup with optional batch sensor IDs and legacy import."""
        errors: dict[str, str] = {}
        defaults = user_input or {}
        unconfigured_legacy = _discover_unconfigured_legacy(self.hass)

        if user_input is not None:
            hub_name = str(user_input.get(CONF_NAME, DEFAULT_HUB_NAME)).strip() or DEFAULT_HUB_NAME
            import_legacy = bool(user_input.get(CONF_IMPORT_LEGACY, False))
            raw_device_ids = str(user_input.get(CONF_DEVICE_IDS, ""))
            raw_phone_id = normalize_phone_id(str(user_input.get(CONF_PHONE_ID, "")))
            send_phone_id = bool(user_input.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID))
            scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
            name_prefix = str(user_input.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX))

            if raw_phone_id and not is_valid_phone_id(raw_phone_id):
                errors[CONF_PHONE_ID] = "invalid_phone_id"

            valid_ids, invalid_tokens = parse_device_ids_text(raw_device_ids)
            if invalid_tokens:
                errors[CONF_DEVICE_IDS] = "invalid_device_id_format"
            elif valid_ids and any(
                did in _all_configured_device_ids(self.hass) for did in valid_ids
            ):
                errors[CONF_DEVICE_IDS] = "already_configured"

            legacy_ids = list(unconfigured_legacy) if import_legacy else []
            combined_ids = list(dict.fromkeys([*legacy_ids, *valid_ids]))

            subentries: list[ConfigSubentryData] = []
            if not errors and combined_ids:
                session = async_get_clientsession(self.hass)
                rate_state = get_rate_limit_state(self.hass)
                try:
                    client = MobileAlertsClient(
                        session=session,
                        phone_id=raw_phone_id or None,
                        send_phone_id=send_phone_id,
                        rate_state=rate_state,
                    )
                    readings = await client.fetch_measurements(combined_ids)
                except MobileAlertsError as err:
                    _LOGGER.debug("Hub setup sensor check failed: %s", err)
                    errors["base"] = _map_api_error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error during hub setup")
                    errors["base"] = "unknown"
                else:
                    missing = [did for did in combined_ids if did not in readings]
                    if missing:
                        if any(did in valid_ids for did in missing):
                            errors[CONF_DEVICE_IDS] = "device_not_found"
                        else:
                            errors["base"] = "device_not_found"
                    else:
                        cache_readings(self.hass, readings)
                        for dev_id in combined_ids:
                            reading = readings[dev_id]
                            legacy = unconfigured_legacy.get(dev_id)
                            model_override = (
                                legacy.suggested_model_override
                                if legacy is not None
                                else CONF_MODEL_AUTO
                            )
                            profile = resolve_device_profile(
                                dev_id,
                                model_override=model_override,
                                reading=reading,
                            )
                            legacy_title = (
                                (legacy.device_name or legacy.title)
                                if legacy is not None
                                and (
                                    legacy.device_name or (legacy.title and legacy.title != dev_id)
                                )
                                else None
                            )
                            title = legacy_title or f"{profile.model_code} {dev_id[-4:]}"
                            subentries.append(
                                ConfigSubentryData(
                                    data={
                                        CONF_DEVICE_ID: dev_id,
                                        CONF_MODEL_OVERRIDE: model_override,
                                    },
                                    subentry_type=SUBENTRY_TYPE_SENSOR,
                                    title=title,
                                    unique_id=dev_id,
                                )
                            )

            if not errors:
                return self.async_create_entry(
                    title=hub_name,
                    data={
                        CONF_PHONE_ID: raw_phone_id,
                        CONF_SEND_PHONE_ID: send_phone_id,
                    },
                    options={
                        CONF_SCAN_INTERVAL: scan_interval,
                        CONF_NAME_PREFIX: name_prefix,
                    },
                    subentries=subentries,
                )

        schema_fields: dict[Any, Any] = {
            probatio.Optional(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_HUB_NAME)): str,
        }
        if unconfigured_legacy:
            schema_fields[
                probatio.Optional(
                    CONF_IMPORT_LEGACY,
                    default=defaults.get(CONF_IMPORT_LEGACY, True),
                )
            ] = bool
        schema_fields.update(
            {
                probatio.Optional(CONF_DEVICE_IDS, default=defaults.get(CONF_DEVICE_IDS, "")): str,
                probatio.Optional(
                    CONF_SCAN_INTERVAL,
                    default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES),
                ): probatio.All(
                    probatio.Coerce(int),
                    probatio.Range(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                    ),
                ),
                probatio.Optional(
                    CONF_NAME_PREFIX,
                    default=defaults.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX),
                ): str,
                probatio.Optional(CONF_PHONE_ID, default=defaults.get(CONF_PHONE_ID, "")): str,
                probatio.Optional(
                    CONF_SEND_PHONE_ID,
                    default=defaults.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=probatio.Schema(schema_fields), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure Hub Phone ID connection settings."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_phone_id = normalize_phone_id(str(user_input.get(CONF_PHONE_ID, "")))
            send_phone_id = bool(user_input.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID))
            if raw_phone_id and not is_valid_phone_id(raw_phone_id):
                errors[CONF_PHONE_ID] = "invalid_phone_id"
            else:
                updates = {
                    CONF_PHONE_ID: raw_phone_id,
                    CONF_SEND_PHONE_ID: send_phone_id,
                }
                return self.async_update_and_abort(entry, data_updates=updates)

        defaults = user_input if user_input is not None else dict(entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_hub_connection_schema(defaults),
            errors=errors,
        )


class MobileAlertsCloudOptionsFlow(OptionsFlow):
    """Handle Hub runtime options (scan interval and name prefix)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage Hub runtime options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
                    ),
                    CONF_NAME_PREFIX: str(user_input.get(CONF_NAME_PREFIX, DEFAULT_NAME_PREFIX)),
                },
            )

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_hub_options_schema(current),
        )


class SensorSubentryFlow(ConfigSubentryFlow):
    """Handle adding and reconfiguring individual Mobile Alerts sensors under a Hub."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a single Mobile Alerts sensor subentry."""
        errors: dict[str, str] = {}
        entry = self._get_entry()
        defaults = user_input or {}

        if user_input is not None:
            device_id = normalize_device_id(str(user_input.get(CONF_DEVICE_ID, "")))
            custom_name = str(user_input.get(CONF_NAME, "")).strip()
            model_override = str(user_input.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO))

            if not is_valid_device_id(device_id):
                errors[CONF_DEVICE_ID] = "invalid_device_id_format"
            else:
                existing_ids = _all_configured_device_ids(self.hass) | {
                    normalize_device_id(str(sub.data.get(CONF_DEVICE_ID, "")))
                    for sub in entry.subentries.values()
                    if sub.data.get(CONF_DEVICE_ID)
                }
                if device_id in existing_ids or any(
                    sub.unique_id == device_id for sub in entry.subentries.values()
                ):
                    return self.async_abort(reason="already_configured")

                session = async_get_clientsession(self.hass)
                rate_state = get_rate_limit_state(self.hass)
                phone_id = str(entry.data.get(CONF_PHONE_ID, "")).strip() or None
                send_phone_id = bool(entry.data.get(CONF_SEND_PHONE_ID, DEFAULT_SEND_PHONE_ID))

                try:
                    client = MobileAlertsClient(
                        session=session,
                        phone_id=phone_id,
                        send_phone_id=send_phone_id,
                        rate_state=rate_state,
                    )
                    readings = await client.fetch_measurements([device_id])
                except MobileAlertsError as err:
                    _LOGGER.debug("Sensor check failed: %s", err)
                    errors["base"] = _map_api_error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error while adding a sensor")
                    errors["base"] = "unknown"
                else:
                    reading = readings.get(device_id)
                    if reading is None:
                        errors[CONF_DEVICE_ID] = "device_not_found"
                    elif not reading.values and reading.last_seen is None:
                        errors["base"] = "device_offline_no_data"
                    else:
                        cache_readings(self.hass, readings)
                        profile = resolve_device_profile(
                            device_id,
                            model_override=model_override,
                            reading=reading,
                        )
                        title = custom_name or f"{profile.model_code} {device_id[-4:]}"
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_DEVICE_ID: device_id,
                                CONF_MODEL_OVERRIDE: model_override,
                            },
                            unique_id=device_id,
                        )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
                probatio.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
                probatio.Optional(
                    CONF_MODEL_OVERRIDE,
                    default=defaults.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO),
                ): _model_override_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename a sensor subentry or override its hardware profile."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            new_title = str(user_input.get(CONF_NAME, "")).strip() or subentry.title
            model_override = str(user_input.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO))
            return self.async_update_and_abort(
                entry=entry,
                subentry=subentry,
                title=new_title,
                data_updates={CONF_MODEL_OVERRIDE: model_override},
            )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_NAME, default=subentry.title): str,
                probatio.Optional(
                    CONF_MODEL_OVERRIDE,
                    default=subentry.data.get(CONF_MODEL_OVERRIDE, CONF_MODEL_AUTO),
                ): _model_override_selector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={
                "device_id": str(subentry.data.get(CONF_DEVICE_ID, "")),
            },
        )
