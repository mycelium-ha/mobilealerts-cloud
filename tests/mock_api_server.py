#!/usr/bin/env python3
"""Mock Mobile Alerts REST API server covering all documented profiles (ID01-ID20).

Used by the test suite (`tests/conftest.py`, fixture `mock_api`) and as a standalone
server for manual testing against a local Home Assistant.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from aiohttp import web

_LOGGER = logging.getLogger(__name__)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "live_responses.json"
API_PATH = "/api/pv1/device/lastmeasurement"


def load_fixture_devices() -> dict[str, dict[str, Any]]:
    """Return every fixture device payload (documented profiles and edge cases) by ID."""
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    devices: dict[str, dict[str, Any]] = {}
    for group in ("all_documented_profiles", "edge_cases"):
        for record in data[group].values():
            if isinstance(record, dict) and "deviceid" in record:
                devices[record["deviceid"].upper()] = record
    return devices


class MockMobileAlertsServer:
    """HTTP mock server speaking the Mobile Alerts REST API v1.

    `devices` maps sensor IDs to the device objects returned by `lastmeasurement`; tests
    change it to simulate new measurements. `requests` records the parsed form of every
    request. `next_status` / `next_payload` let a test force one error response.
    """

    def __init__(self, port: int = 8888, *, live_timestamps: bool = True) -> None:
        self.port = port
        self.live_timestamps = live_timestamps
        self.devices = load_fixture_devices()
        self.requests: list[dict[str, Any]] = []
        self.next_status: int | None = None
        self.next_payload: Any = None
        self.app = web.Application()
        self.app.router.add_post(API_PATH, self.handle_lastmeasurement)
        self.app.router.add_get("/health", self.handle_health)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def url(self) -> str:
        """Return the full `lastmeasurement` URL of the running server."""
        return f"http://127.0.0.1:{self.port}{API_PATH}"

    @property
    def batches(self) -> list[str]:
        """Return the raw `deviceids` value of every request, in order."""
        return [req["deviceids"] for req in self.requests]

    async def handle_lastmeasurement(self, request: web.Request) -> web.Response:
        """Handle POST /api/pv1/device/lastmeasurement (form-urlencoded)."""
        body = await request.text()
        qs = parse_qs(body, keep_blank_values=True)
        raw_ids = qs.get("deviceids", [""])[0]
        phone_id = qs.get("phoneid", [None])[0]
        self.requests.append(
            {
                "content_type": request.headers.get("Content-Type", ""),
                "deviceids": raw_ids,
                "phoneid": phone_id,
            }
        )

        if self.next_status is not None or self.next_payload is not None:
            status = self.next_status or 200
            payload = self.next_payload
            self.next_status = None
            self.next_payload = None
            if payload is None:
                return web.Response(status=status, text="forced error")
            return web.json_response(payload, status=status)

        if not raw_ids.strip():
            return web.json_response(
                {"success": False, "errorcode": 1, "errormessage": "Missing deviceids"},
                status=400,
            )

        now = int(time.time())
        result_devices = []
        for token in raw_ids.split(","):
            dev_id = token.strip().upper()
            if dev_id in self.devices:
                item = copy.deepcopy(self.devices[dev_id])
                if self.live_timestamps:
                    item["lastseen"] = now
                    if isinstance(item.get("measurement"), dict):
                        item["measurement"]["ts"] = now - 5
                        item["measurement"]["c"] = now
                result_devices.append(item)

        payload_ok: dict[str, Any] = {"success": True, "devices": result_devices}
        if phone_id:
            payload_ok["phoneid"] = phone_id
        return web.json_response(payload_ok)

    async def handle_health(self, _request: web.Request) -> web.Response:
        """Return health status and supported mock device IDs."""
        return web.json_response(
            {
                "status": "healthy",
                "device_count": len(self.devices),
                "devices": sorted(self.devices),
            }
        )

    async def start(self) -> None:
        """Start the HTTP server; port 0 picks a free port."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()
        server = self._site._server
        if server is not None and server.sockets:  # type: ignore[union-attr]
            self.port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        _LOGGER.info("Mock Mobile Alerts API running on %s", self.url)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()


async def _run(port: int) -> None:
    server = MockMobileAlertsServer(port=port)
    await server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Mock Mobile Alerts REST API server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MOCK_API_PORT", "8888")),
        help="TCP port to listen on (default: 8888)",
    )
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args.port))


if __name__ == "__main__":
    main()
