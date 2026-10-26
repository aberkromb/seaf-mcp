"""Configuration management for SEAF MCP Server."""

import os
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError


class Config(BaseSettings):
    """Configuration loaded from environment variables.
    
    Required:
        API_BASE_URL: Base URL for SEAF REST API
    
    Optional:
        ENDPOINT_CATALOG_PATH: Path to endpoints.json (default: /app/endpoints.json)
        REQUEST_TIMEOUT: HTTP request timeout in seconds (default: 30)
        MAX_RESPONSE_CHARS: Response size limit for agent, 0 = off (default: 50000)
        HEALTH_CHECK_PATH: Path probed at startup, empty = skip
            (default: /seaf-core/api/core/storage/versions)
        LOG_LEVEL: Logging level (default: INFO)
    """
    
    api_base_url: str = Field(..., description="Base URL for SEAF REST API")
    endpoint_catalog_path: str = Field(
        default="/app/endpoints.json",
        description="Path to endpoint catalog JSON file"
    )
    request_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="HTTP request timeout in seconds"
    )
    api_min_interval_seconds: float = Field(
        default=0,
        ge=0,
        alias="API_MIN_INTERVAL_SECONDS",
        description="Min interval between requests to the portal (0 = disabled)"
    )
    max_response_chars: int = Field(
        default=50000,
        description="Max characters of response returned to agent (0 = unlimited)"
    )
    health_check_path: str = Field(
        default="/seaf-core/api/core/storage/versions",
        description="Path probed once at startup (empty = skip)"
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR"
    )
    seaf_log_file: str = Field(
        default="",
        alias="SEAF_LOG_FILE",
        description="Diagnostic log file (rotating, 10MB x 3); empty = stderr only"
    )
    seaf_metamodel_path: str = Field(
        default="",
        alias="SEAF_METAMODEL_PATH",
        description="Path to a local SEAF metamodel repo for YAML examples (empty = cookbook only)"
    )
    mcp_transport: str = Field(
        default="stdio",
        alias="MCP_TRANSPORT",
        description="MCP transport: stdio or streamable-http"
    )
    mcp_http_host: str = Field(
        default="127.0.0.1",
        alias="MCP_HTTP_HOST",
        description="Bind host for streamable-http (0.0.0.0 exposes the model to the network!)"
    )
    mcp_http_port: int = Field(
        default=14280,
        ge=1,
        le=65535,
        alias="MCP_HTTP_PORT",
        description="Bind port for streamable-http; MCP endpoint is /mcp"
    )
    jsonata_log_file: str = Field(
        default="",
        alias="SEAF_JSONATA_LOG_FILE",
        description="Path to the JSONL query log (empty = disabled)"
    )
    jsonata_log_include_query: bool = Field(
        default=False,
        alias="SEAF_JSONATA_LOG_INCLUDE_QUERY",
        description="Write the full query text to the JSONL log (needed for cookbook)"
    )
    jsonata_log_max_bytes: int = Field(
        default=10_000_000,
        ge=10_000,
        alias="SEAF_JSONATA_LOG_MAX_BYTES",
        description="Rotate the JSONL log to .1 above this size"
    )
    
    @field_validator("api_base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        """Ensure base URL doesn't end with /"""
        if not v:
            raise ConfigError("API_BASE_URL is required")
        return v.rstrip("/")
    
    @field_validator("max_response_chars")
    @classmethod
    def validate_max_response_chars(cls, v: int) -> int:
        """0 disables truncation; otherwise 1000..1000000."""
        if v != 0 and not (1000 <= v <= 1_000_000):
            raise ConfigError("MAX_RESPONSE_CHARS must be 0 or between 1000 and 1000000")
        return v

    @field_validator("mcp_transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower not in ("stdio", "streamable-http"):
            raise ConfigError(
                f"Invalid MCP_TRANSPORT: {v}. Must be 'stdio' or 'streamable-http'")
        return v_lower

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log level is valid."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ConfigError(
                f"Invalid LOG_LEVEL: {v}. Must be one of: {', '.join(valid_levels)}"
            )
        return v_upper
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      populate_by_name=True)


def load_config() -> Config:
    """Load and validate configuration from environment variables.
    
    Returns:
        Config: Validated configuration object
    
    Raises:
        ConfigError: If configuration is invalid or missing required values
    """
    try:
        return Config()
    except Exception as e:
        raise ConfigError(f"Failed to load configuration: {e}")
