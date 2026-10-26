"""Append-only JSONL log of agent-written JSONata queries.

Off by default; enabled via SEAF_JSONATA_LOG_FILE. Records are minimal:
timestamp, tool, status (empty|non_empty|error), error code, duration and
SHA-256 of the query. The full query text is written only when
SEAF_JSONATA_LOG_INCLUDE_QUERY=true (needed to build the cookbook, 016).

Never logged: user_request, dataset params, API responses, cookies, certs.
Write failures never break the tool; the warning is logged at most once.
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class QueryLog:
    def __init__(self, path: str = "", include_query: bool = False,
                 max_bytes: int = 10_000_000):
        self.path = path
        self.include_query = include_query
        self.max_bytes = max_bytes
        self._lock = asyncio.Lock()
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    async def log(self, tool: str, query: str, status: str,
                  error_code: str | None = None, duration_ms: int = 0) -> None:
        """Append one JSONL record. Never raises."""
        if not self.enabled:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "tool": tool,
            "status": status,
            "duration_ms": duration_ms,
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        }
        if error_code:
            record["error_code"] = error_code
        if self.include_query:
            record["query"] = query
        line = json.dumps(record, ensure_ascii=False) + "\n"
        async with self._lock:
            try:
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                if not self._warned:
                    self._warned = True
                    logger.warning(f"Query log disabled (cannot write {self.path}): {e}")

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self.path) >= self.max_bytes:
                os.replace(self.path, self.path + ".1")
        except FileNotFoundError:
            pass
