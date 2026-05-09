"""Memory-CL Python SDK — Phase-9 developer surface.

Wraps the existing HTTP API only. Adds zero new business logic.
Every method is async-first, fully typed, and raises a structured
`MemoryClientError` on non-2xx responses.

Usage:
    from sdk import AsyncMemoryClient

    async with AsyncMemoryClient(base_url="http://localhost:8000") as c:
        result = await c.retrieve(text="auth flow", repo_id="acme")
"""

from sdk.client import (
    AsyncMemoryClient,
    MemoryClientError,
)
from sdk.types import (
    IngestResult,
    McpToolResult,
    QueryGraphResult,
    ReplayResult,
    RetrieveResult,
    SnapshotResult,
    StatusResult,
)

__all__ = [
    "AsyncMemoryClient",
    "IngestResult",
    "McpToolResult",
    "MemoryClientError",
    "QueryGraphResult",
    "ReplayResult",
    "RetrieveResult",
    "SnapshotResult",
    "StatusResult",
]
