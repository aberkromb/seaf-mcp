"""Integration tests against any deployed SEAF portal.

Skipped unless SEAF_CORP_IT_URL is set, e.g.:
    SEAF_CORP_IT_URL=https://seaf-portal.example.com .venv/bin/pytest tests/test_integration_corp.py
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import load_catalog
from seaf_mcp.server import SeafMCPServer

URL = os.environ.get("SEAF_CORP_IT_URL")
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not URL, reason="SEAF_CORP_IT_URL not set"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def server():
    s = SeafMCPServer(
        Config(api_base_url=URL, request_timeout=60, max_response_chars=50000),
        load_catalog(CATALOG),
    )
    yield s
    await s.cleanup()


async def test_get_dataset_people(server):
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.people"})
    assert "example.person." in out
    assert '"role"' in out and '"department"' in out
    assert "error" not in out[:200]


async def test_get_dataset_unknown_id(server):
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.no_such_dataset_x9"})
    assert "seaf.company.ds.no_such_dataset_x9" in out
    assert "HTTP 200" in out and "stack" not in out


async def test_get_dataset_service_rejected(server):
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.ds.objects"})
    assert "allowlist" in out


async def test_query_dataset_short_selection(server):
    out = await server._execute_tool("query_dataset", {
        "origin": "seaf.company.ds.people",
        "source": "$keys($)[[0..2]]",
    })
    assert "example.person." in out and len(out) < 1000
    assert out.strip().startswith("[")


async def test_query_dataset_composite_origin(server):
    out = await server._execute_tool("query_dataset", {
        "origin": {"people": "seaf.company.ds.people"},
        "source": "$count($keys($.\"people\")) > 0",
    })
    assert out.strip() == "true"


async def test_query_dataset_count_service_dataset(server):
    out = await server._execute_tool("query_dataset", {
        "origin": "seaf.ds.objects",
        "source": "$count($keys($))",
    })
    assert int(out.strip()) > 1000


async def test_query_dataset_undefined(server):
    out = await server._execute_tool("query_dataset", {
        "origin": "seaf.company.ds.people",
        "source": "$lookup($, \"no_such_key_x9\")",
    })
    assert "HTTP 200" in out and "empty body" in out and "stack" not in out


async def test_describe_query_result_array(server):
    out = await server._execute_tool(
        "describe_query_result", {"query": '$keys($."seaf.company.people")'})
    assert '"result_type": "array"' in out and '"analyzed": 100' in out
    assert "example.person." in out


async def test_describe_query_result_object_fields(server):
    out = await server._execute_tool(
        "describe_query_result", {"query": '$."seaf.company.people"'})
    assert '"result_type": "object"' in out
    assert '"field": "title"' in out and "present_in" in out


async def test_describe_query_result_scalar(server):
    out = await server._execute_tool(
        "describe_query_result", {"query": '$count($keys($."seaf.company.systems"))'})
    assert '"result_type": "number"' in out and '"value": 945' in out


async def test_describe_query_result_missing(server):
    out = await server._execute_tool(
        "describe_query_result", {"query": '$lookup($, "no.such.section")'})
    assert '"result_type": "missing"' in out and "returned nothing" in out


async def test_describe_query_result_error_hint(server):
    out = await server._execute_tool(
        "describe_query_result", {"query": "$nosuchfn(1)"})
    assert "Status: 500" in out and "Hint:" in out and "$nosuchfn" in out


async def test_inspect_sections_real(server):
    out = await server._execute_tool("inspect_sections", {
        "sections": ["seaf.company.systems", "seaf.company.people"], "sample_limit": 2})
    assert '"count": 945' in out
    assert '"samples"' in out
    assert '"section": "seaf.company.people"' in out and '"title"' in out
    assert len(out) < 50000


async def test_inspect_sections_unknown_inline(server):
    out = await server._execute_tool("inspect_sections", {
        "sections": ["seaf.company.people", "seaf.company.no_such_x9"]})
    assert '"samples"' in out
    assert "not found" in out


# --- list_entities server-side (009) -------------------------------------------

GOLDEN_DIVERGENCES = {
    # задокументированные расхождения со старой клиентской семантикой (bool/float)
    "eq_bool_python_style": [],
    "eq_bool_json_style": ["a"],
    "eq_number": ["a", "b"],   # $string(5.0) = "5"
    "multi_and": ["b"],        # active=false как "false"
}


async def test_list_entities_golden_table(server):
    import json as jsonlib
    from seaf_mcp.seaf_tools import build_list_entities_query, lit
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "list_entities_golden.json"),
              encoding="utf-8") as f:
        golden = jsonlib.load(f)
    for case in golden["cases"]:
        q = build_list_entities_query("test", case["where"], ["title"], 0, 50,
                                      obj_expr=lit(golden["entities"]))
        result = await server.run_jsonata(q)
        ids = [i["id"] for i in result["items"]]
        expected = GOLDEN_DIVERGENCES.get(case["name"], case["expected_ids"])
        assert ids == expected, f"{case['name']}: {ids} != {expected}"


async def test_list_entities_pagination_large_section(server):
    async def page(offset):
        out = await server._execute_tool("list_entities", {
            "section": "seaf.company.systems", "limit": 100, "offset": offset})
        return json.loads(out)

    p0 = await page(0)
    assert p0["total"] == 945 and len(p0["items"]) == 100
    p1 = await page(100)
    assert len(p1["items"]) == 100
    ids0 = {i["id"] for i in p0["items"]}
    ids1 = {i["id"] for i in p1["items"]}
    assert not ids0 & ids1  # страницы не пересекаются
    last = await page(940)
    assert len(last["items"]) == 5


async def test_list_entities_server_side_filter(server):
    out = await server._execute_tool("list_entities", {
        "section": "seaf.company.systems",
        "where": [{"field": "stakeholders", "op": "missing"}], "limit": 5})
    data = json.loads(out)
    assert data["total"] >= 0 and len(data["items"]) <= 5


async def test_cookbook_expressions_run_on_portal(server):
    """Every non-template cookbook entry must stay valid."""
    from seaf_mcp.seaf_tools import load_cookbook
    for entry in load_cookbook():
        if entry.get("template"):
            continue
        out = await server._execute_tool(
            "seaf_jsonata_query", {"query": entry["expression"]})
        assert "error" not in out.lower()[:200], f"{entry['id']}: {out[:200]}"


async def test_find_jsonata_examples_tool(server):
    out = await server._execute_tool(
        "find_jsonata_examples", {"queries": ["системы", "стейкхолдеры"], "limit": 3})
    data = json.loads(out)
    assert data["returned"] == 3
    assert any(e["id"] == "relations.system_stakeholders" for e in data["examples"])


async def test_find_relation_paths_real(server):
    out = await server._execute_tool("find_relation_paths", {
        "start_section": "seaf.company.people", "end_section": "seaf.company.systems"})
    data = json.loads(out)
    assert data["count"] >= 1
    step = data["paths"][0][0]
    assert step["direction"] == "in" and step["field"]
    out = await server._execute_tool("find_relation_paths", {
        "start_section": "seaf.company.people",
        "end_section": "seaf.company.people"})
    assert "совпадают" in json.loads(out)["error"]
