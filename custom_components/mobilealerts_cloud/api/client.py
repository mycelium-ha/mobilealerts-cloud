"""Asynchronous REST API client for Mobile Alerts Public Server API v1."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .models import (
    DeviceReading,
    is_valid_device_id,
    is_valid_phone_id,
    normalize_device_id,
    normalize_phone_id,
    parse_device_reading,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_API_URL = "https://www.data199.com/api/pv1/device/lastmeasurement"

# Official API limits (Public Server API documentation, page 1)
MAX_CALLS_PER_MINUTE_PER_SENSOR = 3
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_COOLDOWN_SECONDS = 420.0  # 7 minutes on HTTP 429
INVALID_CALL_COOLDOWN_SECONDS = 900.0  # 15 minutes on HTTP 403
REQUEST_TIMEOUT_SECONDS = 30.0


def mask_id(sensor_id: str) -> str:
    """Return a sensor ID shortened to its last four characters for messages and logs.

    Sensor IDs are credentials: anyone who knows one can read the sensor's data.
    """
    return f"...{sensor_id[-4:]}"


_SENSOR_ID_IN_TEXT_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{12}(?![0-9A-Fa-f])")


def mask_ids_in_text(text: str) -> str:
    """Return API-provided text with every sensor-ID-shaped token replaced by `mask_id`."""
    return _SENSOR_ID_IN_TEXT_RE.sub(lambda match: mask_id(match.group(0).upper()), text)


class MobileAlertsError(Exception):
    """Base exception for Mobile Alerts client errors."""


class MobileAlertsConnectionError(MobileAlertsError):
    """Raised on network, timeout, or invalid JSON transport failures."""


class MobileAlertsValidationError(MobileAlertsError):
    """Raised when input IDs fail local validation before hitting the API."""


class MobileAlertsApiError(MobileAlertsError):
    """Raised when the API returns HTTP error status or success=false JSON."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_code: int | str | None = None,
        error_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.error_message = error_message


class MobileAlertsRateLimitError(MobileAlertsError):
    """Raised when per-sensor rate limit (HTTP 429 or local guard) is active."""

    def __init__(self, message: str, *, retry_after: float = RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        super().__init__(message)
        self.retry_after = max(0.0, retry_after)


class MobileAlertsBlockedError(MobileAlertsError):
    """Raised when the IP is temporarily blocked (HTTP 403 for 15 minutes)."""

    def __init__(self, message: str, *, retry_after: float = INVALID_CALL_COOLDOWN_SECONDS) -> None:
        super().__init__(message)
        self.retry_after = max(0.0, retry_after)


@dataclass
class RateLimitState:
    """Mutable rate-limit state, shared by every client of one Home Assistant instance.

    The IP block applies to the whole IP and sensor IDs are unique across hubs, so one
    state guards all hubs, flows and the migration together and survives reloads.
    """

    sensor_call_times: dict[str, deque[float]] = field(default_factory=dict)
    sensor_cooldown_until: dict[str, float] = field(default_factory=dict)
    ip_blocked_until: float = 0.0
    rejected_phone_ids: set[str] = field(default_factory=set)


class MobileAlertsClient:
    """Async HTTP client implementing the official Mobile Alerts REST API."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        api_url: str | None = None,
        phone_id: str | None = None,
        send_phone_id: bool = False,
        rate_state: RateLimitState | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session = session
        self._api_url: str = api_url or DEFAULT_API_URL
        cleaned_phone_id = normalize_phone_id(phone_id) if phone_id else ""
        self._phone_id = cleaned_phone_id or None
        self._send_phone_id = bool(send_phone_id and self._phone_id)
        self._rate_state = rate_state if rate_state is not None else RateLimitState()
        self._clock = clock or time.monotonic

        if self._phone_id is not None and not is_valid_phone_id(self._phone_id):
            raise MobileAlertsValidationError("Invalid Mobile Alerts phone_id format")

    @property
    def api_url(self) -> str:
        """Return the configured REST API endpoint URL."""
        return self._api_url

    @property
    def rate_state(self) -> RateLimitState:
        """Return the shared rate-limit tracking state."""
        return self._rate_state

    def rate_limit_status(self) -> dict[str, Any]:
        """Return a redacted summary of active rate limits and recent call windows."""
        now = self._clock()
        ip_remaining = max(0.0, self._rate_state.ip_blocked_until - now)
        active_sensor_cooldowns = {
            mask_id(sid): round(until - now, 1)
            for sid, until in self._rate_state.sensor_cooldown_until.items()
            if until > now
        }
        recent_calls = {
            mask_id(sid): sum(1 for ts in times if now - ts < RATE_LIMIT_WINDOW_SECONDS)
            for sid, times in self._rate_state.sensor_call_times.items()
        }
        return {
            "ip_blocked_remaining_seconds": round(ip_remaining, 1),
            "sensor_cooldowns_seconds": active_sensor_cooldowns,
            "recent_calls_last_minute": recent_calls,
            "phone_id_rejected_fallback": self._phone_id in self._rate_state.rejected_phone_ids,
        }

    def _validate_and_normalize_ids(self, device_ids: Sequence[str]) -> list[str]:
        """Validate and deduplicate 12-hex device IDs prior to making an API request."""
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_id in device_ids:
            dev_id = normalize_device_id(raw_id)
            if not is_valid_device_id(dev_id):
                raise MobileAlertsValidationError(
                    "Invalid Mobile Alerts device ID; expected 12 hex characters."
                )
            if dev_id not in seen:
                seen.add(dev_id)
                normalized.append(dev_id)
        return normalized

    def _check_rate_limits(self, device_ids: Sequence[str]) -> None:
        """Raise immediately if any cooldown or per-sensor 3-calls/min budget is exhausted."""
        now = self._clock()

        if now < self._rate_state.ip_blocked_until:
            remaining = self._rate_state.ip_blocked_until - now
            raise MobileAlertsBlockedError(
                f"Mobile Alerts IP block active for {int(remaining)}s (HTTP 403 cooldown).",
                retry_after=remaining,
            )

        for dev_id in device_ids:
            cooldown_until = self._rate_state.sensor_cooldown_until.get(dev_id, 0.0)
            if now < cooldown_until:
                remaining = cooldown_until - now
                raise MobileAlertsRateLimitError(
                    f"Mobile Alerts rate limit cooldown active for sensor {mask_id(dev_id)} "
                    f"({int(remaining)}s remaining).",
                    retry_after=remaining,
                )

            history = self._rate_state.sensor_call_times.get(dev_id)
            if history is not None:
                while history and now - history[0] >= RATE_LIMIT_WINDOW_SECONDS:
                    history.popleft()
                if len(history) >= MAX_CALLS_PER_MINUTE_PER_SENSOR:
                    wait_seconds = max(1.0, RATE_LIMIT_WINDOW_SECONDS - (now - history[0]))
                    raise MobileAlertsRateLimitError(
                        f"Client-side rate-limit guard: sensor {mask_id(dev_id)} already queried "
                        f"{MAX_CALLS_PER_MINUTE_PER_SENSOR} times in the last minute; "
                        f"wait {wait_seconds:.1f}s to avoid a 7-minute API block.",
                        retry_after=wait_seconds,
                    )

    def _record_call_timestamps(self, device_ids: Sequence[str]) -> None:
        """Record an outgoing API call timestamp for each queried sensor ID."""
        now = self._clock()
        for dev_id in device_ids:
            history = self._rate_state.sensor_call_times.setdefault(dev_id, deque())
            while history and now - history[0] >= RATE_LIMIT_WINDOW_SECONDS:
                history.popleft()
            history.append(now)

    async def _post_form(self, form_data: dict[str, str]) -> tuple[int, str]:
        """Send an application/x-www-form-urlencoded POST request."""
        client_timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.post(
            self._api_url,
            data=form_data,
            timeout=client_timeout,
        ) as response:
            return response.status, await response.text()

    async def fetch_measurements(self, device_ids: Sequence[str]) -> dict[str, DeviceReading]:
        """Fetch the latest measurements for one or more Mobile Alerts sensor IDs."""
        normalized_ids = self._validate_and_normalize_ids(device_ids)
        if not normalized_ids:
            return {}

        self._check_rate_limits(normalized_ids)

        include_phone_id = (
            self._send_phone_id
            and self._phone_id is not None
            and self._phone_id not in self._rate_state.rejected_phone_ids
        )
        form_data: dict[str, str] = {"deviceids": ",".join(normalized_ids)}
        if include_phone_id and self._phone_id is not None:
            form_data["phoneid"] = self._phone_id

        try:
            self._record_call_timestamps(normalized_ids)
            status, body = await self._post_form(form_data)

            # Upstream API returns HTTP 400 if an opt-in phoneid isn't linked to all
            # queried sensors (issue #54). Retry once without it and remember that this
            # phone ID was rejected; a different phone ID gets its own chance.
            if status == 400 and include_phone_id and self._phone_id is not None:
                _LOGGER.warning(
                    "Mobile Alerts API returned HTTP 400 with phoneid attached; "
                    "retrying without phoneid and no longer sending this phone ID."
                )
                self._rate_state.rejected_phone_ids.add(self._phone_id)
                self._check_rate_limits(normalized_ids)
                self._record_call_timestamps(normalized_ids)
                status, body = await self._post_form({"deviceids": ",".join(normalized_ids)})
        except MobileAlertsError:
            raise
        except TimeoutError as err:
            raise MobileAlertsConnectionError("Timeout connecting to Mobile Alerts API") from err
        except aiohttp.ClientError as err:
            raise MobileAlertsConnectionError(
                f"Connection error talking to Mobile Alerts API: {err}"
            ) from err

        now = self._clock()
        if status == 429:
            until = now + RATE_LIMIT_COOLDOWN_SECONDS
            for dev_id in normalized_ids:
                self._rate_state.sensor_cooldown_until[dev_id] = until
            raise MobileAlertsRateLimitError(
                "Mobile Alerts API rate limit exceeded (HTTP 429); cooling down for 7 minutes.",
                retry_after=RATE_LIMIT_COOLDOWN_SECONDS,
            )

        if status == 403:
            self._rate_state.ip_blocked_until = now + INVALID_CALL_COOLDOWN_SECONDS
            raise MobileAlertsBlockedError(
                "Mobile Alerts API temporarily blocked IP (HTTP 403); cooling down for 15 minutes.",
                retry_after=INVALID_CALL_COOLDOWN_SECONDS,
            )

        if status != 200:
            preview = mask_ids_in_text(body[:200]) if body else ""
            raise MobileAlertsApiError(
                f"Mobile Alerts API returned HTTP {status}" + (f": {preview}" if preview else ""),
                status=status,
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise MobileAlertsConnectionError(
                f"Invalid JSON response from Mobile Alerts API: {err}"
            ) from err

        if not isinstance(payload, dict):
            raise MobileAlertsConnectionError("Unexpected non-object JSON payload from API")

        if payload.get("success") is not True:
            error_code = payload.get("errorcode")
            error_msg = mask_ids_in_text(str(payload.get("errormessage") or "Unknown API error"))
            raise MobileAlertsApiError(
                f"Mobile Alerts API error ({error_code}): {error_msg}",
                status=status,
                error_code=error_code,
                error_message=str(error_msg),
            )

        devices_raw = payload.get("devices")
        if not isinstance(devices_raw, list):
            return {}

        readings: dict[str, DeviceReading] = {}
        for item in devices_raw:
            if not isinstance(item, dict):
                continue
            reading = parse_device_reading(item)
            if reading.device_id:
                readings[reading.device_id] = reading

        return readings
