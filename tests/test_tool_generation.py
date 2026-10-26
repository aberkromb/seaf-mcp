"""Tests for Stage 4: tool name generation and tool factory logic."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from seaf_mcp.endpoint_catalog import Endpoint
from seaf_mcp.tool_factory import (
    generate_tool_name,
    resolve_tool_name,
    create_mcp_tool_definition,
    filter_and_create_tools,
    should_include_endpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_endpoint(**kwargs) -> Endpoint:
    """Build a minimal Endpoint with sensible defaults."""
    defaults = dict(
        id=None,
        description="Test endpoint",
        method="GET",
        path="/api/test",
        pathParams=[],
        queryParams=[],
        bodyParams=[],
        readOnly=True,
        requestExample="curl http://localhost/api/test",
        responseDescription="Returns JSON",
    )
    defaults.update(kwargs)
    return Endpoint(**defaults)


# ---------------------------------------------------------------------------
# generate_tool_name
# ---------------------------------------------------------------------------

class TestGenerateToolName:
    def test_simple_get(self):
        assert generate_tool_name("GET", "/api/health") == "get_api_health"

    def test_post_method(self):
        assert generate_tool_name("POST", "/api/search") == "post_api_search"

    def test_nested_path(self):
        assert generate_tool_name("GET", "/seaf-core/api/core/storage/versions") \
            == "get_seaf_core_api_core_storage_versions"

    def test_path_with_colon_param(self):
        # :id should be treated as a regular segment separator
        assert generate_tool_name("GET", "/api/entities/:id/details") \
            == "get_api_entities_id_details"

    def test_hyphens_collapsed(self):
        assert generate_tool_name("GET", "/seaf-core/api/search-run") \
            == "get_seaf_core_api_search_run"

    def test_uppercase_method_normalized(self):
        result = generate_tool_name("POST", "/api/run")
        assert result.startswith("post_")

    def test_no_leading_trailing_underscore(self):
        name = generate_tool_name("GET", "/api/test")
        assert not name.startswith("_")
        assert not name.endswith("_")

    def test_no_consecutive_underscores(self):
        name = generate_tool_name("GET", "/api//double//slash")
        assert "__" not in name

    def test_matches_existing_catalog_style(self):
        # Verify output matches the naming convention used in endpoints.json
        assert generate_tool_name("GET", "/seaf-core/api/health") \
            == "get_seaf_core_api_health"


# ---------------------------------------------------------------------------
# resolve_tool_name
# ---------------------------------------------------------------------------

class TestResolveToolName:
    def test_uses_explicit_id_when_present(self):
        ep = make_endpoint(id="my_custom_tool", method="GET", path="/api/something")
        assert resolve_tool_name(ep) == "my_custom_tool"

    def test_generates_name_when_id_is_none(self):
        ep = make_endpoint(id=None, method="GET", path="/api/health")
        assert resolve_tool_name(ep) == "get_api_health"

    def test_generates_name_for_post(self):
        ep = make_endpoint(id=None, method="POST", path="/api/search/run", readOnly=True)
        assert resolve_tool_name(ep) == "post_api_search_run"


# ---------------------------------------------------------------------------
# should_include_endpoint
# ---------------------------------------------------------------------------

class TestShouldIncludeEndpoint:
    def test_get_is_allowed(self):
        ep = make_endpoint(method="GET")
        include, reason = should_include_endpoint(ep)
        assert include is True
        assert reason is None

    def test_head_is_allowed(self):
        ep = make_endpoint(method="HEAD")
        include, _ = should_include_endpoint(ep)
        assert include is True

    def test_options_is_allowed(self):
        ep = make_endpoint(method="OPTIONS")
        include, _ = should_include_endpoint(ep)
        assert include is True

    def test_post_readonly_allowed(self):
        ep = make_endpoint(method="POST", readOnly=True)
        include, reason = should_include_endpoint(ep)
        assert include is True

    def test_post_not_readonly_blocked(self):
        ep = make_endpoint(method="POST", readOnly=False)
        include, reason = should_include_endpoint(ep)
        assert include is False
        assert reason is not None

    def test_put_blocked(self):
        ep = make_endpoint(method="PUT")
        include, reason = should_include_endpoint(ep)
        assert include is False

    def test_delete_blocked(self):
        ep = make_endpoint(method="DELETE")
        include, reason = should_include_endpoint(ep)
        assert include is False

    def test_patch_blocked(self):
        ep = make_endpoint(method="PATCH")
        include, reason = should_include_endpoint(ep)
        assert include is False

    def test_wildcard_path_blocked(self):
        ep = make_endpoint(path="/api/storage/:hash/*")
        include, reason = should_include_endpoint(ep)
        assert include is False
        assert "wildcard" in reason.lower()

    def test_sse_blocked_by_response_description(self):
        ep = make_endpoint(responseDescription="Returns Server-Sent Events stream")
        include, reason = should_include_endpoint(ep)
        assert include is False

    def test_sse_blocked_by_content_type(self):
        ep = make_endpoint(responseDescription="Content-Type: text/event-stream")
        include, reason = should_include_endpoint(ep)
        assert include is False


# ---------------------------------------------------------------------------
# filter_and_create_tools
# ---------------------------------------------------------------------------

class TestFilterAndCreateTools:
    def _make_catalog(self):
        return [
            make_endpoint(id="explicit_id", method="GET", path="/api/one"),
            make_endpoint(id=None, method="GET", path="/api/two"),          # fallback name
            make_endpoint(id=None, method="POST", path="/api/write", readOnly=False),  # blocked
            make_endpoint(id=None, method="PUT", path="/api/put"),           # blocked
        ]

    def test_returns_three_tuple(self):
        tools, skipped, tool_map = filter_and_create_tools(self._make_catalog())
        assert isinstance(tools, list)
        assert isinstance(skipped, list)
        assert isinstance(tool_map, dict)

    def test_correct_counts(self):
        tools, skipped, tool_map = filter_and_create_tools(self._make_catalog())
        assert len(tools) == 2      # explicit_id + fallback GET
        assert len(skipped) == 2    # POST non-readonly + PUT

    def test_explicit_id_in_map(self):
        _, _, tool_map = filter_and_create_tools(self._make_catalog())
        assert "explicit_id" in tool_map

    def test_generated_name_in_map(self):
        _, _, tool_map = filter_and_create_tools(self._make_catalog())
        assert "get_api_two" in tool_map

    def test_tool_map_values_are_endpoints(self):
        _, _, tool_map = filter_and_create_tools(self._make_catalog())
        for name, ep in tool_map.items():
            assert isinstance(ep, Endpoint)

    def test_tool_names_match_tool_defs(self):
        tools, _, tool_map = filter_and_create_tools(self._make_catalog())
        tool_def_names = {t["name"] for t in tools}
        assert tool_def_names == set(tool_map.keys())

    def test_empty_catalog(self):
        tools, skipped, tool_map = filter_and_create_tools([])
        assert tools == []
        assert skipped == []
        assert tool_map == {}


# ---------------------------------------------------------------------------
# create_mcp_tool_definition
# ---------------------------------------------------------------------------

class TestCreateMcpToolDefinition:
    def test_uses_explicit_id(self):
        ep = make_endpoint(id="my_tool")
        defn = create_mcp_tool_definition(ep)
        assert defn["name"] == "my_tool"

    def test_uses_generated_name_when_no_id(self):
        ep = make_endpoint(id=None, method="GET", path="/api/status")
        defn = create_mcp_tool_definition(ep)
        assert defn["name"] == "get_api_status"

    def test_description_contains_endpoint_description(self):
        ep = make_endpoint(description="Check server health")
        defn = create_mcp_tool_definition(ep)
        assert "Check server health" in defn["description"]

    def test_description_contains_method_and_path(self):
        ep = make_endpoint(method="GET", path="/api/health")
        defn = create_mcp_tool_definition(ep)
        assert "GET" in defn["description"]
        assert "/api/health" in defn["description"]

    def test_has_input_schema(self):
        ep = make_endpoint()
        defn = create_mcp_tool_definition(ep)
        assert "inputSchema" in defn
        assert defn["inputSchema"]["type"] == "object"
