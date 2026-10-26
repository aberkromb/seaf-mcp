"""Tests for the optional diagnostic file log."""

import logging
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seaf_mcp.config import Config
from seaf_mcp.rest_client import RestClient
from seaf_mcp.server import setup_file_logging

BASE = "http://seaf.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    original = root.handlers[:]
    yield
    for h in root.handlers[:]:
        if h not in original:
            root.removeHandler(h)
            h.close()


def test_writes_to_file(tmp_path, clean_root_logger):
    path = tmp_path / "diag" / "seaf-mcp.log"
    setup_file_logging(str(path))
    logging.getLogger("seaf_mcp.test").warning("hello-diagnostic")
    content = path.read_text()
    assert "hello-diagnostic" in content
    assert "WARNING" in content and "seaf_mcp.test" in content


def test_off_by_default(tmp_path, clean_root_logger):
    before = len(logging.getLogger().handlers)
    setup_file_logging("")
    assert len(logging.getLogger().handlers) == before
    assert list(tmp_path.iterdir()) == []


def test_bad_path_warns_and_continues(clean_root_logger, caplog):
    with caplog.at_level(logging.ERROR):
        setup_file_logging("/proc/seaf_mcp_no_such_dir/x.log")
    assert any("Cannot open SEAF_LOG_FILE" in r.message for r in caplog.records)


def test_rotation(tmp_path, clean_root_logger):
    path = tmp_path / "seaf-mcp.log"
    setup_file_logging(str(path), max_bytes=200, backup_count=3)
    log = logging.getLogger("seaf_mcp.test")
    for i in range(40):
        log.warning(f"line {i} " + "x" * 50)
    assert path.exists()
    assert (tmp_path / "seaf-mcp.log.1").exists()
    assert len(list(tmp_path.glob("seaf-mcp.log.*"))) <= 3


def test_no_handler_writes_to_stdout(tmp_path, clean_root_logger):
    setup_file_logging(str(tmp_path / "seaf-mcp.log"))
    for handler in logging.getLogger().handlers:
        stream = getattr(handler, "stream", None)
        assert stream is not sys.stdout


def test_config_env(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", BASE)
    assert Config().seaf_log_file == ""
    monkeypatch.setenv("SEAF_LOG_FILE", "/logs/x.log")
    assert Config().seaf_log_file == "/logs/x.log"


@respx.mock
async def test_debug_logs_have_no_request_values(caplog):
    respx.post(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    with caplog.at_level(logging.DEBUG, logger="seaf_mcp.rest_client"):
        async with RestClient(BASE) as client:
            await client.call("POST", "/x", body={"query": "SECRET_QUERY", "token": "S3CR3T"})
    messages = [r.message for r in caplog.records]
    assert any("Request body" in m for m in messages)
    assert all("SECRET_QUERY" not in m and "S3CR3T" not in m for m in messages)
