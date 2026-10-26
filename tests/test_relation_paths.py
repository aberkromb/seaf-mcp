"""Tests for find_relation_paths."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.relation_paths import build_section_graph, find_paths
from seaf_mcp.seaf_tools import SeafTools

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def relmap(*edges):
    """(src, field, target) → relations map as extract_relations returns."""
    out: dict[str, list[dict[str, str]]] = {}
    for src, field, target in edges:
        out.setdefault(src, []).append({"field": field, "target": target})
    return out


S, P, C, F = "seaf.company.systems", "seaf.company.people", \
    "seaf.company.sys_components", "seaf.company.ba.functions"


# --- граф и поиск ----------------------------------------------------------

def test_direct_path():
    g = build_section_graph(relmap((S, "stakeholders.name", P)))
    paths, truncated = find_paths(g, S, P)
    assert not truncated and len(paths) == 1
    step = paths[0][0]
    assert step == {"from_section": S, "field": "stakeholders.name",
                    "to_section": P, "direction": "out"}


def test_reverse_direction():
    g = build_section_graph(relmap((S, "stakeholders.name", P)))
    paths, _ = find_paths(g, P, S)
    assert paths[0][0]["direction"] == "in"
    assert paths[0][0]["field"] == "stakeholders.name"


def test_two_equal_shortest_paths():
    g = build_section_graph(relmap((S, "owner", P), (S, "lead", P)))
    paths, truncated = find_paths(g, S, P)
    assert len(paths) == 2 and not truncated
    assert {p[0]["field"] for p in paths} == {"owner", "lead"}


def test_loop_not_repeated():
    g = build_section_graph(relmap((S, "is_part_of", S), (S, "owner", P), (P, "works_on", S)))
    paths, _ = find_paths(g, S, P)
    sections_used = [[st["to_section"] for st in p] for p in paths]
    for used in sections_used:
        assert len(used) == len(set(used))  # без повторов
    assert all(len(p) == 1 for p in paths)  # петли/циклы не удлиняют кратчайший


def test_cycle_excluded():
    g = build_section_graph(relmap((S, "a", P), (P, "b", C), (C, "c", S), (C, "d", F)))
    paths, _ = find_paths(g, S, F)
    # кратчайший: S -(in по c)-> C -(out по d)-> F; цикл S→P→C→S не вовлекается
    assert [[st["to_section"] for st in p] for p in paths] == [[C, F]]
    assert paths[0][0]["direction"] == "in"


def test_via_filter():
    g = build_section_graph(relmap((S, "owner", P), (P, "does", F), (S, "has", C), (C, "does", F)))
    paths, _ = find_paths(g, S, F, via=[C])
    assert [[st["to_section"] for st in p] for p in paths] == [[C, F]]


def test_via_does_not_filter_endpoints():
    g = build_section_graph(relmap((S, "owner", P)))
    paths, _ = find_paths(g, S, P, via=[C])
    assert len(paths) == 1  # start/end не фильтруются


def test_no_path():
    g = build_section_graph(relmap((S, "owner", P), (C, "x", F)))
    paths, truncated = find_paths(g, S, F)
    assert paths == [] and not truncated


def test_max_steps():
    edges = [(f"s{i}", "f", f"s{i+1}") for i in range(7)]
    g = build_section_graph(relmap(*edges))
    paths, _ = find_paths(g, "s0", "s6")   # 6 шагов > лимита 5
    assert paths == []
    paths, _ = find_paths(g, "s0", "s5")   # ровно 5
    assert len(paths) == 1


def test_max_ten_routes():
    edges = [(S, f"f{i}", P) for i in range(15)]
    g = build_section_graph(relmap(*edges))
    paths, truncated = find_paths(g, S, P)
    assert len(paths) == 10 and truncated


def test_exponential_graph_stops():
    # решётка 2×5: 2^5 = 32 кратчайших пути — перебор останавливается на 10
    edges = []
    for i in range(5):
        edges += [(f"a{i}", "x", f"a{i+1}"), (f"a{i}", "y", f"b{i+1}"),
                  (f"b{i}", "x", f"a{i+1}"), (f"b{i}", "y", f"b{i+1}")]
    g = build_section_graph(relmap(*edges))
    started = time.monotonic()
    paths, truncated = find_paths(g, "a0", "a5")
    assert time.monotonic() - started < 2
    assert 0 < len(paths) <= 10 and truncated


# --- через handler -----------------------------------------------------------

@pytest.fixture
def tools():
    async def run_query(q):
        return None
    t = SeafTools(run_query)
    t._relations = relmap((S, "stakeholders.name", P), (P, "responsible_for", F),
                          (C, "unrelated", "seaf.company.ba.products"))
    return t


async def test_handler_path(tools):
    out = await tools.find_relation_paths({"start_section": S, "end_section": F})
    assert out["count"] == 1
    assert out["paths"][0][0]["field"] == "stakeholders.name"
    assert "JOIN не выполняется" in out["note"]


async def test_handler_no_path_lists_neighbors(tools):
    out = await tools.find_relation_paths({"start_section": S, "end_section": C})
    assert out["paths"] == [] and out["start_neighbors"] == [P]


async def test_handler_unknown_and_same_sections(tools):
    out = await tools.find_relation_paths({"start_section": "nope", "end_section": P})
    assert "не найден" in out["error"] and P in out["known_sections"]
    out = await tools.find_relation_paths({"start_section": S, "end_section": S})
    assert "совпадают" in out["error"]
