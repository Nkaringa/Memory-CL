"""Per-tool integration tests.

These exercise the orchestration wrappers against fakes built for the
Phase 1-4 dependencies. Each tool's contract is verified end-to-end
without booting Postgres / Neo4j / Qdrant / Redis.
"""

from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.mcp.registry import build_default_registry
from core.config import get_settings
from core.embeddings import DeterministicEmbedder
from core.mcp.execution import ExecutionContext, ToolExecutor


# ============================================================================
#                              Fakes
# ============================================================================
class _PgConn:
    def __init__(self, rows: list[dict[str, Any]] | Exception) -> None:
        self._rows = rows
        self.last_params: dict[str, Any] | None = None

    async def execute(self, _stmt, params):
        self.last_params = params
        if isinstance(self._rows, Exception):
            raise self._rows
        return _PgResult(self._rows)


class _PgResult:
    def __init__(self, rows): self._rows = rows
    def all(self): return [_RowMapping(r) for r in self._rows]
    def first(self): return _RowMapping(self._rows[0]) if self._rows else None


class _RowMapping:
    """SA-compatible row that exposes both _mapping and tuple subscript."""

    def __init__(self, d: dict[str, Any]) -> None:
        self._mapping = d
        self._tuple = tuple(d.values())

    def __getitem__(self, idx):
        if isinstance(idx, int):
            return self._tuple[idx]
        return self._mapping[idx]


class _PgEngine:
    def __init__(self, rows=None) -> None:
        self._rows = rows or []

    @asynccontextmanager
    async def connect(self):
        yield _PgConn(self._rows)


class _FakeQdrantSearch:
    """Exposes the .search method shape the VectorRetriever expects."""

    def __init__(self, hits: list[Any]) -> None:
        self._hits = hits

    async def search(self, *, collection_name, query_vector, limit,
                     query_filter=None, with_payload=True):
        return self._hits


def _qhit(point_id: str, score: float, qname: str = "pkg.m.f",
          kind: str = "fn") -> SimpleNamespace:
    return SimpleNamespace(
        id=point_id, score=score,
        payload={"kind": kind, "qualified_name": qname,
                 "file_path": "pkg/m.py", "has_vector": True},
    )


def _state(pg_rows=None, qdrant_hits=None, neighbors=None,
           redis_calls: dict | None = None):
    pg_engine = _PgEngine(pg_rows)
    qdrant_search = _FakeQdrantSearch(qdrant_hits or [])

    graph_repo = AsyncMock()
    graph_repo.neighbors = AsyncMock(return_value=neighbors or [])

    vector_repo = AsyncMock()
    vector_repo.ensure_collection = AsyncMock()

    redis_client = AsyncMock()
    redis_client.rpush = AsyncMock()
    redis_client.expire = AsyncMock()
    redis_client.llen = AsyncMock(return_value=1)
    if redis_calls is not None:
        redis_calls["client"] = redis_client

    return SimpleNamespace(
        postgres=SimpleNamespace(engine=pg_engine),
        qdrant=SimpleNamespace(client=qdrant_search),
        neo4j=AsyncMock(),
        redis=SimpleNamespace(client=redis_client),
        units_repo=AsyncMock(),
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedder=DeterministicEmbedder(dimension=32),
    )


def _ctx(state) -> ExecutionContext:
    return ExecutionContext.new(state=state, request_id="rid-test")


@pytest.fixture(autouse=True)
def _settings_cache_clear():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
#                               Tests
# ============================================================================
@pytest.mark.asyncio
async def test_get_context_runs_full_retrieval_path() -> None:
    state = _state(
        qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login", "fn")],
        pg_rows=[],  # metadata channel returns nothing
    )
    registry = build_default_registry()
    resp = await ToolExecutor(registry).execute(
        "get_context",
        {"task": "auth flow", "repo_id": "acme", "top_k": 5},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["packet"]["task"] == "auth flow"
    # Vector channel produced a hit; metadata channel produced none.
    assert resp.data["vector_hits"] >= 1


# ---- get_module_summary ---------------------------------------------------
def _module_row(qname: str, kind: str = "mod", parent=None) -> dict[str, Any]:
    from datetime import UTC, datetime
    return {
        "unit_id": f"u-{qname}", "repo_id": "acme", "commit_sha": "c",
        "kind": kind, "name": qname.rsplit(".", 1)[-1],
        "qualified_name": qname,
        "parent_qualified_name": parent,
        "file_path": "pkg/m.py", "language": "python",
        "line_start": 1, "line_end": 5,
        "content": "def x(): pass\n", "source_sha": "s",
        "docstring": None, "signature": "def x()",
        "imports": ["os"] if kind == "mod" else [],
        "calls": [], "references": [], "bases": [],
        "token_count": 0,
        "schema_version": "1",
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
        "source": "memory-cl", "checksum": None,
    }


@pytest.mark.asyncio
async def test_get_module_summary_returns_dense_module() -> None:
    rows = [
        _module_row("pkg.m", "mod"),
        _module_row("pkg.m.alpha", "fn", parent="pkg.m"),
        _module_row("pkg.m.Beta", "cls", parent="pkg.m"),
    ]
    state = _state(pg_rows=rows)
    resp = await ToolExecutor(build_default_registry()).execute(
        "get_module_summary",
        {"module": "pkg.m", "repo_id": "acme"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    summary = resp.data["summary"]
    assert summary["id"] == "pkg.m"
    assert summary["t"] == "mod"
    assert "alpha" in summary["fn"]
    assert "Beta" in summary["cls"]


@pytest.mark.asyncio
async def test_get_module_summary_handles_unknown_module() -> None:
    state = _state(pg_rows=[])
    resp = await ToolExecutor(build_default_registry()).execute(
        "get_module_summary",
        {"module": "ghost.module", "repo_id": "acme"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["found"] is False


# ---- get_related_components -----------------------------------------------
@pytest.mark.asyncio
async def test_get_related_components_resolves_qname_seed() -> None:
    from schemas import GraphNode, NodeKind

    state = _state(
        pg_rows=[{"unit_id": "u-seed"}],
        neighbors=[
            GraphNode(
                node_id="u-neigh", kind=NodeKind.FUNCTION, repo_id="acme",
                qualified_name="pkg.m.helper", name="helper",
                file_path="pkg/m.py", line_start=1, line_end=2,
                commit_sha="c", source_sha="s",
            ),
        ],
    )
    resp = await ToolExecutor(build_default_registry()).execute(
        "get_related_components",
        {"component": "pkg.m.thing", "repo_id": "acme", "depth": 1},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    related_ids = {r["unit_id"] for r in resp.data["related"]}
    assert "u-neigh" in related_ids
    # Seed itself excluded from `related`.
    assert "u-seed" not in related_ids


@pytest.mark.asyncio
async def test_get_related_components_unknown_qname_returns_empty() -> None:
    state = _state(pg_rows=[])
    resp = await ToolExecutor(build_default_registry()).execute(
        "get_related_components",
        {"component": "nope.q", "repo_id": "acme", "depth": 1},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["found"] is False
    assert resp.data["related"] == []


# ---- get_risks -------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_risks_surfaces_external_neighbors() -> None:
    from schemas import GraphNode, NodeKind

    neighbors = [
        GraphNode(
            node_id="external:numpy", kind=NodeKind.EXTERNAL, repo_id="acme",
            qualified_name="numpy", name="numpy",
        ),
        GraphNode(
            node_id="u-internal", kind=NodeKind.FUNCTION, repo_id="acme",
            qualified_name="pkg.m.helper", name="helper",
            file_path="pkg/m.py", line_start=1, line_end=2,
            commit_sha="c", source_sha="s",
        ),
    ]
    state = _state(pg_rows=[{"unit_id": "u-seed"}], neighbors=neighbors)
    resp = await ToolExecutor(build_default_registry()).execute(
        "get_risks",
        {"entity": "pkg.m.thing", "repo_id": "acme"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["risk_count"] == 1
    assert resp.data["risks"][0]["qualified_name"] == "numpy"


# ---- query_graph ----------------------------------------------------------
@pytest.mark.asyncio
async def test_query_graph_returns_seed_plus_neighbors() -> None:
    from schemas import GraphNode, NodeKind

    neighbors = [
        GraphNode(
            node_id="u-x", kind=NodeKind.FUNCTION, repo_id="acme",
            qualified_name="pkg.m.x", name="x", file_path="f.py",
            line_start=1, line_end=2, commit_sha="c", source_sha="s",
        ),
    ]
    state = _state(pg_rows=[{"unit_id": "u-seed"}], neighbors=neighbors)
    resp = await ToolExecutor(build_default_registry()).execute(
        "query_graph",
        {"node": "pkg.m.thing", "repo_id": "acme", "depth": 2},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    ids = {c["unit_id"] for c in resp.data["candidates"]}
    assert "u-seed" in ids and "u-x" in ids


# ---- update_memory --------------------------------------------------------
@pytest.mark.asyncio
async def test_update_memory_appends_to_redis_list() -> None:
    captured: dict[str, Any] = {}
    state = _state(redis_calls=captured)
    resp = await ToolExecutor(build_default_registry()).execute(
        "update_memory",
        {"session_id": "s1", "repo_id": "acme",
         "session_data": {"k": "v", "n": 1}},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["stored"] is True
    captured["client"].rpush.assert_awaited_once()
    captured["client"].expire.assert_awaited_once()
    args = captured["client"].rpush.call_args.args
    assert args[0] == "mcp:mem:acme:s1"
    # Encoded value is canonical JSON.
    assert "\"k\":\"v\"" in args[1]


# ---- ingest_repository ----------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_repository_runs_pipeline(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(textwrap.dedent("""
        def f(): return 1
    """).lstrip())
    state = _state(pg_rows=[])
    state.units_repo.list_units_for_file = AsyncMock(return_value=[])
    state.units_repo.delete_units_for_file = AsyncMock(return_value=0)
    state.units_repo.upsert_units = AsyncMock(side_effect=lambda u: len(list(u)))
    state.graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    state.graph_repo.upsert_nodes = AsyncMock(side_effect=lambda n: len(list(n)))
    state.graph_repo.upsert_edges = AsyncMock(side_effect=lambda e: len(list(e)))
    state.vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    state.vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, p: len(list(p)))

    resp = await ToolExecutor(build_default_registry()).execute(
        "ingest_repository",
        {"path": str(tmp_path), "repo_id": "acme", "commit_sha": "deadbeef"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    assert resp.data["repo_id"] == "acme"
    assert resp.data["commit_sha"] == "deadbeef"
    assert resp.data["units_collection"] == "repo_acme"
    assert resp.data["metrics"]["units_emitted"] >= 1


@pytest.mark.asyncio
async def test_ingest_repository_wires_embedding_pipeline_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP ingest builds the same Phase-3 embedding stack as the HTTP
    router: changed units get real vectors and the embedder's HTTP
    client is closed after the run."""
    from core.mcp.tools import ingest_tool as ingest_tool_module

    (tmp_path / "m.py").write_text("def f(): return 1\n")
    state = _state(pg_rows=[])
    state.units_repo.list_units_for_file = AsyncMock(return_value=[])
    state.units_repo.delete_units_for_file = AsyncMock(return_value=0)
    state.units_repo.upsert_units = AsyncMock(side_effect=lambda u: len(list(u)))
    state.graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    state.graph_repo.upsert_nodes = AsyncMock(side_effect=lambda n: len(list(n)))
    state.graph_repo.upsert_edges = AsyncMock(side_effect=lambda e: len(list(e)))
    state.vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    state.vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, p: len(list(p)))

    class _FakePipe:
        def __init__(self) -> None:
            self.calls: list[tuple[list[Any], str]] = []

        async def run(self, units, *, collection):
            self.calls.append((list(units), collection))

    fake_pipe = _FakePipe()
    fake_embedder = AsyncMock()
    fake_embedder.dimension = 1536
    monkeypatch.setattr(
        ingest_tool_module,
        "_build_embedding_components",
        lambda vector_repo: (fake_pipe, fake_embedder),
    )

    resp = await ToolExecutor(build_default_registry()).execute(
        "ingest_repository",
        {"path": str(tmp_path), "repo_id": "acme", "commit_sha": "deadbeef"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "success"
    # Fresh repo: every emitted unit got embedded against the repo
    # collection, and the embedder was closed afterwards.
    assert fake_pipe.calls
    assert all(coll == "repo_acme" for _, coll in fake_pipe.calls)
    assert resp.data["metrics"]["units_embedded"] == resp.data["metrics"]["units_emitted"]
    fake_embedder.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_repository_stays_placeholder_only_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OPENAI_API_KEY → the MCP tool builds no embedding stack."""
    from core.mcp.tools import ingest_tool as ingest_tool_module

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert ingest_tool_module._build_embedding_components(AsyncMock()) is None


@pytest.mark.asyncio
async def test_ingest_repository_returns_error_for_missing_path() -> None:
    state = _state()
    resp = await ToolExecutor(build_default_registry()).execute(
        "ingest_repository",
        {"path": "/this/does/not/exist", "repo_id": "acme",
         "commit_sha": "c"},
        ctx=_ctx(state),
    )
    assert resp.status.value == "failed"
    assert resp.error_code.value == "backend_error"


# ---- determinism + registry shape ----------------------------------------
def test_default_registry_exposes_all_seven_tools() -> None:
    registry = build_default_registry()
    assert sorted(registry.names()) == [
        "get_context",
        "get_module_summary",
        "get_related_components",
        "get_risks",
        "ingest_repository",
        "query_graph",
        "update_memory",
    ]


@pytest.mark.asyncio
async def test_get_context_is_deterministic_across_calls() -> None:
    """Same query + same fakes → byte-equal data dict (sans latency)."""
    def make_state():
        return _state(
            qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login")],
            pg_rows=[],
        )

    a = await ToolExecutor(build_default_registry()).execute(
        "get_context",
        {"task": "auth", "repo_id": "acme", "top_k": 3},
        ctx=_ctx(make_state()),
    )
    b = await ToolExecutor(build_default_registry()).execute(
        "get_context",
        {"task": "auth", "repo_id": "acme", "top_k": 3},
        ctx=_ctx(make_state()),
    )
    # latency_ms differs by definition; everything else must match.
    assert a.data == b.data
