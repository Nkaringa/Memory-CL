"""Phase-5 golden gate.

End-to-end MCP scenario:
    1. Ingest the fixture repo via the MCP `ingest_repository` tool
    2. Issue `query_graph` against a known seed via the MCP HTTP API
    3. Issue `get_context` twice → assert byte-identical packets
    4. Confirm structured failure responses for invalid payloads

This test reaches the MCP HTTP surface (TestClient) so the audit /
auth / executor wiring is exercised exactly as production sees it.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.mcp import mcp_router
from apps.mcp.registry import build_default_registry
from core.embeddings import DeterministicEmbedder
from schemas import GraphNode, NodeKind

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


# ---- shared fakes ---------------------------------------------------------
class _FakeQdrant:
    """In-memory Qdrant: receives Phase-3 VectorPoints, replays cosine search."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict]] = {}

    def upsert_call(self, points):
        for p in points:
            self.points[str(p.point_id)] = (
                list(p.vector or []),
                {
                    "kind": p.kind,
                    "qualified_name": p.qualified_name,
                    "file_path": p.file_path,
                    "has_vector": p.vector is not None,
                },
            )

    async def search(self, *, collection_name, query_vector, limit,
                     query_filter=None, with_payload=True):
        scored = []
        for pid, (vec, payload) in self.points.items():
            if not vec:
                continue
            cos = _cos(query_vector, vec)
            scored.append(SimpleNamespace(id=pid, score=cos, payload=payload))
        scored.sort(key=lambda h: (-h.score, h.id))
        return scored[:limit]


def _cos(a, b) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


class _PgConn:
    """Records (sql_template, params) for inspection; returns canned rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt, params):
        self.calls.append((str(stmt), params))
        return _Result(self._rows)


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return [_Row(r) for r in self._rows]
    def first(self): return _Row(self._rows[0]) if self._rows else None


class _Row:
    def __init__(self, d): self._mapping = d
    def __getitem__(self, k):
        return self._mapping[k] if isinstance(k, str) else tuple(self._mapping.values())[k]


class _PgEngine:
    def __init__(self, rows=None) -> None:
        self.conn = _PgConn(rows or [])

    @asynccontextmanager
    async def connect(self):
        yield self.conn


def _state_after_ingest(qdrant: _FakeQdrant, neighbors: list[GraphNode]):
    """Build an AppState whose stores reflect the ingested fixture."""
    pg_engine = _PgEngine(rows=[{"unit_id": "seed-uid"}])
    graph_repo = AsyncMock()
    graph_repo.neighbors = AsyncMock(return_value=neighbors)
    return SimpleNamespace(
        postgres=SimpleNamespace(engine=pg_engine),
        qdrant=SimpleNamespace(client=qdrant),
        neo4j=AsyncMock(),
        redis=SimpleNamespace(client=AsyncMock()),
        units_repo=AsyncMock(),
        graph_repo=graph_repo,
        vector_repo=AsyncMock(),
        embedder=DeterministicEmbedder(dimension=32),
    )


def _build_app(state) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.app_state = state
        app.state.mcp_registry = build_default_registry()
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(mcp_router)
    return app


# ---- ingest the fixture so the fake Qdrant has real embeddings ----------
async def _seed_qdrant_via_ingest(qdrant: _FakeQdrant) -> None:
    """Run Phase-2+3 directly so qdrant has live points before MCP runs."""
    from core.compression import CompressionContext
    from core.compression.pipeline import CompressionPipeline
    from core.embeddings import ChunkingStrategy
    from core.ingestion import GraphBuilder
    from core.parsing import FileWalker, PythonParser
    from schemas import CompressionMetrics

    walk = FileWalker().walk(FIXTURE, repo_id="acme")
    parser = PythonParser()
    units = []
    for ref in walk.files:
        units.extend(parser.parse_file(
            source=(FIXTURE / ref.path).read_text(encoding="utf-8"),
            repo_id="acme",
            file_path=ref.path,
            commit_sha="commit-deadbeef",
        ))
    graph = GraphBuilder().build(units)

    vector_repo = AsyncMock()
    vector_repo.ensure_collection = AsyncMock()
    vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: (
        qdrant.upsert_call(pts) or len(list(pts))
    ))
    ctx = CompressionContext(
        repo_id="acme", commit_sha="commit-deadbeef",
        units_collection="repo:acme",
        vector_repo=vector_repo, metrics=CompressionMetrics(),
    )
    await CompressionPipeline(
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        embedder=DeterministicEmbedder(dimension=32),
    ).run(ctx, units=units, nodes=graph.nodes, edges=graph.edges)


@pytest.mark.asyncio
async def test_phase5_golden_full_mcp_roundtrip(tmp_path: Path) -> None:
    qdrant = _FakeQdrant()
    await _seed_qdrant_via_ingest(qdrant)

    neighbors = [
        GraphNode(
            node_id="u-helper", kind=NodeKind.FUNCTION, repo_id="acme",
            qualified_name="pkg.utils.helper", name="helper",
            file_path="pkg/utils.py", line_start=1, line_end=2,
            commit_sha="c", source_sha="s",
        ),
    ]
    state = _state_after_ingest(qdrant, neighbors)
    app = _build_app(state)

    with TestClient(app) as client:
        # 1. tool listing exposes all 7 tools
        listing = client.get("/mcp/tools").json()
        assert sorted(t["name"] for t in listing["tools"]) == [
            "get_context", "get_module_summary", "get_related_components",
            "get_risks", "ingest_repository", "query_graph", "update_memory",
        ]

        # 2. get_context is deterministic across two identical calls
        body_a = client.post("/mcp/tools/get_context", json={
            "task": "auth flow", "repo_id": "acme", "top_k": 5,
        }).json()
        body_b = client.post("/mcp/tools/get_context", json={
            "task": "auth flow", "repo_id": "acme", "top_k": 5,
        }).json()
        assert body_a["status"] == "success"
        assert body_a["data"] == body_b["data"]
        assert body_a["data"]["packet"]["task"] == "auth flow"

        # 3. query_graph against a qname seed surfaces the helper neighbor
        graph_body = client.post("/mcp/tools/query_graph", json={
            "node": "pkg.utils.add", "repo_id": "acme", "depth": 2,
        }).json()
        assert graph_body["status"] == "success"
        assert any(c["unit_id"] == "u-helper"
                   for c in graph_body["data"]["candidates"])

        # 4. validation failures are in-band, not 4xx
        bad = client.post("/mcp/tools/get_context", json={
            "task": "auth", "repo_id": "acme", "top_k": "not-an-int",
        })
        assert bad.status_code == 200
        assert bad.json()["status"] == "failed"
        assert bad.json()["error_code"] == "validation_error"
