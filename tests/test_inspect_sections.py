"""Unit tests for inspect_sections."""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.seaf_tools import SeafTools, _shrink
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


SCHEMA = {
    "$defs": {},
    "properties": {
        "seaf.company.sys_components": {"patternProperties": {"^.*$": {"properties": {
            "realizes_system": {"$ref": "#/$rels/seaf.company.systems.system"}}}}},
        "seaf.company.systems": {"type": "object"},
    },
}


class FakePortal:
    """Отвечает describe/samples-запросам двух разделов; неизвестный — пусто."""

    def __init__(self, delay=0):
        self.queries = []
        self.delay = delay
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, q):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.queries.append(q)
        self.concurrent -= 1
        if q == "$manifestschema()":
            return SCHEMA
        if '"count":$count($ks)' in q:  # describe_section
            if '"seaf.company.unknown"' in q:
                return None
            return {"count": 2, "samples": ["id1", "id2"],
                    "attrs": ["title", "title", "description"]}
        if "$ks:=$keys($o)[[" in q:  # примеры через индексный фильтр
            return [{"title": "S1", "description": "d" * 400,
                     "integrations": {f"i{i:03}": {"service": "s"} for i in range(20)},
                     "tags": [f"t{i}" for i in range(9)]},
                    {"title": "S2"}]
        raise AssertionError(q)


async def test_two_sections():
    out = await SeafTools(FakePortal()).inspect_sections(
        {"sections": ["seaf.company.systems", "seaf.company.sys_components"]})
    secs = out["sections"]
    assert len(secs) == 2
    assert secs[0]["count"] == 2 and secs[0]["section"] == "seaf.company.systems"
    assert "samples" in secs[0] and len(secs[0]["samples"]) == 2
    assert secs[1]["outgoing_relation_fields"] == [
        {"field": "realizes_system", "target": "seaf.company.systems"}]
    assert secs[0]["incoming_relation_fields"] == [
        {"from_section": "seaf.company.sys_components", "field": "realizes_system"}]


async def test_unknown_section_inline_error():
    out = await SeafTools(FakePortal()).inspect_sections(
        {"sections": ["seaf.company.systems", "seaf.company.unknown"]})
    assert "samples" in out["sections"][0]
    assert "error" in out["sections"][1] and "not found" in out["sections"][1]["error"]


async def test_sample_limit_in_query():
    portal = FakePortal()
    await SeafTools(portal).inspect_sections(
        {"sections": ["seaf.company.systems"], "sample_limit": 3})
    assert any("$ks:=$keys($o)[[0..2]]" in q for q in portal.queries)


async def test_no_full_section_fetch():
    portal = FakePortal()
    await SeafTools(portal).inspect_sections({"sections": ["seaf.company.systems"]})
    for q in portal.queries:
        if q == "$manifestschema()":
            continue
        assert "$keys($o)" in q  # всегда через ключи/индексный фильтр, не раздел целиком


async def test_samples_shrunk():
    out = await SeafTools(FakePortal()).inspect_sections(
        {"sections": ["seaf.company.systems"]})
    sample = out["sections"][0]["samples"][0]
    assert sample["description"].endswith("[cut 100 chars]")
    assert sample["tags"][-1] == "…(+4 more)"
    assert sample["integrations"]["…"] == "+8 more keys"
    assert json.dumps(sample, ensure_ascii=False).__len__() < 2000


async def test_validation_no_portal_calls():
    portal = FakePortal()
    tools = SeafTools(portal)
    for bad in [{}, {"sections": []}, {"sections": [f"s{i}" for i in range(6)]},
                {"sections": ["a", "a"]}, {"sections": ["a", 1]},
                {"sections": ["a"], "sample_limit": 0},
                {"sections": ["a"], "sample_limit": 4},
                {"sections": ["a"], "sample_limit": "x"}]:
        out = await tools.inspect_sections(bad)
        assert "error" in out, bad
    assert portal.queries == []


async def test_concurrency_limited():
    portal = FakePortal(delay=0.05)
    await SeafTools(portal).inspect_sections({"sections": [f"s{i}" for i in range(5)]})
    assert portal.max_concurrent <= 3


# --- _shrink -------------------------------------------------------------------

def test_shrink_hard_cut():
    big = {f"k{i}": "v" * 100 for i in range(3)}  # все строки урежутся, но бюджет ещё меньше
    out = _shrink(big, budget=120)
    assert isinstance(out, str) and "…[cut: sample exceeded 120 chars]" in out
    assert len(out) <= 120 + 40  # бюджет + маркер


def test_shrink_small_untouched():
    obj = {"a": 1, "b": [1, 2], "c": "short"}
    assert _shrink(obj, 5000) == obj


# --- итоговый текст в пределах MAX_RESPONSE_CHARS ---------------------------------

async def test_final_text_within_limit():
    config = Config(api_base_url=BASE, max_response_chars=3000, health_check_path="/health")
    server = SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))
    server.seaf_tools = SeafTools(FakePortal(), max_chars=3000)
    server.seaf_defs = {d["name"]: d for d in server.seaf_tools.definitions()}
    out = await server._execute_tool(
        "inspect_sections", {"sections": ["seaf.company.systems", "seaf.company.sys_components"]})
    assert len(out) <= 3000
    assert "[truncated:" not in out or out.count("[truncated:") == 1
    await server.cleanup()
