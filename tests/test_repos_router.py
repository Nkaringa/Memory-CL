from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import repos as repos_router
from apps.api.state import AppState
from storage import RepoSummary


def _build_app(state: AppState) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.app_state = state
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(repos_router.router)
    return app


def _make_state(summaries: list[RepoSummary]) -> AppState:
    units_repo = AsyncMock()
    units_repo.list_repos = AsyncMock(return_value=summaries)
    return AppState.with_default_embedder(
        postgres=AsyncMock(),
        qdrant=AsyncMock(),
        neo4j=AsyncMock(),
        redis=AsyncMock(),
        units_repo=units_repo,
        graph_repo=AsyncMock(),
        vector_repo=AsyncMock(),
        embedding_dimension=32,
    )


def test_repos_endpoint_returns_aggregate_listing() -> None:
    state = _make_state([
        RepoSummary(repo_id="alpha", units=10, files=4,
                    languages=("typescript", "python")),
        RepoSummary(repo_id="beta", units=2, files=1, languages=("python",)),
    ])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos")

    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "1"
    assert body["repos"] == [
        {"repo_id": "alpha", "units": 10, "files": 4,
         "languages": ["python", "typescript"]},
        {"repo_id": "beta", "units": 2, "files": 1, "languages": ["python"]},
    ]
    state.units_repo.list_repos.assert_awaited_once()


def test_repos_endpoint_empty_database() -> None:
    state = _make_state([])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos")

    assert resp.status_code == 200
    assert resp.json() == {"schema_version": "1", "repos": []}
