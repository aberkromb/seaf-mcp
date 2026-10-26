"""Async REST API client for calling SEAF endpoints."""

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from .errors import RestClientError

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({502, 503, 504})
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)

PATH_PARAM_PATTERN = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


class APIResponse:
    """Structured API response."""

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        body: str | dict | list,
        error: str | None = None,
    ):
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.error = error

    def is_success(self) -> bool:
        """Check if response indicates success (2xx status)."""
        return 200 <= self.status_code < 300

    def is_client_error(self) -> bool:
        """Check if response is client error (4xx status)."""
        return 400 <= self.status_code < 500

    def is_server_error(self) -> bool:
        """Check if response is server error (5xx status)."""
        return 500 <= self.status_code < 600


def _to_param_value(value: Any) -> str:
    """Serialize a parameter value for use in a URL.

    dict/list are serialized as JSON, booleans as lowercase strings.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class RestClient:
    """Async HTTP client for calling REST API endpoints.

    Handles:
    - Path parameter substitution (URL-encoded)
    - Query parameter encoding
    - JSON body serialization
    - Timeout and error handling
    - Limited retry of confirmed transient failures: ConnectError,
      ConnectTimeout and HTTP 502/503/504, at most 4 attempts with the
      retry_delays pauses (default 1/2/4 s).
      ReadTimeout, 4xx and HTTP 500 are never retried; probe() is not retried.
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        min_interval: float = 0,
    ):
        """Initialize REST client.

        Args:
            base_url: Base URL without trailing slash
            timeout: Request timeout in seconds
            headers: Extra headers sent with every request (reserved for auth)
            transport: Optional custom transport (used in tests)
            retry_delays: Pauses between attempts (tests use zeros)
            min_interval: Min seconds between requests to the portal; the next
                slot is reserved under a lock, the wait happens outside it.
                0 disables pacing (off by default)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_delays = tuple(retry_delays)
        self.min_interval = min_interval
        self._slot_lock = asyncio.Lock()
        self._next_slot = 0.0
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers or {},
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close HTTP client and release resources."""
        await self.client.aclose()

    @staticmethod
    def substitute_path_params(path: str, path_params: dict[str, Any]) -> str:
        """Substitute :param placeholders in path with URL-encoded values.

        Raises:
            RestClientError: If a path parameter is missing
        """

        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in path_params or path_params[name] in (None, ""):
                raise RestClientError(f"Missing required path parameter: {name}")
            return quote(_to_param_value(path_params[name]), safe="")

        return PATH_PARAM_PATTERN.sub(replace, path)

    @staticmethod
    def build_query_params(query_params: dict[str, Any]) -> dict[str, str]:
        """Drop empty values and serialize the rest."""
        return {
            k: _to_param_value(v)
            for k, v in query_params.items()
            if v is not None and v != ""
        }

    async def _wait_for_slot(self) -> None:
        """Reserve the next request slot. No-op when pacing is off.

        The slot is reserved under the lock; the sleep happens outside it so
        concurrent calls keep FIFO order and cancellation leaves no pending
        lock holders.
        """
        if self.min_interval <= 0:
            return
        async with self._slot_lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def call(
        self,
        method: str,
        path: str,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> APIResponse:
        """Call REST API endpoint.

        Raises:
            RestClientError: For network errors or invalid parameters
        """
        final_path = self.substitute_path_params(path, path_params or {})
        url = f"{self.base_url}{final_path}"
        params = self.build_query_params(query_params or {})

        logger.debug(f"Calling {method} {url} params={sorted(params)}")
        if body:
            logger.debug(f"Request body: params={sorted(body)}")

        attempts = 1 + len(self.retry_delays)
        for attempt in range(1, attempts + 1):
            await self._wait_for_slot()
            try:
                response = await self.client.request(
                    method=method.upper(),
                    url=url,
                    params=params or None,
                    json=body if body else None,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                if attempt < attempts:
                    delay = self.retry_delays[attempt - 1]
                    logger.warning(
                        f"{method} {final_path}: attempt {attempt}/{attempts} failed "
                        f"({type(e).__name__}), retry in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                if isinstance(e, httpx.ConnectTimeout):
                    raise RestClientError(
                        f"Connect to {self.base_url} timed out after {self.timeout}s "
                        f"({attempts} attempts). Check API_BASE_URL and network access.")
                raise RestClientError(
                    f"Cannot connect to {self.base_url}: {e}. "
                    "Check API_BASE_URL (inside Docker, 'localhost' is the container itself; "
                    "use http://host.docker.internal:<port> for a service on the host)."
                )
            except httpx.TimeoutException:
                raise RestClientError(
                    f"Request timed out after {self.timeout}s. "
                    "Try a narrower query or increase REQUEST_TIMEOUT."
                )
            except httpx.HTTPError as e:
                raise RestClientError(f"HTTP error: {type(e).__name__}: {e}")

            if response.status_code not in RETRYABLE_STATUSES or attempt == attempts:
                break
            delay = self.retry_delays[attempt - 1]
            logger.warning(
                f"{method} {final_path}: attempt {attempt}/{attempts} got "
                f"HTTP {response.status_code}, retry in {delay}s")
            await asyncio.sleep(delay)

        content_type = response.headers.get("content-type", "")
        parsed_body: str | dict | list
        if "application/json" in content_type:
            try:
                parsed_body = response.json()
            except ValueError:
                parsed_body = response.text
        else:
            parsed_body = response.text

        logger.debug(f"Response status: {response.status_code}")

        error = None
        if not response.is_success:
            error = response.text or response.reason_phrase or "(empty response body)"

        return APIResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=parsed_body,
            error=error,
        )

    async def probe(self, path: str = "/health") -> tuple[bool, str]:
        """Best-effort availability check. Never raises. Never retried (019)."""
        try:
            await self._wait_for_slot()
            response = await self.client.get(f"{self.base_url}{path}")
            return response.is_success, f"HTTP {response.status_code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
