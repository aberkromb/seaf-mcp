"""Unit tests for get_dataset tool."""

import json
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.errors import RestClientError
from seaf_mcp.seaf_tools import SeafTools
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
RDP_PATH = "/seaf-core/api/core/storage/release-data-profile"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "datasets")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def make_server() -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


def people_response():
    return load_fixture("people_success.json")["response"]


# --- run_dataset: путь, тело, params ----------------------------------------

@respx.mock
async def test_run_dataset_path_and_body():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(
        return_value=httpx.Response(200, json=people_response())
    )
    server = make_server()
    result = await server.run_dataset("seaf.company.ds.people")
    assert isinstance(result, dict) and "example.person.example_one" in result
    assert json.loads(route.calls.last.request.content) == {"query": "/datasets/seaf.company.ds.people"}
    await server.cleanup()


@respx.mock
async def test_run_dataset_sends_params_only_when_present():
    route = respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json={}))
    server = make_server()
    await server.run_dataset("seaf.company.ds.people", {"region": "nsk"})
    assert json.loads(route.calls.last.request.content) == {
        "query": "/datasets/seaf.company.ds.people", "params": {"region": "nsk"}}
    await server.run_dataset("seaf.company.ds.people", None)
    assert "params" not in json.loads(route.calls.last.request.content)
    await server.cleanup()


@respx.mock
async def test_run_dataset_portal_error_raises():
    body = load_fixture("error_source_no_parens.json")["response"]
    respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(500, json=body))
    server = make_server()
    with pytest.raises(RestClientError, match="HTTP 500"):
        await server.run_dataset("seaf.company.ds.people")
    await server.cleanup()


# --- get_dataset: валидация до HTTP -----------------------------------------

async def test_invalid_id_no_http():
    calls = []

    async def run_dataset(dataset_id, params=None):
        calls.append(dataset_id)
        return {}

    tools = SeafTools(lambda q: None, run_dataset)
    for bad in ["seaf.company.ds.people; DROP", "../x", "with space", "кириллица",
                "a" * 201, "-leading-dash"]:
        out = await tools.get_dataset({"dataset_id": bad})
        assert "error" in out, bad
        assert "Invalid dataset_id" in out["error"]
    assert calls == []


async def test_allowlist_rejects_service_datasets_no_http():
    calls = []

    async def run_dataset(dataset_id, params=None):
        calls.append(dataset_id)
        return {}

    tools = SeafTools(lambda q: None, run_dataset)
    for ds in ["seaf.ds.objects", "seaf.toolkit.ds.objects", "seaf.ds.entities", "other.ds.x"]:
        out = await tools.get_dataset({"dataset_id": ds})
        assert "allowlist" in out["error"], ds
        assert "query_dataset" in out["error"]
    assert calls == []


async def test_params_must_be_object():
    async def run_dataset(dataset_id, params=None):
        return {}

    tools = SeafTools(lambda q: None, run_dataset)
    out = await tools.get_dataset({"dataset_id": "seaf.company.ds.people", "params": "x"})
    assert "Invalid params" in out["error"]


async def test_unknown_dataset_message():
    async def run_dataset(dataset_id, params=None):
        return None

    tools = SeafTools(lambda q: None, run_dataset)
    out = await tools.get_dataset({"dataset_id": "seaf.company.ds.nonexistent"})
    assert "seaf.company.ds.nonexistent" in out["error"]
    assert "HTTP 200" in out["error"]
    assert "stack" not in out["error"]


# --- сквозной путь через сервер ---------------------------------------------

@respx.mock
async def test_get_dataset_via_server():
    respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(200, json=people_response()))
    server = make_server()
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.people"})
    assert "example.person.example_one" in out
    await server.cleanup()


@respx.mock
async def test_get_dataset_invalid_id_no_http():
    with respx.mock(assert_all_called=False) as mock:
        server = make_server()
        out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.ds.objects"})
        assert not mock.calls
    assert "allowlist" in out
    await server.cleanup()


@respx.mock
async def test_get_dataset_portal_error_no_stack():
    body = load_fixture("error_jsonata_t1006.json")["response"]
    respx.post(f"{BASE}{RDP_PATH}").mock(return_value=httpx.Response(500, json=body))
    server = make_server()
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.people"})
    assert "Status: 500" in out and "Attempted to invoke a non-function" in out
    assert "stack" not in out
    await server.cleanup()
