"""Custom exceptions for SEAF MCP Server."""


class SeafMCPError(Exception):
    """Base exception for all SEAF MCP errors."""
    pass


class ConfigError(SeafMCPError):
    """Configuration error (missing or invalid environment variables)."""
    pass


class CatalogLoadError(SeafMCPError):
    """Error loading or parsing endpoint catalog."""
    pass


class EndpointNotAllowedError(SeafMCPError):
    """Endpoint does not pass filtering rules."""
    pass


class RestClientError(SeafMCPError):
    """Error during REST API call."""
    pass


class PortalError(RestClientError):
    """Structured portal/API error: HTTP status + JSONata error details.

    Raised when the portal answered with an error response (not on
    network-level failures). `code`/`token`/`position` come from the
    portal's error body and may be None.
    """

    def __init__(self, status: int, message: str, code: str | None = None,
                 token: str | None = None, position: int | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.code = code
        self.token = token
        self.position = position
