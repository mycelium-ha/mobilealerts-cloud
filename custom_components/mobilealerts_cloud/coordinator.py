"""DataUpdateCoordinator for Mobile Alerts Cloud."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DeviceReading,
    MobileAlertsBlockedError,
    MobileAlertsClient,
    MobileAlertsError,
    MobileAlertsRateLimitError,
    mask_id,
)
from .const import (
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MISSING_SENSOR_REPAIR_SECONDS,
    STALE_MIN_SECONDS,
    STALE_POLL_FACTOR,
    STALE_TRANSMIT_FACTOR,
)

if TYPE_CHECKING:
    from . import MobileAlertsCloudConfigEntry

_LOGGER = logging.getLogger(__name__)


def stale_after_seconds(
    scan_interval_minutes: int, transmit_interval_seconds: int | None = None
) -> float:
    """Return how old `lastseen` may get before a sensor counts as stale (ADR-005, ADR-010)."""
    limit = float(max(STALE_MIN_SECONDS, STALE_POLL_FACTOR * scan_interval_minutes * 60))
    if transmit_interval_seconds is not None:
        limit = max(limit, STALE_TRANSMIT_FACTOR * transmit_interval_seconds)
    return limit


def missing_issue_id(subentry_id: str) -> str:
    """Return the repair issue ID for a sensor the API no longer returns."""
    return f"sensor_missing_{subentry_id}"


def _update_failed(err: MobileAlertsError) -> UpdateFailed:
    """Return a translated `UpdateFailed` for an API error, with the API's retry delay."""
    retry_after = (
        err.retry_after
        if isinstance(err, (MobileAlertsRateLimitError, MobileAlertsBlockedError))
        else None
    )
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"error": str(err)},
        retry_after=retry_after,
    )


class MobileAlertsCoordinator(DataUpdateCoordinator[dict[str, DeviceReading]]):
    """Single shared coordinator fetching all configured sensors for a Hub entry."""

    config_entry: MobileAlertsCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MobileAlertsCloudConfigEntry,
        client: MobileAlertsClient,
        device_ids: Sequence[str],
        scan_interval_minutes: int = DEFAULT_SCAN_INTERVAL_MINUTES,
        on_readings: Callable[[dict[str, DeviceReading]], None] | None = None,
        sensor_titles: Mapping[str, tuple[str, str]] | None = None,
        transmit_intervals: Mapping[str, int] | None = None,
    ) -> None:
        """Initialize the coordinator.

        `sensor_titles` maps a sensor ID to its (subentry ID, title), used for repair issues
        and log messages that must not contain the sensor ID itself. `transmit_intervals` maps
        a sensor ID to the seconds between its transmissions, for sensors that send rarely.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(minutes=scan_interval_minutes),
        )
        self.client = client
        self.device_ids: tuple[str, ...] = tuple(dict.fromkeys(device_ids))
        self._scan_interval_minutes = scan_interval_minutes
        self._transmit_intervals: dict[str, int] = dict(transmit_intervals or {})
        self._on_readings = on_readings
        self._sensor_titles: dict[str, tuple[str, str]] = dict(sensor_titles or {})
        self._stale_ids: set[str] = set()
        self._missing_since: dict[str, float] = {}
        self._rate_limited = False

    async def _async_update_data(self) -> dict[str, DeviceReading]:
        """Fetch latest measurements for all configured devices in one batch POST."""
        if not self.device_ids:
            return {}

        try:
            fetched = await self.client.fetch_measurements(self.device_ids)
        except MobileAlertsRateLimitError as err:
            if not self.data:
                raise _update_failed(err) from err
            # Keep the cached readings; the guard prevents a 7 minute block. Log once.
            if not self._rate_limited:
                _LOGGER.warning(
                    "Mobile Alerts rate limit reached for hub %s; keeping the last values "
                    "until it clears: %s",
                    self.config_entry.title,
                    err,
                )
                self._rate_limited = True
            else:
                _LOGGER.debug("Mobile Alerts poll still rate limited: %s", err)
            return self.data
        except MobileAlertsError as err:
            raise _update_failed(err) from err

        if self._rate_limited:
            _LOGGER.info("Mobile Alerts polls for hub %s work again", self.config_entry.title)
            self._rate_limited = False

        if self._on_readings is not None and fetched:
            self._on_readings(fetched)

        self._track_missing(fetched)

        # Preserve last known readings for active devices omitted from a transient response,
        # while dropping any devices removed from self.device_ids.
        previous = self.data or {}
        merged: dict[str, DeviceReading] = {}
        for dev_id in self.device_ids:
            if dev_id in fetched:
                merged[dev_id] = fetched[dev_id]
            elif dev_id in previous:
                merged[dev_id] = previous[dev_id]

        self._log_stale_transitions(merged)
        return merged

    @property
    def rate_limited(self) -> bool:
        """Return True while polls are rate limited and the cached readings are kept."""
        return self._rate_limited

    def get_reading(self, device_id: str) -> DeviceReading | None:
        """Return the latest parsed reading for a given device ID, if available."""
        if not self.data:
            return None
        return self.data.get(device_id)

    def stale_after_for(self, device_id: str) -> float:
        """Return the stale threshold in seconds for one sensor (ADR-005, ADR-010)."""
        return stale_after_seconds(
            self._scan_interval_minutes, self._transmit_intervals.get(device_id)
        )

    def is_stale(self, device_id: str) -> bool:
        """Return True when the sensor's last report is older than the stale threshold."""
        reading = self.get_reading(device_id)
        return self._reading_is_stale(device_id, reading)

    def _reading_is_stale(self, device_id: str, reading: DeviceReading | None) -> bool:
        if reading is None or reading.last_seen is None:
            return False
        age = dt_util.utcnow().timestamp() - reading.last_seen
        return age > self.stale_after_for(device_id)

    def _label(self, device_id: str) -> str:
        """Return a log label for a sensor without its full ID."""
        title = self._sensor_titles.get(device_id, ("", ""))[1]
        return title or mask_id(device_id)

    def _log_stale_transitions(self, readings: Mapping[str, DeviceReading]) -> None:
        """Log one warning when a sensor goes stale and one info when it recovers."""
        for dev_id in self.device_ids:
            stale = self._reading_is_stale(dev_id, readings.get(dev_id))
            if stale and dev_id not in self._stale_ids:
                self._stale_ids.add(dev_id)
                _LOGGER.warning(
                    "Sensor %s has not reported for more than %d minutes; its entities are "
                    "unavailable until it reports again",
                    self._label(dev_id),
                    int(self.stale_after_for(dev_id) // 60),
                )
            elif not stale and dev_id in self._stale_ids:
                self._stale_ids.discard(dev_id)
                _LOGGER.info("Sensor %s reports again", self._label(dev_id))

    def _track_missing(self, fetched: Mapping[str, DeviceReading]) -> None:
        """Raise a repair issue for sensors the API has not returned for a long time."""
        now = dt_util.utcnow().timestamp()
        for dev_id in self.device_ids:
            subentry_id, title = self._sensor_titles.get(dev_id, ("", ""))
            if dev_id in fetched:
                if self._missing_since.pop(dev_id, None) is not None and subentry_id:
                    ir.async_delete_issue(self.hass, DOMAIN, missing_issue_id(subentry_id))
                continue
            since = self._missing_since.setdefault(dev_id, now)
            if subentry_id and now - since >= MISSING_SENSOR_REPAIR_SECONDS:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    missing_issue_id(subentry_id),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="sensor_missing",
                    translation_placeholders={
                        "sensor": title or mask_id(dev_id),
                        "hub": self.config_entry.title,
                    },
                )
