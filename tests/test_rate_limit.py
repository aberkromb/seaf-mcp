"""Tests for the optional request pacing of RestClient.

003 found no portal-side rate limit, so pacing is off by default
(API_MIN_INTERVAL_SECONDS=0). The slot is reserved under a lock and the wait
happens outside it; the interval applies to every portal request, including
retries (019) and concurrent calls.
"""

import asyncio
import os
import sys
import time

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.rest_client import RestClient

BASE = "http://seaf.test"
NO_DELAY = (0, 0, 0)
INTERVAL = 0.05

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@respx.mock
async def test_concurrent_calls_respect_min_interval():
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    async with RestClient(BASE, retry_delays=NO_DELAY, min_interval=INTERVAL) as client:
        started = time.monotonic()
        await asyncio.gather(*(client.call("GET", "/x") for _ in range(3)))
        elapsed = time.monotonic() - started
    assert elapsed >= INTERVAL * 2  # first call free, two waits


@respx.mock
async def test_disabled_by_default():
    assert RestClient(BASE).min_interval == 0
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        started = time.monotonic()
        await asyncio.gather(*(client.call("GET", "/x") for _ in range(5)))
        assert time.monotonic() - started < INTERVAL


@respx.mock
async def test_interval_applies_to_retries():
    route = respx.get(f"{BASE}/x").mock(side_effect=[
        httpx.Response(503, text="x"),
        httpx.Response(503, text="x"),
        httpx.Response(200, json={}),
    ])
    async with RestClient(BASE, retry_delays=NO_DELAY, min_interval=INTERVAL) as client:
        started = time.monotonic()
        resp = await client.call("GET", "/x")
        elapsed = time.monotonic() - started
    assert resp.is_success() and route.call_count == 3
    assert elapsed >= INTERVAL * 2


@respx.mock
async def test_waiting_call_is_cancellable():
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    async with RestClient(BASE, retry_delays=NO_DELAY, min_interval=60) as client:
        first = asyncio.create_task(client.call("GET", "/x"))
        second = asyncio.create_task(client.call("GET", "/x"))
        await asyncio.sleep(0.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        await first
    assert not client._slot_lock.locked()


def test_config_validates_non_negative(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", BASE)
    monkeypatch.setenv("API_MIN_INTERVAL_SECONDS", "0.5")
    assert Config().api_min_interval_seconds == 0.5
    monkeypatch.setenv("API_MIN_INTERVAL_SECONDS", "-1")
    with pytest.raises(Exception, match="greater than or equal to 0"):
        Config()
