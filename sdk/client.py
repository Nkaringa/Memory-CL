"""AsyncMemoryClient — Phase-9 typed client over the existing HTTP API.

Pure orchestration: every method serializes a request, fires it, and
parses the response into a Pydantic model from `sdk.types`. There is
no caching, no retry, no business logic — those concerns live (and
should keep living) in the server-side Phase 1-8 layers.

Phase-10 addition: every outbound request carries an ``X-Request-ID``
header so traces and logs on the API side correlate with the SDK
caller. The id is either supplied by the caller (per-call override),
fixed for the lifetime of the client (constructor argument), or
freshly generated per request via ``uuid.uuid4()``.
"""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any

import httpx

from sdk.types import (
    IngestResult,
    McpToolResult,
    QueryGraphResult,
    ReembedResult,
    ReplayResult,
    RetrieveResult,
    SnapshotResult,
    StatusResult,
)

# Header name kept in sync with apps/api/middleware.RequestContextMiddleware.
_REQUEST_ID_HEADER = "X-Request-ID"


class MemoryClientError(RuntimeError):
    """Raised when the API returns a non-2xx response.

    `status_code` and `body` are populated for callers that want to
    branch on specific error shapes.
    """

    def __init__(self, *, status_code: int, body: Any, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status_code} from {url}: {body}")


class AsyncMemoryClient:
    """Single entry point to the running Memory-CL service.

    The client owns its `httpx.AsyncClient`. Use it as an async
    context manager (`async with`) so the underlying connection
    pool is released cleanly.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        request_id: str | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=headers,
            transport=transport,
        )
        # Phase-10 correlation id. None ⇒ auto-generate per request.
        # A non-None pin means every call from this client carries the
        # same id — useful for CLI sessions that fire several calls
        # under one logical "operation".
        self._pinned_request_id = request_id

    # ----- async lifecycle -----
    async def __aenter__(self) -> AsyncMemoryClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- ingestion -----
    async def ingest_repository(
        self, *, repo_id: str, repo_path: str, commit_sha: str,
    ) -> IngestResult:
        """Trigger Phase-2 IngestionPipeline via POST /ingest."""
        return IngestResult.model_validate(await self._post_json(
            "/ingest",
            {"repo_id": repo_id, "repo_path": repo_path, "commit_sha": commit_sha},
        ))

    async def reembed_repository(self, *, repo_id: str) -> ReembedResult:
        """Backfill real vectors for an ingested repo via POST /ingest/reembed."""
        return ReembedResult.model_validate(await self._post_json(
            "/ingest/reembed", {"repo_id": repo_id},
        ))

    # ----- retrieval -----
    async def retrieve(
        self,
        *,
        text: str,
        repo_id: str,
        top_k: int = 10,
        unit_kinds: list[str] | None = None,
        seed_unit_ids: list[str] | None = None,
    ) -> RetrieveResult:
        """Run hybrid retrieval via POST /retrieve."""
        body: dict[str, Any] = {
            "text": text, "repo_id": repo_id, "top_k": top_k,
        }
        if unit_kinds:
            body["unit_kinds"] = unit_kinds
        if seed_unit_ids:
            body["seed_unit_ids"] = seed_unit_ids
        return RetrieveResult.model_validate(await self._post_json("/retrieve", body))

    # ----- graph -----
    async def query_graph(
        self,
        *,
        node: str,
        repo_id: str,
        depth: int = 1,
    ) -> QueryGraphResult:
        """Bounded BFS via the Phase-5 query_graph MCP tool."""
        result = await self.run_mcp_tool(
            tool="query_graph",
            payload={"node": node, "repo_id": repo_id, "depth": depth},
        )
        if result.status != "success":
            raise MemoryClientError(
                status_code=200, body=result.error or result.error_code,
                url="/mcp/tools/query_graph",
            )
        return QueryGraphResult.model_validate({
            "node": node, **result.data,
        })

    # ----- MCP -----
    async def run_mcp_tool(
        self, *, tool: str, payload: dict[str, Any],
    ) -> McpToolResult:
        """Invoke any of the seven Phase-5 MCP tools via /mcp/tools/{tool}."""
        return McpToolResult.model_validate(
            await self._post_json(f"/mcp/tools/{tool}", payload),
        )

    async def list_mcp_tools(self) -> list[str]:
        body = await self._get_json("/mcp/tools")
        return [t["name"] for t in body.get("tools", [])]

    # ----- snapshot + replay -----
    async def get_snapshot(
        self, *, tenant_id: str, state_version_token: str = "v0",
    ) -> SnapshotResult:
        return SnapshotResult.model_validate(await self._post_json(
            "/snapshot/build",
            {"tenant_id": tenant_id, "state_version_token": state_version_token},
        ))

    async def replay_snapshot(
        self, *, snapshot_id: str, payload: Any,
        expected_output: Any | None = None,
    ) -> ReplayResult:
        return ReplayResult.model_validate(await self._post_json(
            "/snapshot/replay",
            {
                "snapshot_id": snapshot_id,
                "payload": payload,
                "expected_output": expected_output,
            },
        ))

    # ----- status / audit -----
    async def get_status(self) -> StatusResult:
        return StatusResult.model_validate(await self._get_json("/status"))

    async def get_audit_tail(self, *, limit: int = 50) -> dict[str, Any]:
        return await self._get_json("/audit/tail", params={"limit": limit})

    async def verify_audit_chain(self) -> dict[str, Any]:
        return await self._get_json("/audit/verify")

    # ----- internal HTTP plumbing -----
    def _request_id_headers(self) -> dict[str, str]:
        """Compute X-Request-ID for the current call.

        A pinned id (constructor argument) wins over auto-generation.
        Auto-generation produces a fresh uuid4 per request so traces
        in the API logs separate cleanly.
        """
        rid = self._pinned_request_id or str(uuid.uuid4())
        return {_REQUEST_ID_HEADER: rid}

    async def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        resp = await self._client.post(
            path, json=body, headers=self._request_id_headers(),
        )
        return self._parse(resp)

    async def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(
            path, params=params or {}, headers=self._request_id_headers(),
        )
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        if not (200 <= resp.status_code < 300):
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise MemoryClientError(
                status_code=resp.status_code, body=body, url=str(resp.request.url),
            )
        return resp.json()


__all__ = ["AsyncMemoryClient", "MemoryClientError"]
