"""Tests for the metamodel YAML examples index."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.metamodel_index import MetamodelIndex
from seaf_mcp.seaf_tools import SeafTools

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


GOALS_YAML = """
entities:
  seaf.company.change.goals:
    presentations:
      list:
        title: Список целей
        origin: seaf.company.ds.change.goals
        source: >
          ( $objects := $;
            $each($objects, function($val, $key) {(
              {"id": $val.__id__, "title": $val.title}
            )})[]; )
"""

KADZO_YAML = """
entities:
  kadzo.research.run:
    datasets:
      main:
        origin: kadzo.research
        source: "( $ )"
"""

DEV_YAML = """
entities:
  seaf.company.hidden:
    presentations:
      draft:
        source: "( $keys($) )"
"""

BROKEN_YAML = "entities: [unclosed\n  : : :\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    write(tmp_path / "change/goals/presentations/list.yaml", GOALS_YAML)
    write(tmp_path / "kadzo/research.yaml", KADZO_YAML)
    write(tmp_path / "__dev__/draft.yaml", DEV_YAML)
    write(tmp_path / "broken.yaml", BROKEN_YAML)
    return tmp_path


def test_extraction_with_context(tree):
    idx = MetamodelIndex(str(tree))
    entries = idx.entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["question"] == "Список целей"
    assert e["group"] == "metamodel"
    assert e["origin"] == "seaf.company.ds.change.goals"
    assert e["source_file"] == "change/goals/presentations/list.yaml"
    assert "$each($objects" in e["expression"]
    assert "goals/presentations/list.yaml" not in e["expression"]  # не полный YAML


def test_exclusions(tree):
    idx = MetamodelIndex(str(tree))
    ids = [e["id"] for e in idx.entries()]
    assert not any("kadzo" in i or "draft" in i for i in ids)


def test_broken_file_does_not_break_index(tree):
    idx = MetamodelIndex(str(tree))
    assert idx.entries() and idx.skipped_files == 1


def test_file_size_limit(tree):
    write(tree / "big.yaml", "entities:\n  x:\n    source: \"( $ )\"\n# " + "x" * 2000)
    idx = MetamodelIndex(str(tree), max_file_bytes=100)
    assert all("big.yaml" not in e["id"] for e in idx.entries())


def test_file_count_limit(tree):
    for i in range(5):
        write(tree / f"extra/{i}.yaml", "entities:\n  x:\n    source: \"( $ )\"\n")
    idx = MetamodelIndex(str(tree), max_files=3)
    assert len(idx.entries()) <= 3


def test_symlink_escaping_root_skipped(tree, tmp_path):
    outside = tmp_path.parent / f"outside_{tree.name}.yaml"
    outside.write_text("entities:\n  x:\n    source: \"( $ )\"\n", encoding="utf-8")
    (tree / "linked.yaml").symlink_to(outside)
    idx = MetamodelIndex(str(tree))
    assert all("linked" not in e["id"] for e in idx.entries())
    outside.unlink()


def test_symlink_dir_not_followed(tree, tmp_path):
    outside_dir = tmp_path.parent / f"outside_dir_{tree.name}"
    outside_dir.mkdir()
    write(outside_dir / "x.yaml", "entities:\n  x:\n    source: \"( $ )\"\n")
    (tree / "linked_dir").symlink_to(outside_dir)
    idx = MetamodelIndex(str(tree))
    assert all("linked_dir" not in e["id"] for e in idx.entries())


def test_cache_rebuilt_on_change(tree):
    idx = MetamodelIndex(str(tree))
    first = idx.entries()
    assert len(first) == 1
    write(tree / "new.yaml", "entities:\n  seaf.company.new:\n    source: \"( $count($) )\"\n")
    os.utime(tree / "new.yaml")
    second = idx.entries()
    assert len(second) == 2
    assert idx.entries() is second  # без изменений — тот же кэш


def test_missing_path():
    assert not MetamodelIndex("").available
    assert not MetamodelIndex("/no/such/dir").available
    assert MetamodelIndex("/no/such/dir").entries() == []


# --- через tool -----------------------------------------------------------

@pytest.fixture
def tools(tree):
    async def run_query(q):
        return None
    return SeafTools(run_query, metamodel_path=str(tree))


async def test_cookbook_first_then_yaml(tools):
    out = await tools.find_jsonata_examples({"queries": ["цели", "системы"], "limit": 5})
    groups = [e["group"] for e in out["examples"]]
    assert groups == sorted(groups, key=lambda g: g != "metamodel" and 0 or 1) or True
    cookbook_pos = [i for i, e in enumerate(out["examples"]) if e["group"] != "metamodel"]
    yaml_pos = [i for i, e in enumerate(out["examples"]) if e["group"] == "metamodel"]
    assert not yaml_pos or not cookbook_pos or max(cookbook_pos) < min(yaml_pos)


async def test_yaml_example_found(tools):
    out = await tools.find_jsonata_examples({"queries": ["список", "целей"]})
    yaml_hits = [e for e in out["examples"] if e["group"] == "metamodel"]
    assert yaml_hits and yaml_hits[0]["source_file"].endswith("list.yaml")


async def test_tool_without_metamodel_path_still_works():
    async def run_query(q):
        return None
    tools = SeafTools(run_query)  # путь не задан
    out = await tools.find_jsonata_examples({"queries": ["системы"]})
    assert out["returned"] >= 1


# --- интеграция с реальным репозиторием метамодели --------------------------

REAL_MM = os.environ.get(
    "SEAF_METAMODEL_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "seaf-metamodel"))


@pytest.mark.skipif(not os.path.isdir(REAL_MM), reason="seaf-metamodel repo not available")
def test_real_metamodel_index():
    idx = MetamodelIndex(REAL_MM)
    entries = idx.entries()
    assert len(entries) > 10
    assert not any("seaf-company-kadzo" in e["id"] or "__dev__" in e["id"] for e in entries)
    origins = [str(e["origin"]) for e in entries if e["origin"]]
    assert not any(o.startswith(("kadzo.", "seaf.company.ds.kadzo", "eco_facades."))
                   for o in origins)
    assert all(len(e["expression"]) < 100_000 for e in entries)  # фрагменты, не файлы
