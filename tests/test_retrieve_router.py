from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import retrieve as retrieve_router
from apps.api.state import AppState
from schemas import GraphNode, NodeKind


def _qhit(point_id: str, score: float, qname: str = "pkg.m.f",
          kind: str = "fn") -> SimpleNamespace:
    return SimpleNamespace(
        id=point_id, score=score,
        payload={
            "kind": kind, "qualified_name": qname,
            "file_path": "pkg/m.py", "has_vector": True,
        },
    )


def _row(unit_id: str, **kw) -> dict:
    return {
        "unit_id": unit_id, "repo_id": "r", "qualified_name": kw.get("qn", "pkg.m.f"),
        "kind": "fn", "file_path": "pkg/m.py",
        "line_start": 1, "line_end": 5, "source_sha": "s", "updated_at": None,
    }


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _PgConn:
    async def execute(self, _stmt, _params):
        return _Result([_row("u-meta")])


class _PgEngine:
    @asynccontextmanager
    async def connect(self):
        yield _PgConn()


def _make_state() -> AppState:
    # Phase-2/3 storage clients are stubbed out — we only exercise the
    # retrieval orchestration layer.
    pg_client = SimpleNamespace(engine=_PgEngine())

    qdrant_client = AsyncMock()
    qdrant_client.search = AsyncMock(return_value=[
        _qhit("u-vec", 0.9), _qhit("u-shared", 0.7),
    ])
    qd_client = SimpleNamespace(client=qdrant_client)

    graph_repo = AsyncMock()
    graph_repo.neighbors = AsyncMock(return_value=[
        GraphNode(node_id="u-graph", kind=NodeKind.FUNCTION, repo_id="r",
                  qualified_name="pkg.m.g", name="g", file_path="pkg/m.py",
                  line_start=1, line_end=2, commit_sha="c", source_sha="s"),
    ])

    return AppState.with_default_embedder(
        postgres=pg_client,  # type: ignore[arg-type]
        qdrant=qd_client,    # type: ignore[arg-type]
        neo4j=AsyncMock(),
        redis=AsyncMock(),
        units_repo=AsyncMock(),
        graph_repo=graph_repo,
        vector_repo=AsyncMock(),
        embedding_dimension=32,
    )


def _build_app(state: AppState) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.app_state = state
        yield
    app = FastAPI(lifespan=_ls)
    app.include_router(retrieve_router.router)
    return app


def test_retrieve_endpoint_returns_packet_and_per_channel_hits() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve",
            json={"text": "auth flow", "repo_id": "r", "top_k": 5},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_id"] == "r"
    assert body["query_id"]  # non-empty deterministic id
    assert body["vector_hits"] >= 1
    assert body["metadata_hits"] >= 1
    # No graph seeds were supplied -> graph channel skipped.
    assert body["graph_hits"] == 0
    assert body["packet"]["task"] == "auth flow"
    assert isinstance(body["packet"]["context"], list)
    assert body["packet"]["confidence"] >= 0.0


def test_retrieve_with_graph_seeds_invokes_graph_channel() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve",
            json={
                "text": "auth flow", "repo_id": "r", "top_k": 5,
                "seed_unit_ids": ["seed1"],
            },
        )

    body = resp.json()
    assert body["graph_hits"] >= 1
    state.graph_repo.neighbors.assert_awaited()


def test_retrieve_is_deterministic_across_calls() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        a = client.post("/retrieve",
                        json={"text": "x", "repo_id": "r", "top_k": 5}).json()
        b = client.post("/retrieve",
                        json={"text": "x", "repo_id": "r", "top_k": 5}).json()

    # Strip latency_ms (timing-dependent) and compare the rest.
    a.pop("latency_ms"), b.pop("latency_ms")
    a["packet"], b["packet"] = a["packet"], b["packet"]
    assert a == b


def test_retrieve_request_validation() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        # Missing required text field.
        bad = client.post("/retrieve", json={"repo_id": "r"})
        assert bad.status_code == 422
        # Extra forbidden field.
        bad2 = client.post(
            "/retrieve",
            json={"text": "x", "repo_id": "r", "rogue": True},
        )
        assert bad2.status_code == 422


@pytest.mark.asyncio
async def test_retrieve_degrades_on_channel_failure() -> None:
    state = _make_state()
    state.qdrant.client.search = AsyncMock(side_effect=RuntimeError("qdrant down"))
    app = _build_app(state)

    with TestClient(app) as client:
        body = client.post("/retrieve", json={
            "text": "x", "repo_id": "r", "top_k": 5,
        }).json()

    # Vector channel failed silently; metadata channel still produced output.
    assert body["vector_hits"] == 0
    assert body["metadata_hits"] >= 1
