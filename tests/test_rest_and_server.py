"""Tests for REST client, response formatting, argument validation and server dispatch."""

import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import Endpoint, EndpointCatalog
from seaf_mcp.errors import RestClientError, SeafMCPError
from seaf_mcp.response_formatter import truncate
from seaf_mcp.rest_client import RestClient
from seaf_mcp.server import SeafMCPServer
from seaf_mcp.tool_factory import filter_and_create_tools, validate_arguments

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_endpoint(**kwargs) -> Endpoint:
    defaults = dict(
        id=None,
        description="Test endpoint",
        method="GET",
        path="/api/test",
        pathParams=[],
        queryParams=[],
        bodyParams=[],
        readOnly=True,
        requestExample="",
        responseDescription="",
    )
    defaults.update(kwargs)
    return Endpoint(**defaults)


JSONATA = make_endpoint(
    id="jsonata",
    path="/api/jsonata/:query",
    pathParams=[{"name": "query", "type": "string", "required": True, "description": "q"}],
    queryParams=[{"name": "params", "type": "string", "required": False, "description": "p"}],
)


def make_server(endpoints, max_chars=50000) -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=max_chars, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=endpoints))


# --- path / query encoding -------------------------------------------------

def test_path_params_fully_encoded():
    path = RestClient.substitute_path_params(
        "/api/jsonata/:query", {"query": "$.a[type='x/y'] ? #z"}
    )
    assert path == "/api/jsonata/%24.a%5Btype%3D%27x%2Fy%27%5D%20%3F%20%23z"


def test_path_params_no_prefix_clobbering():
    path = RestClient.substitute_path_params("/a/:id/:idx", {"id": "1", "idx": "2"})
    assert path == "/a/1/2"


def test_missing_path_param_raises():
    with pytest.raises(RestClientError, match="query"):
        RestClient.substitute_path_params("/api/:query", {})


def test_query_params_serialization():
    assert RestClient.build_query_params(
        {"a": None, "b": "", "c": {"k": 1}, "d": True, "e": 5}
    ) == {"c": '{"k": 1}', "d": "true", "e": "5"}


@respx.mock
async def test_call_sends_encoded_url():
    route = respx.get(url__startswith=f"{BASE}/api/jsonata/").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with RestClient(BASE) as client:
        resp = await client.call(
            "GET", "/api/jsonata/:query", {"query": "a/b"}, {"params": '{"x":1}'}
        )
    assert resp.is_success() and resp.body == {"ok": True}
    url = route.calls.last.request.url
    assert url.raw_path == b"/api/jsonata/a%2Fb?params=%7B%22x%22%3A1%7D"


@respx.mock
async def test_timeout_error():
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    async with RestClient(BASE, timeout=7) as client:
        with pytest.raises(RestClientError, match="timed out after 7s"):
            await client.call("GET", "/x")


@respx.mock
async def test_connect_error_has_hint():
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("refused"))
    async with RestClient(BASE, retry_delays=(0, 0, 0)) as client:
        with pytest.raises(RestClientError, match="host.docker.internal"):
            await client.call("GET", "/x")


@respx.mock
async def test_non_json_5xx():
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(502, text="<html>Bad Gateway</html>"))
    async with RestClient(BASE, retry_delays=(0, 0, 0)) as client:
        resp = await client.call("GET", "/x")
    assert resp.is_server_error() and "Bad Gateway" in resp.error


# --- truncation ------------------------------------------------------------

def test_truncate():
    assert truncate("abc", 0) == "abc"
    assert truncate("abc", 10) == "abc"
    out = truncate("x" * 2000, 1000)
    assert len(out) <= 1000  # лимит теперь на весь текст, включая примечание
    assert out.startswith("x" * 900) and "[truncated: showing" in out and "of 2000" in out


def test_config_max_response_chars_bounds():
    Config(api_base_url=BASE, max_response_chars=0)
    with pytest.raises(Exception):
        Config(api_base_url=BASE, max_response_chars=10)


# --- validation / factory ---------------------------------------------------

def test_validate_arguments():
    assert validate_arguments(JSONATA, {"query": "x"}) is None
    err = validate_arguments(JSONATA, {"bogus": 1})
    assert "Unknown parameter(s): bogus" in err and "Missing required parameter(s): query" in err


def test_schema_is_strict():
    tools, _, _ = filter_and_create_tools([JSONATA])
    assert tools[0]["inputSchema"]["additionalProperties"] is False


def test_duplicate_tool_names_rejected():
    a = make_endpoint(path="/a-b")
    b = make_endpoint(path="/a_b")
    with pytest.raises(SeafMCPError, match="Duplicate tool name"):
        filter_and_create_tools([a, b])


def test_param_collision_rejected():
    ep = make_endpoint(
        path="/a/:x",
        pathParams=[{"name": "x", "type": "string", "required": True, "description": ""}],
        queryParams=[{"name": "x", "type": "string", "required": False, "description": ""}],
    )
    with pytest.raises(SeafMCPError, match="both path and query"):
        filter_and_create_tools([ep])


# --- server dispatch --------------------------------------------------------

@respx.mock
async def test_server_executes_and_truncates():
    respx.get(url__startswith=f"{BASE}/api/jsonata/").mock(
        return_value=httpx.Response(200, json={"data": "y" * 5000})
    )
    server = make_server([JSONATA], max_chars=1000)
    out = await server._execute_tool("jsonata", {"query": "$"})
    assert "Status:\n200" in out and "[truncated:" in out
    await server.cleanup()


async def test_server_rejects_unknown_args_without_http():
    server = make_server([JSONATA])
    with respx.mock(assert_all_called=False) as mock:
        out = await server._execute_tool("jsonata", {"query": "$", "bogus": 1})
        assert not mock.calls
    assert "Unknown parameter(s): bogus" in out
    await server.cleanup()


@respx.mock
async def test_server_reports_network_error():
    respx.get(url__startswith=BASE).mock(side_effect=httpx.ConnectError("refused"))
    server = make_server([JSONATA])
    out = await server._execute_tool("jsonata", {"query": "$"})
    assert "Tool execution failed" in out and "Cannot connect" in out
    await server.cleanup()


@respx.mock
async def test_health_probe():
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={}))
    server = make_server([JSONATA])
    assert await server.check_api_health() is True
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("down"))
    assert await server.check_api_health() is False
    await server.cleanup()


# --- SEAF specifics (found during hotel_booking integration run) -----------------

from seaf_mcp.response_formatter import format_error
from seaf_mcp.rest_client import APIResponse
from seaf_mcp.server import wrap_jsonata


def test_wrap_jsonata():
    assert wrap_jsonata("$count(x)") == "($count(x))"
    assert wrap_jsonata("  (true) ") == "((true))"
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert wrap_jsonata(uuid) == uuid


def test_jsonata_error_hides_stack():
    body = {"message": 'Expected ")" before end of expression',
            "error": {"code": "S0203", "position": 9, "stack": "Error\n at x"}}
    out = format_error("t", APIResponse(500, {}, body, error="raw"))
    assert 'Expected ")"' in out and "S0203" in out and "stack" not in out
    assert "Hint" not in out


@respx.mock
async def test_server_wraps_jsonata_in_body():
    ep = make_endpoint(id="q", method="POST", path="/api/jsonata",
                       bodyParams=[{"name": "query", "type": "string", "required": True, "description": ""}],
                       jsonataParams=["query"])
    route = respx.post(f"{BASE}/api/jsonata").mock(return_value=httpx.Response(200, json=1))
    server = make_server([ep])
    await server._execute_tool("q", {"query": "$count(x)"})
    assert route.calls.last.request.content == b'{"query":"($count(x))"}'
    await server.cleanup()
