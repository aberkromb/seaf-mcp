"""Tests for the JSONL query log."""

import asyncio
import json
import logging
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.endpoint_catalog import EndpointCatalog, load_catalog
from seaf_mcp.query_log import QueryLog
from seaf_mcp.server import SeafMCPServer

BASE = "http://seaf.test"
CATALOG = os.path.join(os.path.dirname(__file__), "..", "endpoints.json")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_server(log_file="", include_query=False, max_bytes=10_000_000) -> SeafMCPServer:
    config = Config(api_base_url=BASE, max_response_chars=50000, health_check_path="/h",
                    jsonata_log_file=log_file, jsonata_log_include_query=include_query,
                    jsonata_log_max_bytes=max_bytes)
    return SeafMCPServer(config, load_catalog(CATALOG))


def read_records(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- сам QueryLog ----------------------------------------------------------------

async def test_disabled_by_default(tmp_path):
    log = QueryLog("")
    assert not log.enabled
    await log.log("t", "$", "non_empty")
    assert list(tmp_path.iterdir()) == []


async def test_record_shape_and_no_query_by_default(tmp_path):
    log = QueryLog(str(tmp_path / "q.jsonl"))
    await log.log("seaf_jsonata_query", "$count($keys($))", "non_empty",
                  error_code=None, duration_ms=12)
    (rec,) = read_records(tmp_path / "q.jsonl")
    assert rec["tool"] == "seaf_jsonata_query"
    assert rec["status"] == "non_empty"
    assert rec["duration_ms"] == 12
    assert len(rec["query_sha256"]) == 64
    assert "query" not in rec
    assert {"ts", "tool", "status", "duration_ms", "query_sha256"} == set(rec)


async def test_include_query_only_when_explicit(tmp_path):
    log = QueryLog(str(tmp_path / "q.jsonl"), include_query=True)
    await log.log("query_dataset", "$keys($)", "empty")
    (rec,) = read_records(tmp_path / "q.jsonl")
    assert rec["query"] == "$keys($)"


async def test_error_code_recorded(tmp_path):
    log = QueryLog(str(tmp_path / "q.jsonl"))
    await log.log("t", "$x", "error", error_code="S0202")
    (rec,) = read_records(tmp_path / "q.jsonl")
    assert rec["error_code"] == "S0202"


async def test_concurrent_writes_intact(tmp_path):
    log = QueryLog(str(tmp_path / "q.jsonl"))
    await asyncio.gather(*(log.log("t", f"query-{i}", "non_empty") for i in range(50)))
    records = read_records(tmp_path / "q.jsonl")
    assert len(records) == 50
    assert len({r["query_sha256"] for r in records}) == 50


async def test_rotation(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text("x" * 1100)
    log = QueryLog(str(path), max_bytes=1000)
    await log.log("t", "$", "non_empty")
    assert (tmp_path / "q.jsonl.1").exists()
    assert read_records(path)[0]["status"] == "non_empty"


async def test_write_failure_warns_once(tmp_path, caplog):
    log = QueryLog(str(tmp_path / "no_such_dir" / "q.jsonl"))
    with caplog.at_level(logging.WARNING):
        await log.log("t", "$", "non_empty")
        await log.log("t", "$", "non_empty")
    warnings = [r for r in caplog.records if "Query log disabled" in r.message]
    assert len(warnings) == 1


# --- через сервер ------------------------------------------------------------------

@respx.mock
async def test_server_logs_three_statuses(tmp_path):
    log_path = str(tmp_path / "q.jsonl")
    route = respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata")
    route.mock(side_effect=[
        httpx.Response(200, json=115),                                     # non_empty
        httpx.Response(200, text=""),                                      # empty (undefined)
        httpx.Response(500, json={"message": "m", "error": {"code": "T1006",
                                                             "token": "take"}}),
    ])
    server = make_server(log_path)
    await server._execute_tool("seaf_jsonata_query", {"query": "$count($keys(x))"})
    await server._execute_tool("seaf_jsonata_query", {"query": '$lookup($, "no.such")'})
    await server._execute_tool("seaf_jsonata_query", {"query": "$unknownfn(1)"})
    await server.cleanup()

    records = read_records(log_path)
    assert [r["status"] for r in records] == ["non_empty", "empty", "error"]
    assert records[2]["error_code"] == "T1006"
    assert all("query" not in r for r in records)


@respx.mock
async def test_server_query_dataset_logged_without_params(tmp_path):
    log_path = str(tmp_path / "q.jsonl")
    respx.post(f"{BASE}/seaf-core/api/core/storage/release-data-profile").mock(
        return_value=httpx.Response(200, json={"a": 1}))
    server = make_server(log_path, include_query=True)
    await server._execute_tool("query_dataset", {
        "origin": "seaf.company.ds.people", "source": "$keys($)[[0..1]]",
        "params": {"secret_param": "s3cr3t"}})
    await server.cleanup()

    (rec,) = read_records(log_path)
    assert rec["tool"] == "query_dataset" and rec["status"] == "non_empty"
    assert rec["query"] == "$keys($)[[0..1]]"
    assert "s3cr3t" not in json.dumps(rec) and "params" not in rec


async def test_preflight_rejection_logged(tmp_path):
    log_path = str(tmp_path / "q.jsonl")
    server = make_server(log_path)
    out = await server._execute_tool("seaf_jsonata_query", {"query": "$"})
    assert "Query preflight" in out
    (rec,) = read_records(log_path)
    assert rec["status"] == "error" and "error_code" not in rec
    await server.cleanup()


@respx.mock
async def test_high_level_tools_not_logged(tmp_path):
    log_path = str(tmp_path / "q.jsonl")
    respx.post(f"{BASE}/seaf-core/api/core/storage/jsonata").mock(
        return_value=httpx.Response(200, json=[{"section": "s", "count": 1}]))
    server = make_server(log_path)
    await server._execute_tool("list_sections", {})
    await server.cleanup()
    assert not os.path.exists(log_path)
