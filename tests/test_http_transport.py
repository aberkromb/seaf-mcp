"""Tests for the Streamable HTTP transport.

stdio stays the default; the HTTP mode serves the same 17 tools at /mcp in
stateless mode, with Host/Origin validation against DNS rebinding.
"""

import asyncio
import os
import socket
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import load_catalog
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_ready(port: int, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient() as c:
                await c.get(f"http://127.0.0.1:{port}/mcp")
            return
        except httpx.ConnectError:
            await asyncio.sleep(0.1)
    raise RuntimeError("HTTP MCP server did not start")


@pytest.fixture
async def http_server():
    port = free_port()
    config = Config(api_base_url=BASE, health_check_path="",
                    mcp_transport="streamable-http", mcp_http_port=port)
    server = SeafMCPServer(config, load_catalog(CATALOG))

    def portal_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"message": "app is alive"})
        return httpx.Response(404, json={"message": "not mocked"})
    server.rest_client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(portal_handler), timeout=30)

    task = asyncio.create_task(server.run_http())
    await wait_ready(port)
    yield port
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    await server.cleanup()


async def test_http_initialize_list_tools_and_call(http_server):
    async with streamablehttp_client(f"http://127.0.0.1:{http_server}/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            assert init.serverInfo.name
            tools = await session.list_tools()
            assert len(tools.tools) == 17
            result = await session.call_tool("get_health", {})
            assert "app is alive" in result.content[0].text


async def test_foreign_origin_rejected(http_server):
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            f"http://127.0.0.1:{http_server}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Origin": "http://evil.example.com",
                     "Accept": "application/json, text/event-stream"})
    assert resp.status_code == 403


async def test_foreign_host_rejected(http_server):
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            f"http://127.0.0.1:{http_server}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Host": "attacker.example.com",
                     "Accept": "application/json, text/event-stream"})
    assert resp.status_code in (400, 403, 421)


async def test_unknown_path_is_404(http_server):
    async with httpx.AsyncClient() as c:
        resp = await c.get(f"http://127.0.0.1:{http_server}/nope")
    assert resp.status_code == 404


def test_config_validation(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", BASE)
    assert Config().mcp_transport == "stdio"
    assert Config().mcp_http_host == "127.0.0.1"
    assert Config().mcp_http_port == 14280
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    assert Config().mcp_transport == "streamable-http"
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    with pytest.raises(Exception, match="MCP_TRANSPORT"):
        Config()
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_HTTP_PORT", "0")
    with pytest.raises(Exception, match="greater than or equal to 1"):
        Config()
