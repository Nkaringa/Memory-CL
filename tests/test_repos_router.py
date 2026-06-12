from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import repos as repos_router
from apps.api.state import AppState
from schemas import GraphNode, NodeKind
from storage import QnameMatch, RepoSummary


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


# ---------- GET /repos/{repo_id}/qnames ----------
def _make_qnames_state(matches: list[QnameMatch]) -> AppState:
    state = _make_state([])
    state.units_repo.search_qnames = AsyncMock(return_value=matches)
    return state


def test_qnames_endpoint_returns_matches() -> None:
    state = _make_qnames_state([
        QnameMatch(qualified_name="app.ats.scorer", kind="fn"),
        QnameMatch(qualified_name="tests.app.test_ats_scorer", kind="module"),
    ])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/my-repo/qnames", params={"q": "scorer"})

    assert resp.status_code == 200
    assert resp.json() == {
        "repo_id": "my-repo",
        "matches": [
            {"qualified_name": "app.ats.scorer", "kind": "fn"},
            {"qualified_name": "tests.app.test_ats_scorer", "kind": "module"},
        ],
    }
    state.units_repo.search_qnames.assert_awaited_once_with(
        "my-repo", "scorer", limit=20
    )


def test_qnames_endpoint_no_matches() -> None:
    state = _make_qnames_state([])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/my-repo/qnames", params={"q": "nope"})

    assert resp.status_code == 200
    assert resp.json() == {"repo_id": "my-repo", "matches": []}


def test_qnames_endpoint_rejects_empty_or_missing_q() -> None:
    state = _make_qnames_state([])
    app = _build_app(state)

    with TestClient(app) as client:
        assert client.get("/repos/r/qnames", params={"q": ""}).status_code == 422
        assert client.get("/repos/r/qnames").status_code == 422
    state.units_repo.search_qnames.assert_not_awaited()


def test_qnames_endpoint_clamps_limit_to_100() -> None:
    state = _make_qnames_state([])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get(
            "/repos/r/qnames", params={"q": "scorer", "limit": 5000}
        )

    assert resp.status_code == 200
    state.units_repo.search_qnames.assert_awaited_once_with(
        "r", "scorer", limit=100
    )


def test_qnames_endpoint_rejects_nonpositive_limit() -> None:
    state = _make_qnames_state([])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/r/qnames", params={"q": "scorer", "limit": 0})

    assert resp.status_code == 422
    state.units_repo.search_qnames.assert_not_awaited()


# ---------- GET /repos/{repo_id}/graph ----------
def _gnode(node_id: str, kind: NodeKind = NodeKind.FUNCTION) -> GraphNode:
    external = kind == NodeKind.EXTERNAL
    return GraphNode(
        node_id=node_id,
        kind=kind,
        repo_id="my-repo",
        qualified_name=f"pkg.{node_id}",
        name=node_id,
        file_path=None if external else "pkg/m.py",
        line_start=None if external else 1,
        line_end=None if external else 9,
    )


def _make_graph_state(
    nodes: list[GraphNode], edges: list[tuple[str, str, str]]
) -> AppState:
    state = _make_state([])
    state.graph_repo.repo_graph = AsyncMock(return_value=(nodes, edges))
    return state


def test_graph_endpoint_returns_nodes_edges_and_counts() -> None:
    state = _make_graph_state(
        [_gnode("a"), _gnode("ext1", NodeKind.EXTERNAL)],
        [("a", "CALLS", "ext1")],
    )
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/my-repo/graph")

    assert resp.status_code == 200
    assert resp.json() == {
        "repo_id": "my-repo",
        "truncated": False,
        "nodes": [
            {"node_id": "a", "kind": "Function", "qualified_name": "pkg.a",
             "name": "a", "file_path": "pkg/m.py", "line_start": 1,
             "line_end": 9},
            {"node_id": "ext1", "kind": "External",
             "qualified_name": "pkg.ext1", "name": "ext1",
             "file_path": None, "line_start": None, "line_end": None},
        ],
        "edges": [{"src_id": "a", "kind": "CALLS", "dst_id": "ext1"}],
        "counts": {"nodes": 2, "edges": 1},
    }
    state.graph_repo.repo_graph.assert_awaited_once_with(
        "my-repo", include_external=False, max_nodes=5000
    )


def test_graph_endpoint_truncated_when_node_count_hits_max() -> None:
    state = _make_graph_state([_gnode("a"), _gnode("b")], [])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/my-repo/graph", params={"max_nodes": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert body["counts"] == {"nodes": 2, "edges": 0}
    state.graph_repo.repo_graph.assert_awaited_once_with(
        "my-repo", include_external=False, max_nodes=2
    )


def test_graph_endpoint_passes_include_external_through() -> None:
    state = _make_graph_state([], [])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get(
            "/repos/my-repo/graph", params={"include_external": "true"}
        )

    assert resp.status_code == 200
    state.graph_repo.repo_graph.assert_awaited_once_with(
        "my-repo", include_external=True, max_nodes=5000
    )


def test_graph_endpoint_empty_repo() -> None:
    state = _make_graph_state([], [])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/ghost/graph")

    assert resp.status_code == 200
    assert resp.json() == {
        "repo_id": "ghost", "truncated": False, "nodes": [], "edges": [],
        "counts": {"nodes": 0, "edges": 0},
    }


def test_graph_endpoint_clamps_max_nodes_to_20000() -> None:
    state = _make_graph_state([], [])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/r/graph", params={"max_nodes": 50_000})

    assert resp.status_code == 200
    state.graph_repo.repo_graph.assert_awaited_once_with(
        "r", include_external=False, max_nodes=20_000
    )


def test_graph_endpoint_rejects_nonpositive_max_nodes() -> None:
    state = _make_graph_state([], [])
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.get("/repos/r/graph", params={"max_nodes": 0})

    assert resp.status_code == 422
    state.graph_repo.repo_graph.assert_not_awaited()
