"""Unit tests for JSONata error parsing and hints,."""

import json
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.jsonata_errors import (
    extract_error_info, hint_for, is_deterministic_error, MISSING_FUNCTION_HINTS)
from seaf_mcp.response_formatter import format_error, format_portal_error
from seaf_mcp.rest_client import APIResponse
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "jsonata")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def make_server() -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


# --- extract_error_info ------------------------------------------------------

def test_extract_s0201():
    body = load_fixture("error_s0201_semicolon.json")["response"]
    info = extract_error_info(body)
    assert info == {"code": "S0201", "token": ";", "position": 11,
                    "message": 'Syntax error: ";"'}


def test_extract_s0202_cases():
    cases = load_fixture("error_s0202_range_and_func_body.json")["cases"]
    range_info = extract_error_info(cases[0]["response"])
    func_info = extract_error_info(cases[1]["response"])
    assert (range_info["code"], range_info["token"]) == ("S0202", "..")
    assert (func_info["code"], func_info["token"]) == ("S0202", ";")


def test_extract_operators_and_t1006():
    ops = load_fixture("error_s0204_s0211_operators.json")["cases"]
    assert extract_error_info(ops[0]["response"])["code"] == "S0204"
    assert extract_error_info(ops[1]["response"])["code"] == "S0211"
    t1006 = extract_error_info(load_fixture("error_t1006_unknown_function.json")["response"])
    assert t1006["code"] == "T1006" and t1006["token"] == "take"


def test_extract_garbage_input():
    for body in [None, "", "text", 500, [], {"error": "boom"}, {"error": {"code": "bad"}}]:
        assert extract_error_info(body)["code"] is None


def test_is_deterministic():
    coded = load_fixture("error_t1006_unknown_function.json")["response"]
    assert is_deterministic_error(500, coded) is True
    assert is_deterministic_error(400, coded) is True
    assert is_deterministic_error(500, {"message": "x"}) is False
    assert is_deterministic_error(500, "<html>payload too large</html>") is False
    assert is_deterministic_error(200, coded) is False


# --- hint_for: ветки ----------------------------------------------------------

def test_hint_semicolon_variants():
    assert "inside a (...) block" in hint_for("S0201", ";")
    assert "function body" in hint_for("S0202", ";")


def test_hint_range_and_slice():
    assert "[[a..b]]" in hint_for("S0202", "..")
    assert "[[a..b]]" in hint_for("S0202", ":")
    # slice (Expected "]") — по message из тела ответа портала
    assert "[[a..b]]" in hint_for("S0202", ":", 'Expected "]", got ":"')


def test_hint_colon_object_constructor_body():
    # function($x){"k": v}: { — тело функции, парсер падает на ":" (Expected "}")
    hint = hint_for("S0202", ":", 'Expected "}", got ":"')
    assert hint and "wrapped in parentheses" in hint
    assert "function($x){( {" in hint and "} )}" in hint
    assert "function($x){{...}}" in hint
    assert "the body, not the object" in hint
    # без message консервативно — slice, не объект
    assert "[[a..b]]" in hint_for("S0202", ":")


def test_hint_operators():
    assert "'~'" in hint_for("S0204", "~")
    assert "'?.'" in hint_for("S0211", ".")


def test_hint_generic_syntax_not_overclaiming():
    hint = hint_for("S0203", None)
    assert "fix the expression" in hint
    assert "syntax" in hint.lower()
    # никакая подсказка не предлагает повторить детерминированный запрос
    assert "retr" not in hint.lower() or "will fail again" in hint


def test_hint_t1003():
    assert "string literals" in hint_for("T1003")


def test_hint_t1006_known_missing_functions():
    for token in ["take", "slice", "group", "values"]:
        hint = hint_for("T1006", token)
        assert f"${token}" in hint
        assert "does not exist" in hint
        assert MISSING_FUNCTION_HINTS[f"${token}"].split(" — ")[0] in hint


def test_hint_t1006_close_matches():
    hint = hint_for("T1006", "cont")
    assert "$cont" in hint
    assert "$count" in hint or "$contains" in hint


def test_hint_t1006_no_token():
    assert "does not exist" in hint_for("T1006", None)


def test_hint_t0410_chain_dollar():
    hint = hint_for("T0410")
    assert "ROOT context" in hint and "$count($x)" in hint


def test_hint_t2006():
    assert "must be a function" in hint_for("T2006")


def test_hint_runtime_suggests_describe():
    hint = hint_for("T0412")
    assert "describe_section" in hint and "describe_query_result" in hint


def test_hint_unknown_code_neutral():
    assert hint_for("X9999") is None
    assert hint_for(None) is None


# --- форматирование ------------------------------------------------------------

def test_format_error_hint_only_with_flag():
    body = load_fixture("error_t1006_unknown_function.json")["response"]
    resp = APIResponse(500, {}, body, error="raw")
    with_hint = format_error("seaf_jsonata_query", resp, jsonata_hint=True)
    without_hint = format_error("get_health", resp)
    assert "Hint:" in with_hint and "$take" in with_hint and "Status: 500" in with_hint
    assert "Hint:" not in without_hint
    assert "stack" not in with_hint and "value" not in with_hint


def test_format_error_no_stack_no_value_dump():
    body = {"message": "m", "error": {"code": "T0410", "stack": "Error\n at x",
                                      "value": {"huge": "context"}}}
    out = format_error("seaf_jsonata_query", APIResponse(500, {}, body, error="raw"),
                       jsonata_hint=True)
    assert "stack" not in out and "huge" not in out
    assert "ROOT context" in out


def test_format_portal_error_neutral_for_server_built():
    from seaf_mcp.errors import PortalError
    err = PortalError(500, "JSONata query failed: Expected \"]\", got \"..\"", code="S0202", token="..")
    out = format_portal_error("list_entities", err, agent_expression=False)
    assert "Status: 500" in out and "S0202" in out
    assert "built-in query" in out and "Hint:" not in out
    assert "before making the API request" not in out


# --- сквозной путь через сервер -------------------------------------------------

@respx.mock
async def test_query_dataset_error_has_hint():
    body = {"message": "Attempted to invoke a non-function",
            "error": {"code": "T1006", "position": 11, "token": "nosuchfn",
                      "message": "Attempted to invoke a non-function",
                      "stack": "Error\n at x"}}
    respx.post(f"{BASE}/seaf-core/api/core/storage/release-data-profile").mock(
        return_value=httpx.Response(500, json=body))
    server = make_server()
    out = await server._execute_tool(
        "query_dataset", {"origin": "seaf.company.ds.people", "source": "$nosuchfn(1)"})
    assert "Status: 500" in out and "Hint:" in out and "$nosuchfn" in out
    assert "does not exist" in out and "stack" not in out
    await server.cleanup()


@respx.mock
async def test_high_level_tool_error_is_neutral():
    # запрос строит сервер: синтаксических советов быть не должно
    body = load_fixture("error_s0202_range_and_func_body.json")["cases"][1]["response"]
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(500, json=body))
    server = make_server()
    out = await server._execute_tool("list_sections", {})
    assert "Status: 500" in out and "S0202" in out
    assert "built-in query" in out and "Hint:" not in out
    assert "before making the API request" not in out
    await server.cleanup()


@respx.mock
async def test_seaf_jsonata_query_object_body_hint():
    # function($sh){"role": ...} на живом портале (стейкхолдеры hotel_booking, 23.10.2026):
    # { читается как тело function, парсер падает на первом ":"
    body = {"message": 'Expected "}", got ":"',
            "error": {"code": "S0202", "position": 78, "token": ":", "value": "}",
                      "message": 'Expected "}", got ":"'}}
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(500, json=body))
    catalog = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")
    from seaf_mcp.endpoint_catalog import load_catalog
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    server = SeafMCPServer(config, load_catalog(catalog))
    out = await server._execute_tool(
        "seaf_jsonata_query",
        {"query": "$map($s.stakeholders, function($sh){`role`: $sh.role})"})
    assert "Status: 500" in out and "Hint:" in out
    assert "wrapped in parentheses" in out and "function($x){{...}}" in out
    assert "Slice" not in out and "stack" not in out
    await server.cleanup()


@respx.mock
async def test_seaf_jsonata_query_hint():
    ep_path = "/seaf-core/api/core/storage/jsonata"
    body = load_fixture("error_s0202_range_and_func_body.json")["cases"][1]["response"]
    respx.post(f"{BASE}{ep_path}").mock(return_value=httpx.Response(500, json=body))
    catalog = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")
    from seaf_mcp.endpoint_catalog import load_catalog
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/health")
    server = SeafMCPServer(config, load_catalog(catalog))
    out = await server._execute_tool(
        "seaf_jsonata_query", {"query": "$f := function($x){ $y := $x * 2; $y + 1 }; $f(3)"})
    assert "Status: 500" in out and "Hint:" in out and "function body" in out
    assert "stack" not in out
    await server.cleanup()
