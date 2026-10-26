"""Unit tests for empty and large result presentation."""

import json
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog
from seaf_mcp.response_formatter import (
    EMPTY_RESULT_NOTE, describe_result_size, empty_result_note, format_success, truncate)
from seaf_mcp.rest_client import APIResponse
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_server(max_chars=50000) -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=max_chars, health_check_path="/health")
    return SeafMCPServer(config, EndpointCatalog(version="test", baseUrl=BASE, endpoints=[]))


# --- маркеры пустых форм --------------------------------------------------------

@pytest.mark.parametrize("body,form", [
    ("", "undefined"),      # пустое тело — JSONata undefined (форма из 002)
    (None, "null"),         # JSON null
    ([], "[]"),             # пустой массив
    ({}, "{}"),             # пустой объект
])
def test_empty_forms(body, form):
    note = empty_result_note(body)
    assert note and note.startswith(EMPTY_RESULT_NOTE) and form in note


@pytest.mark.parametrize("body", [False, 0, "x", 0.0, [0], {"a": 1}, "null"])
def test_non_empty_not_marked(body):
    assert empty_result_note(body) is None


def test_format_success_marks_empty():
    out = format_success("POST", "/x", APIResponse(200, {}, [], error=None))
    assert "Status:\n200" in out and EMPTY_RESULT_NOTE in out and "[]" in out


def test_format_success_false_not_marked():
    out = format_success("POST", "/x", APIResponse(200, {}, False, error=None))
    assert EMPTY_RESULT_NOTE not in out
    assert out.rstrip().endswith("false")  # JSON-форма, не Python "False"


# --- describe_result_size --------------------------------------------------------

def test_describe_array():
    d = describe_result_size([1, 2, 3])
    assert "array of 3 elements" in d and "[[a..b]]" in d and "$slice" not in d


def test_describe_object_keys_limited():
    d = describe_result_size({f"key{i:02}": i for i in range(25)})
    assert "object with 25 keys" in d and "(+15 more)" in d
    assert "key00" in d and "key24" not in d


def test_describe_scalar_none():
    assert describe_result_size(5) is None
    assert describe_result_size("text") is None


# --- лимит на весь текст ----------------------------------------------------------

def test_truncate_whole_text_contract():
    out = truncate("x" * 5000, 1000)
    assert len(out) <= 1000 and "[truncated: showing" in out


def test_truncate_details_instead_of_default():
    out = truncate("x" * 5000, 1000, details="the full result is an array of 7 elements; filter it")
    assert "array of 7 elements" in out and "narrow your query" not in out


def test_format_success_large_array():
    body = list(range(1000))
    out = format_success("POST", "/x", APIResponse(200, {}, body, error=None), max_chars=1000)
    assert len(out) <= 1000
    assert "[truncated:" in out and "array of 1000 elements" in out and "[[a..b]]" in out


def test_format_success_large_object():
    body = {f"key{i:03}": "v" * 50 for i in range(50)}
    out = format_success("POST", "/x", APIResponse(200, {}, body, error=None), max_chars=1000)
    assert len(out) <= 1000
    assert "object with 50 keys" in out and "key000" in out


def test_format_success_non_json_text():
    out = format_success("GET", "/x", APIResponse(200, {}, "y" * 5000, error=None), max_chars=1000)
    assert len(out) <= 1000 and "[truncated:" in out and "elements" not in out


def test_format_success_short_unchanged():
    resp = APIResponse(200, {}, {"message": "app is alive"}, error=None)
    out = format_success("GET", "/health", resp, max_chars=50000)
    assert out == ("Request:\nGET /health\n\nStatus:\n200\n\nResponse:\n"
                   '{\n  "message": "app is alive"\n}')


# --- сквозной путь ------------------------------------------------------------------

@respx.mock
async def test_seaf_tool_empty_array_marked():
    respx.post(f"{BASE}/seaf-core/api/core/storage/release-data-profile").mock(
        return_value=httpx.Response(200, json=[]))
    server = make_server()
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.people"})
    assert EMPTY_RESULT_NOTE in out and "[]" in out
    await server.cleanup()


@respx.mock
async def test_seaf_tool_large_result_note():
    big = {f"example.person.p{i:03}": {"title": "x" * 100} for i in range(200)}
    respx.post(f"{BASE}/seaf-core/api/core/storage/release-data-profile").mock(
        return_value=httpx.Response(200, json=big))
    server = make_server(max_chars=5000)
    out = await server._execute_tool("get_dataset", {"dataset_id": "seaf.company.ds.people"})
    assert len(out) <= 5000
    assert "[truncated:" in out and "object with 200 keys" in out
    assert "example.person.p000" in out
    await server.cleanup()
