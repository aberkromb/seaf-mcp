"""Unit tests for server-side list_entities."""

import json
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.seaf_tools import SeafTools, build_list_entities_query, lit
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_server() -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


# --- структура запроса ---------------------------------------------------------

def test_query_page_window_and_lookup():
    q = build_list_entities_query("seaf.company.systems", [], ["title"], 20, 50)
    assert "$page := $matched[[20..69]]" in q
    assert '$o := $lookup($,"seaf.company.systems")' in q
    assert '"total": $count($matched)' in q


def test_query_conditions():
    q = build_list_entities_query("x", [
        {"field": "stakeholders", "op": "missing"},
        {"field": "system", "value": "s1"},
        {"field": "title", "op": "contains", "value": "AbC"},
        {"field": "tags", "op": "exists"},
        {"field": "system", "op": "ne", "value": "s9"},
    ], ["title"], 0, 10)
    assert "$count($vs0) = 0" in q
    assert "$string($x) = \"s1\"" in q
    assert '$contains($lowercase($string($x)), "abc")' in q
    assert "$count($vs3) > 0" in q
    assert "= 0) and" in q or "= 0)" in q  # ne инвертирован


def test_query_values_embedded_as_literals():
    evil = 'x"); $boom('
    q = build_list_entities_query("x", [{"field": "note", "value": evil}], ["title"], 0, 10)
    assert lit(evil) in q and "$boom" not in q.replace(lit(evil), "")


def test_query_unknown_op_raises():
    with pytest.raises(ValueError, match="Unknown op"):
        build_list_entities_query("x", [{"field": "a", "op": "regex"}], ["t"], 0, 10)


def test_query_unknown_section_branch():
    q = build_list_entities_query("nope", [], ["title"], 0, 10)
    assert "not found. Use list_sections." in q


# --- сквозной путь через сервер --------------------------------------------------

@respx.mock
async def test_single_call_page_of_50():
    items = [{"id": f"e{i}", "title": f"T{i}"} for i in range(50)]
    route = respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json={
            "section": "seaf.company.systems", "total": 945, "offset": 0, "items": items}))
    server = make_server()
    out = await server._execute_tool("list_entities", {"section": "seaf.company.systems"})
    assert len(route.calls) == 1  # один запрос, раздел целиком не выгружается
    q = json.loads(route.calls[0].request.content)["query"]
    assert "$page := $matched[[0..49]]" in q
    data = json.loads(out)
    assert len(data["items"]) <= 50 and data["total"] == 945
    await server.cleanup()


@respx.mock
async def test_empty_page():
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json={
            "section": "x", "total": 5, "offset": 50, "items": []}))
    server = make_server()
    out = await server._execute_tool("list_entities", {"section": "x", "offset": 50})
    data = json.loads(out)
    assert data["items"] == [] and data["total"] == 5
    await server.cleanup()


@respx.mock
async def test_unknown_section_message():
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json={
            "error": "Section 'nope' not found. Use list_sections."}))
    server = make_server()
    out = await server._execute_tool("list_entities", {"section": "nope"})
    assert "Section 'nope' not found. Use list_sections." in out
    await server.cleanup()


async def test_special_chars_in_value_and_field():
    seen = []

    async def portal(q):
        seen.append(q)
        return {"section": "x", "total": 0, "offset": 0, "items": []}

    await SeafTools(portal).list_entities({
        "section": "x",
        "where": [{"field": 'weird "field"', "value": 'v"\\alue\nперевод'}],
        "fields": ['tit"le'],
    })
    q = seen[0]
    assert '\\"' in q  # кавычки экранированы как JSON-литералы
    assert "v\\" in q
