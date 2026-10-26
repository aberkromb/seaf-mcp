FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/seaf_mcp/ ./seaf_mcp/

# Copy endpoint catalog
COPY endpoints.json ./endpoints.json

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV API_BASE_URL=""
ENV ENDPOINT_CATALOG_PATH="/app/endpoints.json"
ENV REQUEST_TIMEOUT="30"
ENV MAX_RESPONSE_CHARS="50000"
ENV HEALTH_CHECK_PATH="/seaf-core/api/core/storage/versions"
ENV LOG_LEVEL="INFO"

# Health check: server should start without errors
# Note: Actual health is determined by MCP protocol, not HTTP
HEALTHCHECK NONE

# Run MCP server
CMD ["python", "-m", "seaf_mcp"]
