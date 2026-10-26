"""Endpoint catalog loading and parsing."""

import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from .errors import CatalogLoadError


class ParamModel(BaseModel):
    """Base parameter model."""
    name: str
    type: str
    required: bool
    description: str


class PathParam(ParamModel):
    """Path parameter in URL (e.g., :id in /api/users/:id)."""
    pass


class QueryParam(ParamModel):
    """Query parameter in URL (e.g., ?limit=10)."""
    pass


class BodyParam(ParamModel):
    """Body parameter for POST/PUT requests."""
    pass


class Endpoint(BaseModel):
    """API endpoint specification."""
    id: str | None = Field(default=None, description="Unique tool identifier; if absent, name is generated from method+path")
    description: str = Field(..., description="Human-readable description")
    method: str = Field(..., description="HTTP method: GET, POST, PUT, DELETE, etc.")
    path: str = Field(..., description="URL path with optional :params")
    pathParams: list[PathParam] = Field(default_factory=list)
    queryParams: list[QueryParam] = Field(default_factory=list)
    bodyParams: list[BodyParam] = Field(default_factory=list)
    readOnly: bool = Field(..., description="Whether endpoint is read-only")
    hidden: bool = Field(default=False, description="Not exposed as MCP tool")
    jsonataParams: list[str] = Field(
        default_factory=list,
        description="Params holding free-form JSONata; server wraps them in (...) unless the value is a UUID",
    )
    requestExample: str = Field(..., description="Example curl command")
    responseDescription: str = Field(..., description="Description of response format")
    
    def has_path_params(self) -> bool:
        """Check if endpoint has path parameters."""
        return len(self.pathParams) > 0
    
    def has_query_params(self) -> bool:
        """Check if endpoint has query parameters."""
        return len(self.queryParams) > 0
    
    def has_body_params(self) -> bool:
        """Check if endpoint has body parameters."""
        return len(self.bodyParams) > 0


class EndpointCatalog(BaseModel):
    """Complete catalog of API endpoints."""
    version: str = Field(..., description="Catalog version")
    baseUrl: str = Field(..., description="Default base URL (overridden by config)")
    endpoints: list[Endpoint] = Field(..., description="List of API endpoints")
    
    def get_endpoint_by_id(self, endpoint_id: str) -> Endpoint | None:
        """Find endpoint by ID."""
        for endpoint in self.endpoints:
            if endpoint.id == endpoint_id:
                return endpoint
        return None


def load_catalog(catalog_path: str) -> EndpointCatalog:
    """Load and parse endpoint catalog from JSON file.
    
    Args:
        catalog_path: Path to endpoints.json file
    
    Returns:
        EndpointCatalog: Parsed and validated catalog
    
    Raises:
        CatalogLoadError: If file cannot be read or parsed
    """
    path = Path(catalog_path)
    
    if not path.exists():
        raise CatalogLoadError(f"Catalog file not found: {catalog_path}")
    
    if not path.is_file():
        raise CatalogLoadError(f"Catalog path is not a file: {catalog_path}")
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise CatalogLoadError(f"Invalid JSON in catalog file: {e}")
    except Exception as e:
        raise CatalogLoadError(f"Failed to read catalog file: {e}")
    
    try:
        catalog = EndpointCatalog(**data)
    except Exception as e:
        raise CatalogLoadError(f"Invalid catalog structure: {e}")
    
    return catalog
