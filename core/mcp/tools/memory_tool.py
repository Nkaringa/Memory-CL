"""`update_memory(session_data)` — append-only session memory backed by Redis.

Per RETRIEVAL_SYSTEM_SPEC and PROJECT_PROMPT, session memory is
append-only. We model each session as a Redis list keyed by
`mcp:mem:<repo_id>:<session_id>`. Each call adds one entry; entries
are JSON-encoded with deterministic key ordering.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from core import get_settings
from core.mcp.execution.tool_executor import ExecutionContext, Tool  # noqa: F401
from core.mcp.schemas import UpdateMemoryRequest


def _key(repo_id: str, session_id: str) -> str:
    return f"mcp:mem:{repo_id}:{session_id}"


class UpdateMemoryTool:
    name: str = "update_memory"
    description: str = (
        "MUTATES STATE — appends one JSON entry to your session's "
        "memory (Redis-backed, append-only, TTL-bound), e.g. "
        "update_memory(session_id='s1', repo_id='memory-cl', "
        "session_data={'finding': 'auth lives in core/auth'}). Use to "
        "persist working notes across calls in one session. It does NOT "
        "modify ingested code or indexes, and there is no delete."
    )
    request_schema = UpdateMemoryRequest

    async def execute(
        self, request: UpdateMemoryRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        settings = get_settings()
        client = ctx.state.redis.client  # raises if not connected
        key = _key(request.repo_id, request.session_id)

        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "data": request.session_data,
        }
        # Deterministic JSON serialization so two equal entries produce
        # byte-identical strings (sort_keys + compact separators).
        encoded = json.dumps(entry, sort_keys=True, separators=(",", ":"))

        # Atomic RPUSH + EXPIRE — the second call resets the TTL on
        # every update so an active session stays alive.
        await client.rpush(key, encoded)
        await client.expire(key, settings.mcp_session_ttl_seconds)
        length = await client.llen(key)

        return {
            "session_id": request.session_id,
            "repo_id": request.repo_id,
            "stored": True,
            "entries": int(length),
            "ttl_seconds": settings.mcp_session_ttl_seconds,
        }


__all__ = ["UpdateMemoryTool"]
