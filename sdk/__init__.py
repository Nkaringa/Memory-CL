"""Memory-CL Python SDK — Phase-9 developer surface.

Wraps the existing HTTP API only. Adds zero new business logic.
Every method is async-first, fully typed, and raises a structured
`MemoryClientError` on non-2xx responses.

Usage:
    from sdk import AsyncMemoryClient

    async with AsyncMemoryClient(base_url="http://localhost:8000") as c:
        result = await c.search_code(question="auth flow", repo_id="acme")
"""

from sdk.client import (
    AsyncMemoryClient,
    MemoryClientError,
)
from sdk.types import (
    AppConfigView,
    ExploreNeighbor,
    ExploreResult,
    FindSymbolResult,
    IngestResult,
    KeyResult,
    McpToolResult,
    QueryGraphResult,
    ReadUnitResult,
    ReembedResult,
    ReplayResult,
    RepoOverviewResult,
    ReposResult,
    RepoSummary,
    RetrieveResult,
    SearchCodeResult,
    SearchHit,
    SnapshotResult,
    StatusResult,
    SymbolMatch,
)

__all__ = [
    "AppConfigView",
    "AsyncMemoryClient",
    "ExploreNeighbor",
    "ExploreResult",
    "FindSymbolResult",
    "IngestResult",
    "KeyResult",
    "McpToolResult",
    "MemoryClientError",
    "QueryGraphResult",
    "ReadUnitResult",
    "ReembedResult",
    "ReplayResult",
    "RepoOverviewResult",
    "RepoSummary",
    "ReposResult",
    "RetrieveResult",
    "SearchCodeResult",
    "SearchHit",
    "SnapshotResult",
    "StatusResult",
    "SymbolMatch",
]
