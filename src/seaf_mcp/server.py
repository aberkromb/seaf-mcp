"""SEAF MCP Server - Main entrypoint."""

import os
import re
import sys
import time
import logging
import asyncio
from logging.handlers import RotatingFileHandler
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource, ToolAnnotations
import json

from .config import load_config, Config
from .endpoint_catalog import load_catalog, Endpoint, EndpointCatalog
from .tool_factory import filter_and_create_tools, validate_arguments
from .rest_client import RestClient
from .response_formatter import (format_success, format_error, format_client_error,
                                 format_portal_error, empty_result_note, describe_result_size)
from .errors import SeafMCPError, RestClientError, PortalError
from .jsonata_errors import extract_error_info
from .jsonata_preflight import preflight
from .query_log import QueryLog
from .seaf_tools import SeafTools, GUIDE_URI, load_guide
from .response_formatter import truncate

# High-level tools where the JSONata expression is written by the agent
# (fix hints are shown only for these; server-built queries stay neutral).
AGENT_EXPRESSION_TOOLS = frozenset({"query_dataset", "describe_query_result"})

# All published tools only read data: write ops and hidden endpoints are
# filtered out of the catalog, high-level tools are read-only by design.
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

CATALOG_TOOL_TITLES = {
    "get_health": "Проверка портала",
    "seaf_jsonata_query": "Запрос JSONata",
    "get_seaf_core_api_core_storage_versions": "Версия портала",
    "get_seaf_core_api_core_storage_problems": "Проблемы манифеста",
}

# CRITICAL: Log only to stderr to avoid breaking MCP protocol on stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

LOG_FILE_MAX_BYTES = 10_000_000
LOG_FILE_BACKUP_COUNT = 3


def setup_file_logging(path: str, max_bytes: int = LOG_FILE_MAX_BYTES,
                       backup_count: int = LOG_FILE_BACKUP_COUNT) -> None:
    """Attach a rotating diagnostic log file.

    Off when path is empty. On open failure logs one clear message and the
    server continues with stderr only. Never raises.
    """
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=max_bytes,
                                      backupCount=backup_count, encoding="utf-8")
    except OSError as e:
        logger.error(f"Cannot open SEAF_LOG_FILE {path}: {e}; continuing with stderr only")
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logger.info(f"Diagnostic log file: {path}")


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def wrap_jsonata(value: Any) -> Any:
    """SEAF treats a query not starting with '(' as a predefined-query ID.

    Wrap free-form expressions in parentheses (always safe for JSONata);
    leave UUIDs of built-in queries untouched.
    """
    if not isinstance(value, str):
        return value
    v = value.strip()
    if UUID_RE.match(v):
        return v
    return f"({v})"


def _portal_error(response, label: str) -> PortalError:
    """Build a structured PortalError from an API error response."""
    info = extract_error_info(response.body)
    msg = info["message"] or response.error or "unknown portal error"
    return PortalError(
        status=response.status_code,
        message=f"{label} failed: {msg}",
        code=info["code"],
        token=info["token"],
        position=info["position"],
    )


class SeafMCPServer:
    """SEAF MCP Server - bridges MCP protocol to REST API."""
    
    def __init__(self, config: Config, catalog: EndpointCatalog):
        """Initialize server.
        
        Args:
            config: Server configuration
            catalog: API endpoint catalog
        """
        self.config = config
        self.catalog = catalog
        self.rest_client = RestClient(
            base_url=config.api_base_url,
            timeout=config.request_timeout,
            min_interval=config.api_min_interval_seconds,
        )
        self.query_log = QueryLog(
            path=config.jsonata_log_file,
            include_query=config.jsonata_log_include_query,
            max_bytes=config.jsonata_log_max_bytes,
        )
        
        # Create MCP server
        self.server = Server("seaf-mcp")
        
        # Filter endpoints and create tools
        self.tools, self.skipped, self.tool_map = filter_and_create_tools(catalog.endpoints)
        
        # Log summary
        logger.info(f"Loaded {len(catalog.endpoints)} endpoints from catalog")
        logger.info(f"Registered {len(self.tools)} MCP tools")
        logger.info(f"Skipped {len(self.skipped)} endpoints")
        
        for endpoint_id, reason in self.skipped:
            logger.debug(f"  - {endpoint_id}: {reason}")
        
        # High-level SEAF tools (JSONata templates)
        self.seaf_tools = SeafTools(self.run_jsonata, self.run_dataset, self.run_dataset_inline,
                                    max_chars=config.max_response_chars,
                                    metamodel_path=config.seaf_metamodel_path)
        self.seaf_defs = {d["name"]: d for d in self.seaf_tools.definitions()}
        clash = set(self.seaf_defs) & set(self.tool_map)
        if clash:
            raise SeafMCPError(f"High-level tool names clash with catalog: {clash}")
        logger.info(f"Registered {len(self.seaf_defs)} high-level SEAF tools")

        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register MCP protocol handlers."""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return list of available tools."""
            return [
                Tool(
                    name=tool["name"],
                    title=tool.get("title") or CATALOG_TOOL_TITLES.get(tool["name"]),
                    description=tool["description"],
                    inputSchema=tool["inputSchema"],
                    annotations=READ_ONLY_ANNOTATIONS,
                )
                for tool in [*self.seaf_defs.values(), *self.tools]
            ]

        @self.server.list_resources()
        async def list_resources() -> list[Resource]:
            return [Resource(uri=GUIDE_URI, name="SEAF metamodel guide",
                             description="Справочник метамодели SEAF", mimeType="text/markdown")]

        @self.server.read_resource()
        async def read_resource(uri) -> str:
            if str(uri) != GUIDE_URI:
                raise SeafMCPError(f"Unknown resource: {uri}")
            return load_guide()
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool call from MCP client."""
            logger.info(f"Tool called: {name}")
            logger.debug(f"Arguments: {sorted(arguments)} ({len(arguments)} params)")
            
            try:
                result = await self._execute_tool(name, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"Tool execution failed: {e}", exc_info=True)
                error_text = format_client_error(name, str(e))
                return [TextContent(type="text", text=error_text)]
    
    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute tool by calling REST API.
        
        Args:
            tool_name: Tool identifier (same as endpoint.id)
            arguments: Tool arguments from agent
        
        Returns:
            Formatted response text for agent
        
        Raises:
            SeafMCPError: If tool execution fails
        """
        if tool_name in self.seaf_defs:
            return await self._execute_seaf_tool(tool_name, arguments or {})

        # Find endpoint via tool_map (supports both explicit id and generated names)
        endpoint = self.tool_map.get(tool_name)
        if not endpoint:
            raise SeafMCPError(f"Tool not found: {tool_name}")

        arguments = arguments or {}
        validation_error = validate_arguments(endpoint, arguments)
        if validation_error:
            return format_client_error(tool_name, validation_error)
        
        started = time.monotonic()

        # Preflight known-bad constructs in agent-written expressions
        for key in endpoint.jsonataParams:
            value = arguments.get(key)
            if isinstance(value, str):
                hint = preflight(value)
                if hint:
                    await self._log_agent_query(tool_name, endpoint, arguments, "error", started)
                    return format_client_error(tool_name, f"Query preflight: {hint}")

        arguments = {
            k: wrap_jsonata(v) if k in endpoint.jsonataParams else v
            for k, v in arguments.items()
        }

        # Separate arguments into path/query/body params
        path_params = {}
        query_params = {}
        body_params = {}
        
        # Categorize arguments based on endpoint definition
        path_param_names = {p.name for p in endpoint.pathParams}
        query_param_names = {p.name for p in endpoint.queryParams}
        body_param_names = {p.name for p in endpoint.bodyParams}
        
        for key, value in arguments.items():
            if key in path_param_names:
                path_params[key] = value
            elif key in query_param_names:
                query_params[key] = value
            elif key in body_param_names:
                body_params[key] = value
        
        # Prepare body (only if there are body params)
        body = body_params if body_params else None
        
        logger.debug(f"Path params: {sorted(path_params)}")
        logger.debug(f"Query params: {sorted(query_params)}")
        logger.debug(f"Body params: {sorted(body) if body else '-'}")
        
        # Call REST API
        try:
            response = await self.rest_client.call(
                method=endpoint.method,
                path=endpoint.path,
                path_params=path_params,
                query_params=query_params,
                body=body,
            )
        except RestClientError as e:
            logger.error(f"REST client error: {e}")
            await self._log_agent_query(tool_name, endpoint, arguments, "error", started)
            return format_client_error(tool_name, str(e))

        if endpoint.jsonataParams:
            if response.is_success():
                status = "empty" if empty_result_note(response.body) else "non_empty"
                code = None
            else:
                status = "error"
                code = extract_error_info(response.body)["code"]
            await self._log_agent_query(tool_name, endpoint, arguments, status, started, code)
        
        # Format response
        if response.is_success():
            return format_success(
                method=endpoint.method,
                path=endpoint.path,
                response=response,
                max_chars=self.config.max_response_chars,
            )
        else:
            return format_error(
                tool_name=tool_name,
                response=response,
                max_chars=self.config.max_response_chars,
                jsonata_hint=bool(endpoint.jsonataParams),
            )
    
    async def run_jsonata(self, query: str) -> Any:
        """Run a free-form JSONata query and return parsed JSON; raise on failure."""
        response = await self.rest_client.call(
            method="POST",
            path="/seaf-core/api/core/storage/jsonata",
            body={"query": f"({query})"},
        )
        if not response.is_success():
            raise _portal_error(response, "JSONata query")
        if isinstance(response.body, str):
            return json.loads(response.body) if response.body.strip() else None
        return response.body

    async def run_dataset(self, dataset_id: str, params: dict[str, Any] | None = None) -> Any:
        """Read a named dataset via release-data-profile; return parsed JSON; raise on failure."""
        return await self._run_release_data_profile(
            {"query": f"/datasets/{dataset_id}"}, params, f"Dataset '{dataset_id}'")

    async def run_dataset_inline(self, origin: str | dict, source: str,
                                 params: dict[str, Any] | None = None) -> Any:
        """Run JSONata over dataset(s) via release-data-profile inline form."""
        return await self._run_release_data_profile(
            {"query": {"origin": origin, "source": source}}, params, f"Dataset query on '{origin}'")

    async def _run_release_data_profile(self, body: dict[str, Any],
                                        params: dict[str, Any] | None, label: str) -> Any:
        if params:
            body["params"] = params
        response = await self.rest_client.call(
            method="POST",
            path="/seaf-core/api/core/storage/release-data-profile",
            body=body,
        )
        if not response.is_success():
            raise _portal_error(response, label)
        if isinstance(response.body, str):
            return json.loads(response.body) if response.body.strip() else None
        return response.body

    async def _execute_seaf_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        definition = self.seaf_defs[tool_name]
        props = definition["inputSchema"]["properties"]
        unknown = sorted(set(arguments) - set(props))
        missing = [n for n in definition["inputSchema"]["required"] if arguments.get(n) in (None, "")]
        if unknown or missing:
            return format_client_error(
                tool_name, f"Unknown: {unknown or '-'}; missing: {missing or '-'}; valid: {sorted(props)}")
        started = time.monotonic()
        try:
            result = await definition["handler"](arguments)
        except PortalError as e:
            await self._log_agent_query(tool_name, None, arguments, "error", started, e.code)
            return format_portal_error(
                tool_name, e, max_chars=self.config.max_response_chars,
                agent_expression=tool_name in AGENT_EXPRESSION_TOOLS)
        except RestClientError as e:
            await self._log_agent_query(tool_name, None, arguments, "error", started)
            return format_client_error(tool_name, str(e))
        if tool_name in AGENT_EXPRESSION_TOOLS:
            if isinstance(result, dict) and "error" in result:
                status = "error"
            elif isinstance(result, dict) and result.get("result_type") == "missing":
                status = "empty"
            else:
                status = "empty" if empty_result_note(result) else "non_empty"
            await self._log_agent_query(tool_name, None, arguments, status, started)
        empty_note = empty_result_note(result)
        if empty_note is not None and not isinstance(result, str):
            return empty_note
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        return truncate(text, self.config.max_response_chars,
                        details=describe_result_size(result))

    async def _log_agent_query(self, tool_name: str, endpoint, arguments: dict[str, Any],
                               status: str, started: float,
                               error_code: str | None = None) -> None:
        """Log agent-written JSONata calls (seaf_jsonata_query via jsonataParams,
        query_dataset, describe_query_result). Server-built queries are not logged."""
        if not self.query_log.enabled:
            return
        if endpoint is not None and endpoint.jsonataParams:
            query = next((arguments[k] for k in endpoint.jsonataParams
                          if isinstance(arguments.get(k), str)), None)
        else:
            if tool_name not in AGENT_EXPRESSION_TOOLS:
                return
            query = arguments.get("source") or arguments.get("query")
        if not isinstance(query, str):
            return
        await self.query_log.log(tool_name, query, status, error_code,
                                 duration_ms=int((time.monotonic() - started) * 1000))

    async def run(self):
        """Run MCP server with stdio transport."""
        logger.info("Starting SEAF MCP Server...")
        logger.info(f"API Base URL: {self.config.api_base_url}")
        logger.info(f"Request timeout: {self.config.request_timeout}s")
        logger.info(f"Max response chars: {self.config.max_response_chars or 'unlimited'}")
        await self.check_api_health()
        
        async with stdio_server() as (read_stream, write_stream):
            logger.info("MCP server running on stdio")
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )
    
    async def run_http(self):
        """Run MCP server with the Streamable HTTP transport.

        Stateless mode: tools are read-only and keep no per-session state
        (QueryLog/SeafTools live at process level), so no session store is
        needed. DNS-rebinding protection validates Host/Origin headers.
        """
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings

        host, port = self.config.mcp_http_host, self.config.mcp_http_port
        logger.info("Starting SEAF MCP Server (streamable-http)...")
        logger.info(f"API Base URL: {self.config.api_base_url}")
        await self.check_api_health()

        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:*", "127.0.0.1:*", "localhost:*"],
            allowed_origins=[
                f"http://{host}:*", f"https://{host}:*",
                "http://127.0.0.1:*", "https://127.0.0.1:*",
                "http://localhost:*", "https://localhost:*",
            ],
        )
        manager = StreamableHTTPSessionManager(
            app=self.server, json_response=True, stateless=True,
            security_settings=security)

        async def asgi_app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            elif scope["type"] == "http" and scope["path"] == "/mcp":
                await manager.handle_request(scope, receive, send)
            else:
                response = b"Not Found; MCP endpoint is /mcp"
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain"),
                                        (b"content-length", str(len(response)).encode())]})
                await send({"type": "http.response.body", "body": response})

        uvicorn_log = "debug" if self.config.log_level == "DEBUG" else "warning"
        async with manager.run():
            logger.info(f"MCP server listening on http://{host}:{port}/mcp (stateless, no auth)")
            if host not in ("127.0.0.1", "localhost", "::1"):
                logger.warning(
                    f"Bound to {host}: the port is reachable from the network and there is "
                    "NO incoming auth — anyone who can reach it can read the architecture model. "
                    "Publish only on localhost or behind an authenticating proxy (see INSTALL.md).")
            http_server = uvicorn.Server(uvicorn.Config(
                asgi_app, host=host, port=port, log_level=uvicorn_log))
            await http_server.serve()

    async def check_api_health(self) -> bool:
        """Probe the API once at startup. Logs result, never raises."""
        path = self.config.health_check_path
        if not path:
            return True
        ok, detail = await self.rest_client.probe(path)
        if ok:
            logger.info(f"API health check OK: GET {path} -> {detail}")
        else:
            logger.warning(
                f"API health check FAILED: GET {self.config.api_base_url}{path} -> {detail}. "
                "Server will start anyway; tool calls may fail."
            )
        return ok

    async def cleanup(self):
        """Cleanup resources."""
        await self.rest_client.close()


async def main():
    """Main entry point."""
    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = load_config()
        
        # Update log level from config
        logging.getLogger().setLevel(config.log_level)
        logger.info(f"Log level set to: {config.log_level}")
        setup_file_logging(config.seaf_log_file)
        
        # Load endpoint catalog
        logger.info(f"Loading endpoint catalog from: {config.endpoint_catalog_path}")
        catalog = load_catalog(config.endpoint_catalog_path)
        logger.info(f"Catalog version: {catalog.version}")
        
        # Create and run server
        server = SeafMCPServer(config, catalog)
        
        try:
            if config.mcp_transport == "streamable-http":
                await server.run_http()
            else:
                await server.run()
        finally:
            await server.cleanup()
    
    except SeafMCPError as e:
        logger.error(f"SEAF MCP Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


def cli():
    """Console-script entry point (pyproject [project.scripts])."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
