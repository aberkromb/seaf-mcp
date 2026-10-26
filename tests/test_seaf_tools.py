"""Unit tests for high-level SEAF tools (no network)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.seaf_tools import SeafTools, extract_relations, lit, load_guide, outgoing_links, values_at

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


SCHEMA = {
    "$defs": {
        "stake": {"type": "object", "properties": {"stakeholders": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"$ref": "#/$rels/seaf.company.people.person"}}}}}},
    },
    "properties": {
        "seaf.company.sys_components": {"patternProperties": {"^.*$": {"allOf": [
            {"$ref": "#/$defs/stake"},
            {"properties": {"realizes_system": {"$ref": "#/$rels/seaf.company.systems.system"}},
             "$defs": {"junk": {"$ref": "#/$rels/x.y"}}},
        ]}}},
        "seaf.company.systems": {"patternProperties": {"^.*$": {"properties": {
            "integrations": {"patternProperties": {"^.*$": {"properties": {
                "service": {"$ref": "#/$rels/seaf.company.services.service"}}}}},
        }}}},
        "seaf.company.empty": {"type": "object"},
    },
}


def test_extract_relations():
    rel = extract_relations(SCHEMA)
    assert rel["seaf.company.sys_components"] == [
        {"field": "realizes_system", "target": "seaf.company.systems"},
        {"field": "stakeholders.name", "target": "seaf.company.people"},
    ]
    assert rel["seaf.company.systems"] == [{"field": "integrations.service", "target": "seaf.company.services"}]
    assert "seaf.company.empty" not in rel


def test_values_at_lists_and_maps():
    sys_obj = {"integrations": {"i001": {"service": "s1"}, "i002": {"service": "s2"}}}
    assert values_at(sys_obj, ["integrations", "service"]) == ["s1", "s2"]
    assert values_at({"stakeholders": [{"name": "p1"}, {"name": "p2"}]}, ["stakeholders", "name"]) == ["p1", "p2"]
    assert values_at({"c": ["a", "b"]}, ["c"]) == ["a", "b"]
    assert values_at({}, ["x"]) == []


def test_outgoing_links():
    rels = extract_relations(SCHEMA)["seaf.company.sys_components"]
    links = outgoing_links({"realizes_system": "sys1", "stakeholders": [{"name": "p"}]}, rels)
    assert {(l["field"], l["id"]) for l in links} == {("realizes_system", "sys1"), ("stakeholders.name", "p")}


def test_lit_escapes_injection():
    evil = 'x"); $boom('
    assert lit(evil) == '"x\\"); $boom("'
    assert json.loads(lit(evil)) == evil


def test_guide_packaged():
    assert "seaf.company.systems" in load_guide()


def test_guide_within_size_limit():
    # справочник отдаётся через truncate: не больше половины MAX_RESPONSE_CHARS (25 000)
    assert len(load_guide()) <= 25000


class FakePortal:
    """Answers queries by pattern; records them."""

    def __init__(self):
        self.queries = []

    async def __call__(self, q):
        self.queries.append(q)
        if q == "$manifestschema()":
            return SCHEMA
        if q.startswith("$id:=") and "$merge" in q:
            return {"seaf.company.sys_components": {"id": "c1", "v": {"title": "C1", "realizes_system": "sys1"}},
                    "seaf.company.systems": [{"id": "sys2", "v": {"title": "other", "x": "sys10"}}]}
        if q.startswith("$ids:="):
            return {"srv": "Service"}
        if q.startswith("$id:="):
            return {"section": "seaf.company.systems", "entity": {"title": "S1", "integrations": {"i1": {"service": "srv"}}}}
        raise AssertionError(q)


async def test_get_relations():
    portal = FakePortal()
    out = await SeafTools(portal).get_relations({"id": "sys1"})
    assert out["outgoing"] == [{"field": "integrations.service", "target_section": "seaf.company.services",
                                 "items": [{"id": "srv", "title": "Service"}], "count": 1}]
    # sys2 only contains the id as a substring of another value -> excluded
    assert out["incoming"] == [{"field": "realizes_system", "section": "seaf.company.sys_components",
                                "items": [{"id": "c1", "title": "C1"}], "count": 1}]


async def test_relations_cached():
    portal = FakePortal()
    tools = SeafTools(portal)
    await tools.relations()
    await tools.relations()
    assert portal.queries.count("$manifestschema()") == 1


async def test_get_relations_filters_and_limit():
    out = await SeafTools(FakePortal()).get_relations({"id": "sys1", "direction": "in", "limit": 1})
    assert out["outgoing"] == [] and out["incoming"][0]["count"] == 1
    out = await SeafTools(FakePortal()).get_relations({"id": "sys1", "field": "nope"})
    assert out["outgoing"] == [] and out["incoming"] == []


async def test_list_entities():
    seen = []

    async def portal(q):
        seen.append(q)
        return {"section": "x", "total": 2, "offset": 0,
                "items": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}

    t = SeafTools(portal)
    r = await t.list_entities({"section": "x", "where": [{"field": "system", "value": "s1"}]})
    assert r["total"] == 2 and [i["id"] for i in r["items"]] == ["a", "b"]
    # запрос выполняет фильтрацию и страницу на портале, не выгружая раздел
    assert "$page := $matched[[0..49]]" in seen[0]
    assert "$matched := $filter(" in seen[0]


async def test_list_entities_page_window():
    seen = []

    async def portal(q):
        seen.append(q)
        return {"section": "x", "total": 100, "offset": 40, "items": []}

    t = SeafTools(portal)
    r = await t.list_entities({"section": "x", "offset": 40, "limit": 20})
    assert "$page := $matched[[40..59]]" in seen[0]
    assert r == {"section": "x", "total": 100, "offset": 40, "items": []}
