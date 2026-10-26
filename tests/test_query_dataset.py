"""Unit tests for query_dataset tool."""

import json
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.seaf_tools import SeafTools
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
RDP_PATH = "/seaf-core/api/core/storage/release-data-profile"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "datasets")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_server() -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


# --- сериализация запроса ----------------------------------------------------

@respx.mock
async def test_string_origin_body_and_wrapping():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json=3))
    server = make_server()
    out = await server._execute_tool(
        "query_dataset", {"origin": "seaf.company.ds.people", "source": "$count($keys($))"})
    assert json.loads(route.calls.last.request.content) == {
        "query": {"origin": "seaf.company.ds.people", "source": "($count($keys($)))"}}
    assert out.strip() == "3"
    await server.cleanup()


@respx.mock
async def test_already_wrapped_source_double_wrapped():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json=1))
    server = make_server()
    source = "(\n$x := 1;\n$x;\n)"
    await server._execute_tool("query_dataset", {"origin": "seaf.company.ds.people", "source": source})
    assert json.loads(route.calls.last.request.content)["query"]["source"] == f"({source})"
    await server.cleanup()


@respx.mock
async def test_composite_origin_and_params():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json=123))
    server = make_server()
    await server._execute_tool("query_dataset", {
        "origin": {"people": "seaf.company.ds.people", "sys": "seaf.company.ds.systems"},
        "source": "$count($keys($.\"people\"))",
        "params": {"region": "nsk"},
    })
    assert json.loads(route.calls.last.request.content) == {
        "query": {"origin": {"people": "seaf.company.ds.people", "sys": "seaf.company.ds.systems"},
                  "source": "($count($keys($.\"people\")))"},
        "params": {"region": "nsk"}}
    await server.cleanup()


@respx.mock
async def test_service_dataset_allowed_with_filter():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json=14838))
    server = make_server()
    out = await server._execute_tool(
        "query_dataset", {"origin": "seaf.ds.objects", "source": "$count($keys($))"})
    assert out.strip() == "14838"
    await server.cleanup()


# --- валидация до HTTP --------------------------------------------------------

async def test_validation_errors_no_http():
    calls = []

    async def run_inline(origin, source, params=None):
        calls.append((origin, source))
        return 1

    tools = SeafTools(lambda q: None, None, run_inline)
    bad_args = [
        {"origin": "seaf.company.ds.people"},                        # нет source
        {"origin": "seaf.company.ds.people", "source": "   "},       # пустой source
        {"origin": "seaf.company.ds.people", "source": 42},          # source не строка
        {"origin": "", "source": "x"},                               # пустой origin
        {"origin": "bad id;", "source": "x"},                        # невалидный id
        {"origin": {}, "source": "x"},                               # пустая мапа
        {"origin": {"1bad": "seaf.company.ds.people"}, "source": "x"},   # алиас не идентификатор
        {"origin": {"ok": "bad id;"}, "source": "x"},                # значение алиаса невалидно
        {"origin": ["seaf.company.ds.people"], "source": "x"},       # origin массив
        {"origin": "seaf.company.ds.people", "source": "x", "params": "y"},  # params не объект
    ]
    for args in bad_args:
        out = await tools.query_dataset(args)
        assert "error" in out, args
    assert calls == []


@respx.mock
async def test_validation_via_server_no_http():
    with respx.mock(assert_all_called=False) as mock:
        server = make_server()
        out = await server._execute_tool(
            "query_dataset", {"origin": {"1bad": "seaf.company.ds.people"}, "source": "$"})
        assert not mock.calls
    assert "Invalid alias" in out
    await server.cleanup()


# --- ответы портала ------------------------------------------------------------

@respx.mock
async def test_undefined_result_message():
    respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, text=""))
    server = make_server()
    out = await server._execute_tool(
        "query_dataset", {"origin": "seaf.company.ds.people", "source": "$lookup($, \"nope\")"})
    assert "HTTP 200" in out and "empty body" in out and "stack" not in out
    await server.cleanup()


@respx.mock
async def test_portal_error_no_stack():
    body = json.load(open(os.path.join(FIXTURES, "error_source_no_parens.json"), encoding="utf-8"))["response"]
    respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(500, json=body))
    server = make_server()
    out = await server._execute_tool(
        "query_dataset", {"origin": "seaf.company.ds.people", "source": "$count($keys($))"})
    assert "Status: 500" in out and "Не найден источник данных" in out and "stack" not in out
    await server.cleanup()
