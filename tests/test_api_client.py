"""Tests for MobileAlertsClient REST API transport, validation, and rate-limit guards."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import parse_qs

import aiohttp
import pytest
from aiohttp import web

from custom_components.mobilealerts_cloud.api import (
    INVALID_CALL_COOLDOWN_SECONDS,
    RATE_LIMIT_COOLDOWN_SECONDS,
    MobileAlertsApiError,
    MobileAlertsBlockedError,
    MobileAlertsClient,
    MobileAlertsConnectionError,
    MobileAlertsRateLimitError,
    MobileAlertsValidationError,
    RateLimitState,
)


class _FakeClock:
    """Deterministic clock for testing rate-limit windows and cooldowns."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
async def http_session() -> AsyncGenerator[aiohttp.ClientSession]:
    """Provide the injected aiohttp session the client requires (inject-websession)."""
    async with aiohttp.ClientSession() as session:
        yield session


async def _start_test_server(handler: Any) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/api/pv1/device/lastmeasurement", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/api/pv1/device/lastmeasurement"


async def test_form_urlencoded_post_and_batching(http_session: aiohttp.ClientSession) -> None:
    """Client sends official application/x-www-form-urlencoded POST and batches unique IDs."""
    captured: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.text()
        captured.append(
            {
                "content_type": request.headers.get("Content-Type", ""),
                "qs": parse_qs(body, keep_blank_values=True),
            }
        )
        return web.json_response(
            {
                "success": True,
                "devices": [
                    {
                        "deviceid": "010000AABB01",
                        "lastseen": 1795117837,
                        "lowbattery": False,
                        "measurement": {"idx": 1, "ts": 1795117830, "c": 1795117837, "t1": 21.2},
                    },
                    {
                        "deviceid": "080000AABB08",
                        "lastseen": 1795117840,
                        "lowbat": True,
                        "measurement": {
                            "idx": 2,
                            "ts": 1795117835,
                            "c": 1795117840,
                            "t1": 10.6,
                            "r": 12.9,
                        },
                    },
                ],
            }
        )

    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(
            session=http_session, api_url=url, phone_id="100000000001", send_phone_id=False
        )
        readings = await client.fetch_measurements(["010000aabb01", "080000AABB08", "010000AABB01"])
        assert len(captured) == 1
        assert captured[0]["content_type"].startswith("application/x-www-form-urlencoded")
        assert captured[0]["qs"] == {"deviceids": ["010000AABB01,080000AABB08"]}
        assert set(readings) == {"010000AABB01", "080000AABB08"}
        assert readings["010000AABB01"].values["t1"] == 21.2
        assert readings["080000AABB08"].low_battery is True
    finally:
        await runner.cleanup()


async def test_empty_device_ids_and_prevalidation(http_session: aiohttp.ClientSession) -> None:
    """Empty device lists and invalid IDs never issue an HTTP call."""
    calls = 0

    async def handler(_request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.json_response({"success": True, "devices": []})

    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(session=http_session, api_url=url)
        assert await client.fetch_measurements([]) == {}
        assert calls == 0

        try:
            await client.fetch_measurements(["INVALID_ID"])
        except MobileAlertsValidationError as err:
            assert "INVALID_ID" not in str(err)
        else:
            raise AssertionError("Expected MobileAlertsValidationError")
        assert calls == 0

        try:
            MobileAlertsClient(
                session=http_session, api_url=url, phone_id="abc123", send_phone_id=True
            )
        except MobileAlertsValidationError:
            pass
        else:
            raise AssertionError("Expected MobileAlertsValidationError for non-numeric phone_id")
    finally:
        await runner.cleanup()


async def test_sliding_window_rate_limit_guard(http_session: aiohttp.ClientSession) -> None:
    """Client allows 3 calls/min per sensor and prevents the 4th locally."""
    calls = 0

    async def handler(_request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.json_response(
            {
                "success": True,
                "devices": [
                    {
                        "deviceid": "010000AABB01",
                        "lastseen": 1795117837,
                        "lowbat": False,
                        "measurement": {
                            "idx": calls,
                            "ts": 1795117830,
                            "c": 1795117837,
                            "t1": 20.0,
                        },
                    }
                ],
            }
        )

    clock = _FakeClock()
    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(session=http_session, api_url=url, clock=clock)
        for _ in range(3):
            await client.fetch_measurements(["010000AABB01"])
            clock.advance(5.0)
        assert calls == 3

        try:
            await client.fetch_measurements(["010000AABB01"])
        except MobileAlertsRateLimitError as err:
            assert err.retry_after > 0
            # Sensor IDs are credentials: messages show only the last four characters.
            assert "010000AABB01" not in str(err)
            assert "...BB01" in str(err)
        else:
            raise AssertionError("Expected local rate limit guard on 4th call within 60s")
        assert calls == 3

        # Once 60s passes since the first call, a new slot opens
        clock.advance(50.0)
        await client.fetch_measurements(["010000AABB01"])
        assert calls == 4
    finally:
        await runner.cleanup()


async def test_http_429_and_403_cooldowns(http_session: aiohttp.ClientSession) -> None:
    """HTTP 429 triggers 7m cooldown; HTTP 403 triggers 15m IP block cooldown."""
    status_to_return = 429
    calls = 0

    async def handler(_request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if status_to_return == 200:
            return web.json_response({"success": True, "devices": []})
        return web.Response(status=status_to_return, text="blocked")

    clock = _FakeClock()
    state = RateLimitState()
    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(
            session=http_session, api_url=url, rate_state=state, clock=clock
        )

        try:
            await client.fetch_measurements(["010000AABB01"])
        except MobileAlertsRateLimitError as err:
            assert err.retry_after == RATE_LIMIT_COOLDOWN_SECONDS
        else:
            raise AssertionError("Expected MobileAlertsRateLimitError on HTTP 429")
        assert calls == 1

        # Subsequent call within 7 minutes must fail locally without hitting server
        clock.advance(100.0)
        try:
            await client.fetch_measurements(["010000AABB01"])
        except MobileAlertsRateLimitError:
            pass
        else:
            raise AssertionError("Expected cooldown MobileAlertsRateLimitError")
        assert calls == 1

        # After 7 minutes, HTTP 403 triggers 15m IP cooldown
        clock.advance(400.0)
        status_to_return = 403
        try:
            await client.fetch_measurements(["010000AABB01"])
        except MobileAlertsBlockedError as err:
            assert err.retry_after == INVALID_CALL_COOLDOWN_SECONDS
        else:
            raise AssertionError("Expected MobileAlertsBlockedError on HTTP 403")
        assert calls == 2

        # Another device must also respect IP-wide 403 block
        clock.advance(300.0)
        try:
            await client.fetch_measurements(["020000AABB02"])
        except MobileAlertsBlockedError:
            pass
        else:
            raise AssertionError("Expected IP-wide cooldown MobileAlertsBlockedError")
        assert calls == 2

        clock.advance(700.0)
        status_to_return = 200
        await client.fetch_measurements(["020000AABB02"])
        assert calls == 3
    finally:
        await runner.cleanup()


async def test_api_error_json_and_phoneid_400_fallback(http_session: aiohttp.ClientSession) -> None:
    """Client raises MobileAlertsApiError on success=false and falls back on phoneid HTTP 400."""
    mode = "phoneid_400"
    received_forms: list[dict[str, list[str]]] = []

    async def handler(request: web.Request) -> web.Response:
        qs = parse_qs(await request.text(), keep_blank_values=True)
        received_forms.append(qs)
        if mode == "phoneid_400" and "phoneid" in qs:
            return web.Response(status=400, text="Bad Request")
        if mode == "api_error":
            return web.Response(
                text=json.dumps(
                    {"success": False, "errorcode": 1, "errormessage": "Some error message"}
                ),
                content_type="application/json",
            )
        return web.json_response(
            {
                "success": True,
                "devices": [
                    {
                        "deviceid": "020000AABB02",
                        "lastseen": 1795117800,
                        "lowbat": False,
                        "measurement": {"idx": 1, "ts": 1795117795, "c": 1795117800, "t1": 23.4},
                    }
                ],
            }
        )

    clock = _FakeClock()
    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(
            session=http_session,
            api_url=url,
            phone_id="100000000001",
            send_phone_id=True,
            clock=clock,
        )
        readings = await client.fetch_measurements(["020000AABB02"])
        assert readings["020000AABB02"].values["t1"] == 23.4
        assert "phoneid" in received_forms[0]
        assert "phoneid" not in received_forms[1]
        assert client.rate_limit_status()["phone_id_rejected_fallback"] is True

        # Next call omits phoneid directly
        clock.advance(61.0)
        await client.fetch_measurements(["020000AABB02"])
        assert "phoneid" not in received_forms[2]

        # The fallback is remembered per phone ID: a different phone ID sharing the same
        # state (e.g. after "Reconfigure") is sent again.
        other = MobileAlertsClient(
            session=http_session,
            api_url=url,
            phone_id="100000000002",
            send_phone_id=True,
            rate_state=client.rate_state,
            clock=clock,
        )
        assert other.rate_limit_status()["phone_id_rejected_fallback"] is False
        clock.advance(61.0)
        await other.fetch_measurements(["020000AABB02"])
        assert received_forms[3]["phoneid"] == ["100000000002"]

        mode = "api_error"
        clock.advance(61.0)
        try:
            await client.fetch_measurements(["020000AABB02"])
        except MobileAlertsApiError as err:
            assert err.error_code == 1
            assert err.error_message == "Some error message"
        else:
            raise AssertionError("Expected MobileAlertsApiError")
    finally:
        await runner.cleanup()
        await asyncio.sleep(0)


async def test_mock_api_server_serves_all_profiles(http_session: aiohttp.ClientSession) -> None:
    """Verify standalone MockMobileAlertsServer serves all 20 documented prefixes."""
    from mock_api_server import MockMobileAlertsServer

    server = MockMobileAlertsServer(port=0)
    await server.start()
    try:
        client = MobileAlertsClient(session=http_session, api_url=server.url)
        all_ids = sorted(server.devices)
        assert len(all_ids) == 21  # 20 documented profiles + the sentinel edge case
        readings = await client.fetch_measurements(all_ids)
        assert set(readings) == set(all_ids)
    finally:
        await server.stop()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not json", MobileAlertsConnectionError),
        ("[1, 2]", MobileAlertsConnectionError),
        ('{"success": true, "devices": "none"}', {}),
        ('{"success": true, "devices": [1, {"deviceid": ""}]}', {}),
    ],
)
async def test_malformed_responses(
    http_session: aiohttp.ClientSession, body: str, expected: Any
) -> None:
    """Malformed bodies raise a connection error or yield no readings, never crash."""

    async def handler(_request: web.Request) -> web.Response:
        return web.Response(text=body, content_type="application/json")

    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(session=http_session, api_url=url)
        if isinstance(expected, dict):
            assert await client.fetch_measurements(["010000AABB01"]) == expected
        else:
            with pytest.raises(expected):
                await client.fetch_measurements(["010000AABB01"])
        assert client.rate_state is not None
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, json.dumps({"success": False, "errorcode": 1, "errormessage": "Bad 020000AABB02"})),
        (500, "Internal error for deviceids=020000aabb02"),
    ],
)
async def test_api_error_messages_never_contain_full_sensor_ids(
    http_session: aiohttp.ClientSession, status: int, body: str
) -> None:
    """Sensor IDs echoed by the API are masked before they reach logs or the UI."""

    async def handler(_request: web.Request) -> web.Response:
        return web.Response(status=status, text=body, content_type="application/json")

    runner, url = await _start_test_server(handler)
    try:
        client = MobileAlertsClient(session=http_session, api_url=url)
        with pytest.raises(MobileAlertsApiError) as err:
            await client.fetch_measurements(["020000AABB02"])
    finally:
        await runner.cleanup()

    assert "020000AABB02" not in str(err.value).upper()
    assert "...BB02" in str(err.value).upper()
    assert "020000AABB02" not in (err.value.error_message or "").upper()
