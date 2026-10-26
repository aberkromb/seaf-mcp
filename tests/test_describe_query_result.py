"""Unit tests for describe_query_result."""

import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.describe_query import build_analyze_query
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.jsonata_preflight import _mask_literals
from seaf_mcp.seaf_tools import SeafTools
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_server(max_chars=50000) -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=max_chars, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


def assert_semicolons_inside_parens(expr: str):
    """Regression invariant (helper work_log 053): every ';' is inside ( ... )."""
    masked = _mask_literals(expr)
    depth = 0
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";":
            assert depth > 0, f"';' at depth 0, position {i}: {expr[max(0, i-40):i+40]}"


# --- build_analyze_query --------------------------------------------------------

def test_wraps_user_query_and_samples():
    meta = build_analyze_query("$keys($)")
    assert "$r := ($keys($));" in meta
    assert "[[0..99]]" in meta      # выборка первых 100
    assert "[[0..2]]" in meta       # до 3 примеров
    assert "1500" in meta           # предел объёма примера


def test_semicolon_invariant_simple():
    assert_semicolons_inside_parens(build_analyze_query("$count($keys($))"))


def test_semicolon_invariant_with_user_function():
    tricky = '$f := function($x){( $y := $x * 2; $y + 1 )}; $map([1,2], $f)'
    assert_semicolons_inside_parens(build_analyze_query(tricky))


def test_semicolon_invariant_with_strings_and_comments():
    tricky = '$s := "a;b"; /* ; */ $contains($s, ";")'
    assert_semicolons_inside_parens(build_analyze_query(tricky))


# --- handler --------------------------------------------------------------------

async def test_array_result_passthrough():
    seen = []

    async def portal(q):
        seen.append(q)
        return {"result_type": "array", "count": 140, "analyzed": 100,
                "fields": [], "examples": ["a", "b"], "value": None}

    out = await SeafTools(portal).describe_query_result({"query": "$keys($)"})
    assert out["result_type"] == "array" and out["count"] == 140
    assert "$r := ($keys($));" in seen[0]


async def test_object_of_objects_fields():
    async def portal(q):
        return {"result_type": "object", "count": 140, "analyzed": 100,
                "fields": [{"field": "title", "present_in": "100/100"}],
                "examples": [{"title": "x"}], "value": None}

    out = await SeafTools(portal).describe_query_result({"query": '$.\"seaf.company.people\"'})
    assert out["fields"][0]["present_in"] == "100/100"


async def test_scalar_aggregate_value():
    async def portal(q):
        return {"result_type": "number", "count": 0, "analyzed": 0,
                "fields": [], "examples": [], "value": 945}

    out = await SeafTools(portal).describe_query_result({"query": "$count($keys($))"})
    assert out["result_type"] == "number" and out["value"] == 945


async def test_missing_result_note():
    async def portal(q):
        return {"result_type": "missing", "count": 0, "analyzed": 0,
                "fields": [], "examples": [], "value": None}

    out = await SeafTools(portal).describe_query_result({"query": '$lookup($, "no.such")'})
    assert "returned nothing" in out["note"]


async def test_undefined_meta_result():
    async def portal(q):
        return None

    out = await SeafTools(portal).describe_query_result({"query": "$keys($)"})
    assert out["result_type"] == "missing" and "returned nothing" in out["note"]


async def test_preflight_rejects_bare_root():
    calls = []

    async def portal(q):
        calls.append(q)
        return {}

    out = await SeafTools(portal).describe_query_result({"query": "$"})
    assert "Query preflight" in out["error"] and calls == []


async def test_invalid_query_type():
    async def portal(q):
        return {}

    out = await SeafTools(portal).describe_query_result({"query": 42})
    assert "error" in out


# --- сквозной путь: ошибка портала с подсказкой (010) ------------------------------

@respx.mock
async def test_portal_error_has_hint():
    body = {"message": 'Expected "}", got ";"',
            "error": {"code": "S0202", "position": 34, "token": ";",
                      "message": 'Expected "}", got ";"', "stack": "Error\n at x"}}
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(500, json=body))
    server = make_server()
    out = await server._execute_tool("describe_query_result", {"query": "$keys($)"})
    assert "Status: 500" in out and "Hint:" in out and "function body" in out
    assert "stack" not in out
    await server.cleanup()


@respx.mock
async def test_via_server_success():
    payload = {"result_type": "array", "count": 3, "analyzed": 3,
               "fields": [], "examples": ["a", "b", "c"], "value": None}
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json=payload))
    server = make_server()
    out = await server._execute_tool("describe_query_result", {"query": "$keys($)"})
    assert '"result_type": "array"' in out and '"count": 3' in out
    await server.cleanup()
