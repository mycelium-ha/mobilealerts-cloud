"""Constants for the Mobile Alerts Cloud Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "mobilealerts_cloud"
LEGACY_DOMAIN = "mobile_alerts"
SUBENTRY_TYPE_SENSOR = "sensor"

# Config & options keys
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_IDS = "device_ids"
CONF_IMPORT_LEGACY = "import_legacy_devices"
CONF_NAME = "name"
CONF_PHONE_ID = "phone_id"
CONF_SEND_PHONE_ID = "send_phone_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NAME_PREFIX = "name_prefix"
CONF_MODEL_OVERRIDE = "model_override"
CONF_MODEL_AUTO = "auto"

# Defaults & bounds
DEFAULT_HUB_NAME = "Mobile Alerts Cloud"
DEFAULT_SCAN_INTERVAL_MINUTES = 10
MIN_SCAN_INTERVAL_MINUTES = 1
MAX_SCAN_INTERVAL_MINUTES = 1440
DEFAULT_NAME_PREFIX = ""
DEFAULT_SEND_PHONE_ID = False
RELOAD_CACHE_TTL_SECONDS = 60.0

# ADR-005: a sensor is stale when lastseen is older than max(60 min, 3 x poll interval).
# ADR-010: and at least 2.5 x the hardware's transmit interval where the profile sets one.
STALE_MIN_SECONDS = 3600
STALE_POLL_FACTOR = 3
STALE_TRANSMIT_FACTOR = 2.5
# A sensor the API has not returned for this long gets a repair issue.
MISSING_SENSOR_REPAIR_SECONDS = 24 * 3600

ATTRIBUTION = "Data provided by Mobile Alerts (data199.com)"

# Isolated Home Assistant event bus name (never collides with legacy mobile_alerts)
EVENT_KEY_PRESS = "mobilealerts_cloud_key_press"

# Services
SERVICE_DUMP_RAW_RESPONSE = "dump_raw_response"
SERVICE_FORCE_REFRESH = "force_refresh"
SERVICE_MIGRATE_FROM_LEGACY = "migrate_from_legacy"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
]
