"""Integration tests against a running SEAF portal with the Hotel Booking example manifest.

Skipped unless SEAF_IT_URL is set, e.g.:
    SEAF_IT_URL=http://localhost:8080 .venv/bin/pytest tests/test_integration_hotel_booking.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import load_catalog
from seaf_mcp.server import SeafMCPServer

URL = os.environ.get("SEAF_IT_URL")
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not URL, reason="SEAF_IT_URL not set"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def server():
    s = SeafMCPServer(
        Config(api_base_url=URL, request_timeout=300, max_response_chars=50000),
        load_catalog(CATALOG),
    )
    yield s
    await s.cleanup()


async def q(server, query):
    return await server._execute_tool("seaf_jsonata_query", {"query": query})


async def test_health(server):
    assert "app is alive" in await server._execute_tool("get_health", {})


async def test_systems_count(server):
    out = await q(server, "$count($keys(`seaf.company.systems`))")
    assert out.rstrip().endswith("115")


async def test_lookup_hotel_booking(server):
    out = await q(server, '$lookup(`seaf.company.systems`, "example.app_system.hotel_booking").title')
    assert "АС Бронирование" in out


async def test_products(server):
    assert "example.product.hotel_booking.hotel" in await q(server, "$keys(`seaf.company.ba.products`)")


async def test_special_chars(server):
    assert "a/b?c#dx" in await q(server, '"a/b?c#d" & "x"')


async def test_syntax_error_readable(server):
    out = await q(server, "$foo(((")
    assert "Status: 500" in out and "S0203" in out and "stack" not in out


async def test_whole_manifest_rejected():
    config = Config(api_base_url=URL or "http://localhost:8080", request_timeout=300,
                    max_response_chars=50000)
    s = SeafMCPServer(config, load_catalog(CATALOG))
    try:
        out = await q(s, "$")
        assert "Query preflight" in out and "[truncated:" not in out
    finally:
        await s.cleanup()


async def test_big_response_truncated(server):
    out = await q(server, "$.`seaf.company.systems`")
    assert "[truncated:" in out and "Status:\n200" in out


async def test_problems(server):
    out = await server._execute_tool("get_seaf_core_api_core_storage_problems", {})
    assert "Status:\n200" in out


async def test_list_sections(server):
    out = await server._execute_tool("list_sections", {})
    assert '"section": "seaf.company.systems"' in out and '"count": 115' in out


async def test_find_entities(server):
    out = await server._execute_tool("find_entities", {"text": "finance", "limit": 5})
    assert "example.app_system.hotel_booking.finance" in out


async def test_component_found_via_incoming(server):
    out = await server._execute_tool("get_relations", {"id": "example.app_system.hotel_booking.finance"})
    assert "example.sys_component.hotel_booking.finance.daily_report.cron_job" in out
    assert "realizes_system" in out


async def test_product_relations(server):
    out = await server._execute_tool("get_relations", {"id": "example.product.hotel_booking.hotel"})
    assert "distributed_via" in out and "example.process.hotel_booking.hotel_connection" in out


async def test_describe_section(server):
    out = await server._execute_tool("describe_section", {"section": "seaf.company.systems"})
    assert '"title": 115' in out and "realizes_system" in out


async def test_list_entities_missing_stakeholders(server):
    out = await server._execute_tool("list_entities", {"section": "seaf.company.systems",
                                     "where": [{"field": "stakeholders", "op": "missing"}]})
    assert '"total": 14' in out


async def test_relations_limited_with_titles(server):
    out = await server._execute_tool("get_relations", {"id": "example.app_system.hotel_booking", "direction": "in", "field": "is_part_of", "limit": 100})
    assert '"count": 56' in out and len(out) < 20000
    out = await server._execute_tool("get_relations", {"id": "example.product.hotel_booking.hotel"})
    assert "Веб-приложение Hotel Booking (B2C)" in out
