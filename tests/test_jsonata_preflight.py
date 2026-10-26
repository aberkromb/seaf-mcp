"""Unit tests for JSONata preflight checks."""

import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog, load_catalog
from seaf_mcp.jsonata_preflight import preflight, _mask_literals
from seaf_mcp.seaf_tools import SeafTools
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- голый $ ------------------------------------------------------------------

@pytest.mark.parametrize("expr", ["$", "  $  ", "$;", "($)", "(($);)", "( ($) ; )", "$$", "($$)"])
def test_bare_root_rejected(expr):
    hint = preflight(expr)
    assert hint and "whole manifest" in hint and "$keys($)" in hint


def test_bare_root_dataset_label():
    hint = preflight("$", root_label="the whole dataset (can be tens of MB)")
    assert "whole dataset" in hint


@pytest.mark.parametrize("expr", ["$.x", '$."seaf.company.systems"', "$keys($)", "$x", "$$x",
                                  "$map($keys($), function($k){$k})",
                                  "$count($keys($.\"seaf.company.people\"))"])
def test_bare_root_negatives(expr):
    assert preflight(expr) is None, expr


# --- агрегаты в цепочке ---------------------------------------------------------

def test_aggregate_chain_dollar():
    for fn in ["count", "sum", "average", "min", "max"]:
        hint = preflight(f"$keys($) ~> ${fn}($)")
        assert hint and "ROOT context" in hint, fn


@pytest.mark.parametrize("expr", ["$count($x)", "$keys($) ~> $count()", "$x ~> $sum()",
                                  '$m := "a ~> $count($)"; $m',
                                  "/* ~> $count($) */ $count($)"])
def test_aggregate_chain_negatives(expr):
    assert preflight(expr) is None, expr


# --- диапазоны и срезы ------------------------------------------------------------

@pytest.mark.parametrize("expr", ["$x[0..4]", "$k[ 0 .. 4 ]", '"hello"[1:3]', "$arr[1:3]",
                                  "$map($k, function($v){$v[0..2]})"])
def test_unsupported_range_slice(expr):
    hint = preflight(expr)
    assert hint and "x[[a..b]]" in hint, expr


@pytest.mark.parametrize("expr", ["[0..5]", "$k[[0..4]]", "$x[[ 0 .. 2 ]]",
                                  "$map([0..5], function($i){$i})",
                                  '$s := "[0..4]"; $s',  # строка
                                  "/* $x[0..4] */ $x[[0..4]]",  # комментарий
                                  '$x[[0..4]].{"a": $}',  # объект с ':' рядом
                                  "$k[$contains($, S)]".replace("S", '"x"'),
                                  '$m := $match("a", /[0..9]/); $m',  # regex-литерал
                                  ])
def test_allowed_forms(expr):
    assert preflight(expr) is None, expr


def test_backtick_and_index_filter():
    assert preflight("$.`seaf.company.systems`") is None
    assert preflight("$.`seaf.company.people`[[0..2]]") is None


# --- объект-литерал как тело function ----------------------------------------------

@pytest.mark.parametrize("expr", ['function($x){"k": v}',
                                   '$map(a, function($v){"k": $v})',
                                   '$each($p, function($v, $k){"key": $v})',
                                   '$filter($arr, function($v){"n": $v.x})'])
def test_function_body_object_rejected(expr):
    hint = preflight(expr)
    assert hint and "Object literal as a function body" in hint, expr
    assert "wrap in parentheses" in hint and "S0202" in hint


@pytest.mark.parametrize("expr", ['function($x){{"k": v}}',
                                   'function($x){( {"k": v} )}',
                                   '$map($a, function($x){{"k": $x}})',
                                   '$map($a, function($x){({"k": $x})})',
                                   '$s := "function($x){\"k\": v}"; $s',  # внутри строкового литерала
                                   '$sift($p, function($v){"str" in $v.arr})',  # тело начинается со строки, но не объект
                                   '$filter($a, function($v){"example" = $v.name})',
                                   'function($x){$x}'])
def test_function_body_object_allowed(expr):
    assert preflight(expr) is None, expr


# --- $lookup со строковым первым аргументом ---------------------------------------

def test_lookup_string_first_arg():
    hint = preflight('$lookup("seaf.company.systems", "x")')
    assert hint and "first argument must be the OBJECT" in hint


@pytest.mark.parametrize("expr", ['$lookup($, "seaf.company.systems")',
                                  '$x ~> $lookup("example.app_system.hotel_booking")',
                                  "$x ~> $lookup(  \"k\"  )",
                                  '$s := "$lookup(S, y)"'.replace("S", '"a"'),
                                  '$filter($k, function($v){$v ~> $lookup("k")})',
                                  ])
def test_lookup_allowed(expr):
    assert preflight(expr) is None, expr


# --- необъявленные кастомные функции ------------------------------------------------

@pytest.mark.parametrize("fn", ["values", "take", "slice", "group"])
def test_custom_functions(fn):
    hint = preflight(f"${fn}($x)")
    assert hint and f"${fn}" in hint, fn


@pytest.mark.parametrize("expr", ["($values := function($o){$each($o, function($v){$v})}; $values($x))",
                                  "($take := function($a, $n){$a[[0..$n-1]]}; $take($k, 2))",
                                  "$map($keys($), function($k){$k})",
                                  '$s := "$values($x)"; $s',  # в строке не считается
                                  ])
def test_custom_functions_negatives(expr):
    assert preflight(expr) is None, expr


# --- маскировка литералов -------------------------------------------------------------

def test_mask_literals():
    assert _mask_literals('$a := "x $lookup("; $a') == "$a := S; $a"
    assert _mask_literals("$contains(t, /l+/i)") == "$contains(t, R)"
    assert _mask_literals("$a / $b") == "$a / $b"  # деление не regex
    assert _mask_literals("$.`seaf.company.systems`") == "$.B"


def test_regex_does_not_hide_real_errors():
    assert preflight('$contains($string($x), /../) ~> $count($)') is not None


def test_comment_hides_errors():
    assert preflight("/* $x[0..4] ~> $count($) */ $keys($)") is None


# --- отказ одной подсказкой, без стека -----------------------------------------------

def test_single_hint():
    expr = '$lookup("seaf.company.systems", "x") ~> $count($)'
    hint = preflight(expr)
    assert isinstance(hint, str) and "stack" not in hint


# --- интеграция с сервером: HTTP не вызывается ----------------------------------------

@respx.mock
async def test_seaf_jsonata_query_rejected_no_http():
    with respx.mock(assert_all_called=False) as mock:
        config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
        server = SeafMCPServer(config, load_catalog(CATALOG))
        out = await server._execute_tool("seaf_jsonata_query", {"query": "$"})
        assert not mock.calls
    assert "Query preflight" in out and "whole manifest" in out
    await server.cleanup()


@respx.mock
async def test_seaf_jsonata_query_function_body_object_no_http():
    with respx.mock(assert_all_called=False) as mock:
        config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
        server = SeafMCPServer(config, load_catalog(CATALOG))
        out = await server._execute_tool(
            "seaf_jsonata_query",
            {"query": '$map($."seaf.company.people", function($sh){"role": $sh.role})'})
        assert not mock.calls
    assert "Query preflight" in out and "Object literal as a function body" in out
    await server.cleanup()


@respx.mock
async def test_seaf_jsonata_query_valid_passes():
    route = respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json=115))
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    server = SeafMCPServer(config, load_catalog(CATALOG))
    out = await server._execute_tool("seaf_jsonata_query",
                                     {"query": "$count($keys(`seaf.company.systems`))"})
    assert route.calls and "115" in out
    await server.cleanup()


async def test_query_dataset_preflight_no_http():
    calls = []

    async def run_inline(origin, source, params=None):
        calls.append(source)
        return 1

    tools = SeafTools(lambda q: None, None, run_inline)
    out = await tools.query_dataset({"origin": "seaf.company.ds.people", "source": "$take($, 2)"})
    assert "Query preflight" in out["error"] and "$take" in out["error"]
    assert calls == []


async def test_server_built_queries_not_checked():
    # внутренние запросы сервера проходят без preflight
    seen = []

    async def portal(q):
        seen.append(q)
        return []

    tools = SeafTools(portal)
    await tools.list_sections({})
    assert seen and all("$keys($)" in q or "$" in q for q in seen)
