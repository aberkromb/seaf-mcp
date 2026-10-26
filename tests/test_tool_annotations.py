"""Tests for tool annotations."""

import os
import sys

import pytest
from mcp.types import ListToolsRequest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog, load_catalog
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def get_tools(server):
    handler = server.server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    return result.root.tools


async def test_all_tools_annotated_read_only():
    server = SeafMCPServer(
        Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/h"),
        load_catalog(CATALOG))
    tools = await get_tools(server)
    assert tools, "no tools registered"
    for t in tools:
        a = t.annotations
        assert a is not None, t.name
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint, a.openWorldHint) \
            == (True, False, True, False), t.name
    await server.cleanup()


async def test_titles_present():
    server = SeafMCPServer(
        Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/h"),
        load_catalog(CATALOG))
    tools = await get_tools(server)
    by_name = {t.name: t for t in tools}
    # высокоуровневый tool
    assert by_name["list_sections"].title == "Разделы модели"
    assert by_name["describe_query_result"].title == "Описание результата запроса"
    # raw tool из каталога
    assert by_name["seaf_jsonata_query"].title == "Запрос JSONata"
    assert by_name["get_health"].title == "Проверка портала"
    await server.cleanup()


async def test_hidden_and_write_endpoints_not_published():
    server = SeafMCPServer(
        Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/h"),
        load_catalog(CATALOG))
    names = {t.name for t in await get_tools(server)}
    assert "get_seaf_core_api_core_storage_release_data_profile" not in names  # скрытый GET
    assert not any(n.startswith(("put_", "post_seaf_core_api_core_storage_reload")) for n in names)
    # существующие вызовы не меняют схемы аргументов
    for t in await get_tools(server):
        assert t.inputSchema.get("additionalProperties") is False, t.name
    await server.cleanup()
