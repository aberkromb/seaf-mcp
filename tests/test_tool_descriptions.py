"""Tests for tool descriptions."""

import os
import sys

import pytest
from mcp.types import ListToolsRequest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import load_catalog
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

TRIGGER_MARKER = "Когда вызывать:"
NEXT_STEP_MARKER = "Дальше:"

PAIRS = [
    ("describe_section", "inspect_sections"),
    ("seaf_jsonata_query", "query_dataset"),
    ("list_entities", "find_entities"),
]

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def get_tools():
    server = SeafMCPServer(
        Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/h"),
        load_catalog(CATALOG))
    try:
        handler = server.server.request_handlers[ListToolsRequest]
        result = await handler(ListToolsRequest(method="tools/list"))
        return result.root.tools
    finally:
        await server.cleanup()


async def test_every_description_has_trigger_and_next_step():
    tools = await get_tools()
    assert tools
    for t in tools:
        assert TRIGGER_MARKER in t.description, t.name
        assert NEXT_STEP_MARKER in t.description, t.name


async def test_paired_tools_cross_reference():
    tools = await get_tools()
    desc = {t.name: t.description for t in tools}
    for a, b in PAIRS:
        assert b in desc[a], f"{a} не ссылается на {b}"
        assert a in desc[b], f"{b} не ссылается на {a}"


async def test_search_tools_entity_first():
    tools = await get_tools()
    desc = {t.name: t.description for t in tools}
    # в описаниях поиска сущности/разделы идут раньше имён функций JSONata
    for name in ("find_entities", "list_entities"):
        d = desc[name]
        first_section = min(d.find(x) for x in ("раздел", "система", "объект") if d.find(x) >= 0)
        for fn in ("$lookup", "$filter", "$sift"):
            assert fn not in d or d.find(fn) > first_section, name


async def test_schemas_unchanged():
    tools = await get_tools()
    for t in tools:
        assert t.inputSchema.get("additionalProperties") is False, t.name
