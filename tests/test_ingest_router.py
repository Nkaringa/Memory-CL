from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import ingest as ingest_router
from apps.api.state import AppState


def _build_app(state: AppState) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.app_state = state
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(ingest_router.router)
    return app


def _make_state(*, vector_repo_extra: AsyncMock | None = None) -> AppState:
    units_repo = AsyncMock()
    units_repo.list_units_for_file = AsyncMock(return_value=[])
    units_repo.delete_units_for_file = AsyncMock(return_value=0)
    units_repo.upsert_units = AsyncMock(side_effect=lambda u: len(list(u)))
    units_repo.ensure_schema = AsyncMock()

    graph_repo = AsyncMock()
    graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    graph_repo.upsert_nodes = AsyncMock(side_effect=lambda n: len(list(n)))
    graph_repo.upsert_edges = AsyncMock(side_effect=lambda e: len(list(e)))
    graph_repo.ensure_constraints = AsyncMock()

    vector_repo = vector_repo_extra or AsyncMock()
    vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, p: len(list(p)))
    vector_repo.ensure_collection = AsyncMock()

    return AppState.with_default_embedder(
        postgres=AsyncMock(),
        qdrant=AsyncMock(),
        neo4j=AsyncMock(),
        redis=AsyncMock(),
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedding_dimension=32,
    )


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text(textwrap.dedent("""
        def f(): return 1
        def g(): return f()
    """).lstrip())
    return tmp_path


def test_ingest_endpoint_runs_pipeline(tmp_path: Path) -> None:
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_id"] == "tenant-1"
    assert body["commit_sha"] == "abc123"
    assert body["units_collection"] == "repo_tenant-1"
    assert body["metrics"]["files_walked"] >= 1
    assert body["metrics"]["units_emitted"] >= 1
    assert body["failed_files"] == []

    # ensure_collection was called by the endpoint with the per-repo name.
    state.vector_repo.ensure_collection.assert_awaited_once()
    name_arg = state.vector_repo.ensure_collection.await_args.args[0]
    assert name_arg == "repo_tenant-1"


def test_ingest_rejects_bad_repo_path(tmp_path: Path) -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "r",
                "repo_path": str(tmp_path / "does-not-exist"),
                "commit_sha": "c",
            },
        )

    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"]


def test_ingest_request_validation() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        # Missing required field.
        resp = client.post("/ingest", json={"repo_id": "r"})
        assert resp.status_code == 422

        # Extra field rejected (extra="forbid" on the request schema).
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "r",
                "repo_path": "/tmp",
                "commit_sha": "c",
                "rogue_field": "x",
            },
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_endpoint_propagates_failed_files(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def f(): pass\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "t",
                "repo_path": str(tmp_path),
                "commit_sha": "c",
            },
        )

    assert resp.status_code == 200
    assert "bad.py" in resp.json()["failed_files"]
