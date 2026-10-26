"""Tests for the transient-error retry policy of RestClient.

Policy: retry only ConnectError/ConnectTimeout and HTTP 502/503/504,
at most 4 attempts (1 + 3 retries) with pauses 1/2/4 s. ReadTimeout, 4xx and
HTTP 500 are never retried; probe() is not retried.
"""

import asyncio
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.errors import RestClientError
from seaf_mcp.rest_client import RestClient

BASE = "http://seaf.test"
NO_DELAY = (0, 0, 0)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@respx.mock
async def test_recovers_after_connect_error():
    route = respx.get(f"{BASE}/x").mock(side_effect=[
        httpx.ConnectError("refused"),
        httpx.Response(200, json={"ok": True}),
    ])
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("GET", "/x")
    assert resp.is_success() and resp.body == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_recovers_after_503():
    route = respx.get(f"{BASE}/x").mock(side_effect=[
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json={"ok": True}),
    ])
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("GET", "/x")
    assert resp.is_success()
    assert route.call_count == 2


@respx.mock
async def test_exhaustion_makes_exactly_four_calls():
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("refused"))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        with pytest.raises(RestClientError, match="Cannot connect"):
            await client.call("GET", "/x")
    assert route.call_count == 4


@respx.mock
async def test_exhaustion_keeps_last_gateway_status():
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(504, text="timeout"))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("GET", "/x")
    assert resp.status_code == 504 and "timeout" in resp.error
    assert route.call_count == 4


@pytest.mark.parametrize("status", [400, 401, 403, 404])
@respx.mock
async def test_no_retry_on_4xx(status):
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(status, text="no"))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("GET", "/x")
    assert resp.status_code == status
    assert route.call_count == 1


@respx.mock
async def test_no_retry_on_500_with_jsonata_code():
    route = respx.post(f"{BASE}/x").mock(return_value=httpx.Response(
        500, json={"message": "m", "error": {"code": "T1006", "token": "take"}}))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("POST", "/x", body={"query": "$take(1)"})
    assert resp.status_code == 500
    assert route.call_count == 1


@respx.mock
async def test_no_retry_on_500_without_code():
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(500, text="oops"))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        resp = await client.call("GET", "/x")
    assert resp.status_code == 500
    assert route.call_count == 1


@respx.mock
async def test_no_retry_on_read_timeout():
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    async with RestClient(BASE, timeout=7, retry_delays=NO_DELAY) as client:
        with pytest.raises(RestClientError, match="timed out after 7s"):
            await client.call("GET", "/x")
    assert route.call_count == 1


@respx.mock
async def test_probe_not_retried():
    route = respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    async with RestClient(BASE, retry_delays=NO_DELAY) as client:
        ok, _ = await client.probe("/health")
    assert not ok
    assert route.call_count == 1


@respx.mock
async def test_retry_wait_is_cancellable():
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("refused"))
    async with RestClient(BASE, retry_delays=(60, 60, 60)) as client:
        task = asyncio.create_task(client.call("GET", "/x"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@respx.mock
async def test_retry_logs_attempt_and_reason(caplog):
    respx.get(f"{BASE}/x").mock(side_effect=[
        httpx.ConnectError("refused"),
        httpx.Response(200, json={}),
    ])
    import logging
    with caplog.at_level(logging.WARNING):
        async with RestClient(BASE, retry_delays=(0, 0, 0)) as client:
            await client.call("GET", "/x", body={"secret": "payload"})
    messages = [r.message for r in caplog.records]
    assert any("attempt 1/4" in m and "ConnectError" in m for m in messages)
    assert all("payload" not in m for m in messages)
