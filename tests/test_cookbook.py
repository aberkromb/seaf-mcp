"""Tests for the JSONata cookbook and find_jsonata_examples."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.seaf_tools import (SeafTools, _normalize_words, _word_matches,
                                 load_cookbook, search_cookbook)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tools():
    async def run_query(q):
        return None
    return SeafTools(run_query)


# --- файл -----------------------------------------------------------------

def test_cookbook_loads_via_importlib_resources():
    entries = load_cookbook()
    assert len(entries) >= 10
    for e in entries:
        assert {"id", "question", "group", "sections", "expression", "explanation"} <= set(e)
        assert e["expression"].startswith("(")
    assert len({e["id"] for e in entries}) == len(entries)
    assert any(e.get("template") for e in entries)


# --- нормализация -----------------------------------------------------------

def test_normalization():
    assert _normalize_words("Подсистемы Ёжика, v2!") == ["подсистемы", "ежика", "v2"]


@pytest.mark.parametrize("q,w,ok", [
    ("системы", "систем", True),      # словоформы по префиксу
    ("фискализация", "фискал", True),
    ("стейкхолдер", "стейкхолдеры", True),
    ("id", "id", True),               # короткое слово — полное совпадение
    ("id", "ids", False),             # короткое слово — префикс не считается
    ("продукт", "система", False),
])
def test_word_matching(q, w, ok):
    assert _word_matches(q, w) is ok


# --- ранжирование -----------------------------------------------------------

def test_exact_section_id_ranks_first():
    entries = load_cookbook()
    found = search_cookbook(entries, ["seaf.company.ba.products"], limit=5)
    assert found[0]["id"] == "products.find_by_title"


def test_russian_wordform_search():
    entries = load_cookbook()
    found = search_cookbook(entries, ["стейкхолдеры", "системы"], limit=5)
    ids = [e["id"] for e in found]
    assert "relations.system_stakeholders" in ids


def test_limit_and_distinct_words_ranking():
    entries = load_cookbook()
    found = search_cookbook(entries, ["люди", "подразделение", "посчитать"], limit=2)
    assert len(found) == 2
    assert found[0]["group"] == "people"


def test_no_match_and_empty():
    entries = load_cookbook()
    assert search_cookbook(entries, []) == []
    assert search_cookbook(entries, ["zxqwv"], limit=5) == []


# --- tool -----------------------------------------------------------------

async def test_tool_returns_examples(tools):
    out = await tools.find_jsonata_examples({"queries": ["системы"]})
    assert out["returned"] >= 1
    assert "expression" in out["examples"][0]


async def test_tool_limit_and_empty(tools):
    out = await tools.find_jsonata_examples({"queries": ["системы"], "limit": 1})
    assert out["returned"] == 1
    out = await tools.find_jsonata_examples({"queries": []})
    assert "hint" in out and out["examples"] == []
    out = await tools.find_jsonata_examples({"queries": ["zxqwv"]})
    assert "не найдены" in out["hint"]
