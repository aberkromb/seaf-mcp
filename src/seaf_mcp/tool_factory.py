"""Tool factory - filters endpoints and generates MCP tools."""

import logging
import re
from typing import Any

from .endpoint_catalog import Endpoint, ParamModel
from .errors import EndpointNotAllowedError, SeafMCPError

logger = logging.getLogger(__name__)


def generate_tool_name(method: str, path: str) -> str:
    """Generate a tool name from HTTP method and URL path.

    Applies when endpoint has no explicit id.

    Algorithm:
    1. Prepend lowercased method
    2. Concatenate with path
    3. Replace any non-alphanumeric character with '_'
    4. Collapse consecutive underscores; strip leading/trailing underscores

    Examples:
        GET  /seaf-core/api/health          -> get_seaf_core_api_health
        POST /seaf-core/api/storage/search  -> post_seaf_core_api_storage_search
        GET  /api/entities/:id/details      -> get_api_entities_id_details

    Args:
        method: HTTP method (e.g. "GET", "POST")
        path:   URL path (e.g. "/seaf-core/api/health")

    Returns:
        Snake_case tool name string
    """
    raw = f"{method.lower()}_{path}"
    # Replace every run of non-alphanumeric characters with a single underscore
    name = re.sub(r"[^a-zA-Z0-9]+", "_", raw)
    # Strip leading/trailing underscores
    name = name.strip("_")
    return name


def resolve_tool_name(endpoint: Endpoint) -> str:
    """Return the tool name for an endpoint.

    Uses endpoint.id when present; falls back to generate_tool_name().

    Args:
        endpoint: Endpoint specification

    Returns:
        Tool name string
    """
    if endpoint.id:
        return endpoint.id
    return generate_tool_name(endpoint.method, endpoint.path)


def should_include_endpoint(endpoint: Endpoint) -> tuple[bool, str | None]:
    """Check if endpoint should be included as MCP tool.
    
    Filtering rules (from README):
    1. Method filtering:
       - GET, HEAD, OPTIONS: always allowed
       - POST: only if readOnly=true
       - PUT, DELETE, PATCH: never allowed
    
    2. Path filtering:
       - Wildcard paths (/*): not supported in MVP
    
    3. Response type filtering:
       - SSE endpoints: not supported in MVP
    
    Args:
        endpoint: Endpoint to check
    
    Returns:
        Tuple of (include: bool, reason: str | None)
        If include=False, reason explains why
    """
    
    if endpoint.hidden:
        return False, "hidden in catalog"

    # Rule 1: Method filtering
    if endpoint.method.upper() in ["PUT", "DELETE", "PATCH"]:
        return False, f"Method {endpoint.method} not allowed (write operation)"
    
    if endpoint.method.upper() == "POST" and not endpoint.readOnly:
        return False, "POST without readOnly=true not allowed"
    
    # Rule 2: Wildcard paths
    if "/*" in endpoint.path:
        return False, "Wildcard paths (/*) not supported in MVP"
    
    # Rule 3: SSE endpoints
    if "Server-Sent Events" in endpoint.responseDescription or \
       "text/event-stream" in endpoint.responseDescription.lower():
        return False, "SSE endpoints not supported in MVP"
    
    return True, None


def _param_to_json_schema(param: ParamModel) -> dict[str, Any]:
    """Convert endpoint parameter to JSON Schema property.
    
    Args:
        param: Parameter from endpoint catalog
    
    Returns:
        JSON Schema property definition
    """
    # Map catalog types to JSON Schema types
    type_mapping = {
        "string": "string",
        "number": "number",
        "integer": "integer",
        "boolean": "boolean",
        "object": "object",
        "array": "array",
    }
    
    json_type = type_mapping.get(param.type.lower(), "string")
    
    return {
        "type": json_type,
        "description": param.description,
    }


def create_tool_input_schema(endpoint: Endpoint) -> dict[str, Any]:
    """Generate JSON Schema for tool input parameters.
    
    Combines pathParams, queryParams, and bodyParams into single schema.
    
    Args:
        endpoint: Endpoint specification
    
    Returns:
        JSON Schema for tool input
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    
    # Add path parameters
    for param in endpoint.pathParams:
        properties[param.name] = _param_to_json_schema(param)
        if param.required:
            required.append(param.name)
    
    # Add query parameters
    for param in endpoint.queryParams:
        properties[param.name] = _param_to_json_schema(param)
        if param.required:
            required.append(param.name)
    
    # Add body parameters
    for param in endpoint.bodyParams:
        properties[param.name] = _param_to_json_schema(param)
        if param.required:
            required.append(param.name)
    
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    
    if required:
        schema["required"] = required
    
    return schema


def _check_param_collisions(tool_name: str, endpoint: Endpoint) -> None:
    """Raise if the same parameter name appears in more than one location."""
    seen: dict[str, str] = {}
    for location, params in (
        ("path", endpoint.pathParams),
        ("query", endpoint.queryParams),
        ("body", endpoint.bodyParams),
    ):
        for param in params:
            if param.name in seen:
                raise SeafMCPError(
                    f"Tool '{tool_name}': parameter '{param.name}' defined in both "
                    f"{seen[param.name]} and {location}"
                )
            seen[param.name] = location


def validate_arguments(endpoint: Endpoint, arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments against endpoint definition.

    Returns:
        Error message for the agent, or None if arguments are valid.
    """
    all_params = [*endpoint.pathParams, *endpoint.queryParams, *endpoint.bodyParams]
    valid = {p.name for p in all_params}
    required = {p.name for p in all_params if p.required}

    errors = []
    unknown = sorted(set(arguments) - valid)
    if unknown:
        errors.append(
            f"Unknown parameter(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(sorted(valid)) or '(none)'}"
        )
    missing = sorted(n for n in required if arguments.get(n) in (None, ""))
    if missing:
        errors.append(f"Missing required parameter(s): {', '.join(missing)}")
    return "; ".join(errors) or None


def create_mcp_tool_definition(endpoint: Endpoint) -> dict[str, Any]:
    """Create MCP tool definition from endpoint.

    Args:
        endpoint: Endpoint specification

    Returns:
        MCP tool definition with name, description, and inputSchema
    """
    name = resolve_tool_name(endpoint)
    return {
        "name": name,
        "description": f"{endpoint.description}\n\nEndpoint: {endpoint.method} {endpoint.path}",
        "inputSchema": create_tool_input_schema(endpoint),
    }


def filter_and_create_tools(
    endpoints: list[Endpoint],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], dict[str, Endpoint]]:
    """Filter endpoints and create MCP tool definitions.

    Args:
        endpoints: List of all endpoints from catalog

    Returns:
        Tuple of:
        - List of MCP tool definitions (for included endpoints)
        - List of (tool_name, skip_reason) for excluded endpoints
        - Dict mapping tool_name -> Endpoint for fast dispatch
    """
    tools: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    tool_map: dict[str, Endpoint] = {}

    for endpoint in endpoints:
        tool_name = resolve_tool_name(endpoint)
        include, reason = should_include_endpoint(endpoint)

        if include:
            if tool_name in tool_map:
                other = tool_map[tool_name]
                raise SeafMCPError(
                    f"Duplicate tool name '{tool_name}': "
                    f"{other.method} {other.path} and {endpoint.method} {endpoint.path}"
                )
            _check_param_collisions(tool_name, endpoint)
            try:
                tool_def = create_mcp_tool_definition(endpoint)
                tools.append(tool_def)
                tool_map[tool_name] = endpoint
                logger.debug(f"Created tool: {tool_name}")
            except Exception as e:
                logger.error(f"Failed to create tool for {tool_name}: {e}")
                skipped.append((tool_name, f"Tool creation failed: {e}"))
        else:
            logger.info(f"Skipping {tool_name}: {reason}")
            skipped.append((tool_name, reason or "Unknown reason"))

    return tools, skipped, tool_map
