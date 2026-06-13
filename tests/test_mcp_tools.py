"""Per-tool integration tests for the v2 agent-facing MCP surface.

These exercise the orchestration wrappers against fakes built for the
Phase 1-4 dependencies. Each tool's contract (happy / miss / error) is
verified end-to-end without booting Postgres / Neo4j / Qdrant / Redis.
"""

from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.mcp.registry import build_default_registry
from core.config import get_settings
from core.embeddings import DeterministicEmbedder
from core.mcp.execution import ExecutionContext, ToolExecutor
from schemas import GraphNode, IngestionUnit, Language, NodeKind, UnitKind
from storage.repositories import QnameMatch, RepoSummary


# ============================================================================
#                              Fakes
# ============================================================================
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


# Route names → predicate over the (whitespace-normalized) SQL text.
# Each v2 tool issues a distinctive read-only query; tests register the
# rows each query should return.
_ROUTE_PREDICATES = {
    "qname": lambda sql: ":qname" in sql and ":prefix" not in sql,
    "module": lambda sql: ":prefix" in sql,
    "symbol": lambda sql: "length(qualified_name)" in sql,
    "paths": lambda sql: "DISTINCT file_path" in sql,
    "overview": lambda sql: sql.startswith(
        "SELECT qualified_name, kind, file_path, language"
    ),
    "metadata": lambda sql: ":no_kind_filter" in sql,
}


class _RoutingConn:
    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        for name, rows in self._routes.items():
            if _ROUTE_PREDICATES[name](sql):
                if callable(rows):
                    rows = rows(params)
                if isinstance(rows, Exception):
                    raise rows
                return _PgResult(rows)
        return _PgResult([])


class _PgEngine:
    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self._routes = routes or {}

    @asynccontextmanager
    async def connect(self):
        yield _RoutingConn(self._routes)


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


def _unit(
    qname: str,
    *,
    kind: str = "fn",
    unit_id: str | None = None,
    repo_id: str = "acme",
    file_path: str = "pkg/m.py",
    content: str = "def x():\n    return 1\n",
    parent: str | None = None,
    lines: tuple[int, int] = (1, 5),
    signature: str | None = "def x()",
) -> IngestionUnit:
    return IngestionUnit(
        unit_id=unit_id or f"u-{qname}",
        repo_id=repo_id,
        commit_sha="c",
        kind=UnitKind(kind),
        name=qname.rsplit(".", 1)[-1],
        qualified_name=qname,
        parent_qualified_name=parent,
        file_path=file_path,
        language=Language.PYTHON,
        line_start=lines[0],
        line_end=lines[1],
        content=content,
        source_sha="s",
        signature=signature,
    )


def _unit_row(unit: IngestionUnit) -> dict[str, Any]:
    """SQL row dict matching the full ingestion_units column set."""
    return {
        "unit_id": unit.unit_id, "repo_id": unit.repo_id,
        "commit_sha": unit.commit_sha, "kind": unit.kind.value,
        "name": unit.name, "qualified_name": unit.qualified_name,
        "parent_qualified_name": unit.parent_qualified_name,
        "file_path": unit.file_path, "language": unit.language.value,
        "line_start": unit.line_start, "line_end": unit.line_end,
        "content": unit.content, "source_sha": unit.source_sha,
        "docstring": unit.docstring, "signature": unit.signature,
        "imports": list(unit.imports), "calls": list(unit.calls),
        "references": list(unit.references), "bases": list(unit.bases),
        "token_count": unit.token_count, "schema_version": "1",
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
        "source": "memory-cl", "checksum": None,
    }


def _gnode(node_id: str, qname: str, kind: NodeKind = NodeKind.FUNCTION,
           file_path: str | None = "pkg/m.py") -> GraphNode:
    return GraphNode(
        node_id=node_id, kind=kind, repo_id="acme",
        qualified_name=qname, name=qname.rsplit(".", 1)[-1],
        file_path=file_path,
        line_start=1 if file_path else None,
        line_end=2 if file_path else None,
        commit_sha="c" if file_path else None,
        source_sha="s" if file_path else None,
    )


_ACME = RepoSummary(repo_id="acme", units=3, files=2, languages=("python",))


def _state(
    *,
    routes: dict[str, Any] | None = None,
    qdrant_hits: list[Any] | None = None,
    neighbors: list[GraphNode] | None = None,
    edges: list[tuple[str, str, str]] | None = None,
    repos: list[RepoSummary] | None = None,
    units: list[IngestionUnit] | None = None,
    units_for_file: list[IngestionUnit] | None = None,
    qname_matches: list[QnameMatch] | None = None,
    redis_calls: dict | None = None,
):
    pg_engine = _PgEngine(routes)
    qdrant_search = _FakeQdrantSearch(qdrant_hits or [])

    graph_repo = AsyncMock()
    graph_repo.neighbors = AsyncMock(return_value=neighbors or [])
    graph_repo.edges_among = AsyncMock(return_value=edges or [])
    graph_repo.repo_graph = AsyncMock(return_value=([], []))

    units_by_id = {u.unit_id: u for u in (units or [])}
    units_repo = AsyncMock()
    units_repo.get_unit = AsyncMock(
        side_effect=lambda uid: units_by_id.get(uid)
    )
    units_repo.list_repos = AsyncMock(
        return_value=repos if repos is not None else [_ACME]
    )
    units_repo.search_qnames = AsyncMock(return_value=qname_matches or [])
    units_repo.list_units_for_file = AsyncMock(
        return_value=units_for_file or []
    )

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
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedder=DeterministicEmbedder(dimension=32),
    )


def _ctx(state) -> ExecutionContext:
    return ExecutionContext.new(state=state, request_id="rid-test")


async def _run(state, tool: str, payload: dict[str, Any]):
    return await ToolExecutor(build_default_registry()).execute(
        tool, payload, ctx=_ctx(state)
    )


@pytest.fixture(autouse=True)
def _settings_cache_clear():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
#                               search_code
# ============================================================================
@pytest.mark.asyncio
async def test_search_code_returns_content_bearing_hits() -> None:
    login = _unit("pkg.m.login", unit_id="u1",
                  content="def login(user):\n    return token\n")
    state = _state(
        qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login")],
        units=[login],
    )
    resp = await _run(state, "search_code",
                      {"question": "auth flow", "repo_id": "acme"})
    assert resp.status.value == "success"
    (hit,) = resp.data["results"]
    assert hit["qualified_name"] == "pkg.m.login"
    assert hit["repo_id"] == "acme"
    assert hit["kind"] == "fn"
    assert hit["file_path"] == "pkg/m.py"
    assert hit["lines"] == "1-5"
    assert "def login(user):" in hit["snippet"]
    assert hit["snippet_truncated"] is False
    assert "vector" in hit["channels"]
    assert resp.data["truncated"] is False


@pytest.mark.asyncio
async def test_search_code_caps_snippets_at_40_lines() -> None:
    big = _unit("pkg.m.big", unit_id="u1",
                content="\n".join(f"line{i}" for i in range(60)),
                lines=(1, 60))
    state = _state(qdrant_hits=[_qhit("u1", 0.9, "pkg.m.big")], units=[big])
    resp = await _run(state, "search_code",
                      {"question": "big", "repo_id": "acme"})
    (hit,) = resp.data["results"]
    assert hit["snippet_truncated"] is True
    assert len(hit["snippet"].splitlines()) == 40


@pytest.mark.asyncio
async def test_search_code_fans_in_across_all_repos_when_repo_omitted() -> None:
    login = _unit("pkg.m.login", unit_id="u1")
    state = _state(
        qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login")],
        units=[login],
        repos=[
            RepoSummary("beta", 1, 1, ("python",)),
            RepoSummary("acme", 3, 2, ("python",)),
        ],
    )
    resp = await _run(state, "search_code", {"question": "auth"})
    assert resp.status.value == "success"
    assert {h["repo_id"] for h in resp.data["results"]} == {"acme", "beta"}


@pytest.mark.asyncio
async def test_search_code_unknown_repo_lists_valid_ids() -> None:
    state = _state()
    resp = await _run(state, "search_code",
                      {"question": "auth", "repo_id": "ghost"})
    assert resp.status.value == "success"
    assert resp.data["found"] is False
    assert resp.data["valid_repo_ids"] == ["acme"]
    assert "list_repos" in resp.data["hint"]
    assert resp.data["results"] == []


@pytest.mark.asyncio
async def test_search_code_empty_results_suggest_next_tools() -> None:
    state = _state()
    resp = await _run(state, "search_code",
                      {"question": "nonexistent thing", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["results"] == []
    assert "find_symbol" in resp.data["hint"]


@pytest.mark.asyncio
async def test_search_code_no_repos_ingested_teaches_ingest() -> None:
    state = _state(repos=[])
    resp = await _run(state, "search_code", {"question": "anything"})
    assert resp.status.value == "success"
    assert "ingest_repository" in resp.data["hint"]


@pytest.mark.asyncio
async def test_search_code_is_deterministic_across_calls() -> None:
    def make_state():
        return _state(
            qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login")],
            units=[_unit("pkg.m.login", unit_id="u1")],
        )

    a = await _run(make_state(), "search_code",
                   {"question": "auth", "repo_id": "acme", "top_k": 3})
    b = await _run(make_state(), "search_code",
                   {"question": "auth", "repo_id": "acme", "top_k": 3})
    assert a.data == b.data


# ============================================================================
#                               read_unit
# ============================================================================
def _qname_route(*units: IngestionUnit):
    rows = {u.qualified_name: [_unit_row(u)] for u in units}
    return lambda p: rows.get(p["qname"], [])


@pytest.mark.asyncio
async def test_read_unit_by_qualified_name_returns_full_unit() -> None:
    method = _unit("pkg.m.C.run", kind="mth", parent="pkg.m.C",
                   content="def run(self):\n    return 1\n")
    klass = _unit("pkg.m.C", kind="cls", parent="pkg.m", signature=None)
    module = _unit("pkg.m", kind="mod", signature=None)
    state = _state(routes={"qname": _qname_route(method, klass, module)})
    resp = await _run(state, "read_unit",
                      {"reference": "pkg.m.C.run", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is True
    assert resp.data["qualified_name"] == "pkg.m.C.run"
    assert resp.data["content"].startswith("def run(self):")
    assert resp.data["signature"] == "def x()"
    assert resp.data["truncated"] is False
    # Parent chain walks method → class → module.
    chain = [p["qualified_name"] for p in resp.data["parent_chain"]]
    assert chain == ["pkg.m.C", "pkg.m"]


@pytest.mark.asyncio
async def test_read_unit_by_unit_id() -> None:
    uid = "a" * 64
    unit = _unit("pkg.m.f", unit_id=uid)
    state = _state(units=[unit])
    resp = await _run(state, "read_unit", {"reference": uid, "repo_id": "acme"})
    assert resp.data["found"] is True
    assert resp.data["qualified_name"] == "pkg.m.f"


@pytest.mark.asyncio
async def test_read_unit_by_file_path_prefers_module_unit() -> None:
    module = _unit("pkg.m", kind="mod", lines=(1, 50),
                   content="FULL FILE SOURCE\n", signature=None)
    fn = _unit("pkg.m.f", lines=(3, 7))
    state = _state(units_for_file=[fn, module])
    resp = await _run(state, "read_unit",
                      {"reference": "pkg/m.py", "repo_id": "acme"})
    assert resp.data["found"] is True
    assert resp.data["qualified_name"] == "pkg.m"
    assert resp.data["content"] == "FULL FILE SOURCE\n"


@pytest.mark.asyncio
async def test_read_unit_miss_suggests_closest_qnames() -> None:
    state = _state(
        qname_matches=[QnameMatch("pkg.m.login", "fn")],
    )
    resp = await _run(state, "read_unit",
                      {"reference": "pkg.m.loginn", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is False
    assert resp.data["suggestions"][0]["qualified_name"] == "pkg.m.login"
    assert "suggestions" in resp.data["hint"]


@pytest.mark.asyncio
async def test_read_unit_unknown_repo_lists_valid_ids() -> None:
    state = _state()
    resp = await _run(state, "read_unit",
                      {"reference": "pkg.m.f", "repo_id": "ghost"})
    assert resp.data["found"] is False
    assert resp.data["valid_repo_ids"] == ["acme"]


@pytest.mark.asyncio
async def test_read_unit_token_caps_giant_content() -> None:
    giant = _unit("pkg.m.big", content="x = 1\n" * 20_000, lines=(1, 20_000))
    state = _state(routes={"qname": _qname_route(giant)})
    resp = await _run(state, "read_unit",
                      {"reference": "pkg.m.big", "repo_id": "acme"})
    assert resp.data["found"] is True
    assert resp.data["truncated"] is True
    assert len(resp.data["content"]) < len(giant.content)


# ============================================================================
#                               explore
# ============================================================================
def _explore_state(**kw):
    seed = _unit("pkg.m.seedfn", unit_id="u-seed")
    callee = _unit("pkg.m.callee", unit_id="u-callee",
                   content="def callee():\n    pass\n")
    caller = _unit("pkg.m.caller", unit_id="u-caller",
                   content="def caller():\n    seedfn()\n")
    return _state(
        routes={"qname": _qname_route(seed)},
        units=[seed, callee, caller],
        neighbors=[
            _gnode("u-callee", "pkg.m.callee"),
            _gnode("u-caller", "pkg.m.caller"),
        ],
        edges=[
            ("u-seed", "CALLS", "u-callee"),
            ("u-caller", "CALLS", "u-seed"),
        ],
        **kw,
    )


@pytest.mark.asyncio
async def test_explore_callees_follows_outgoing_calls_only() -> None:
    resp = await _run(_explore_state(), "explore",
                      {"qualified_name": "pkg.m.seedfn", "repo_id": "acme",
                       "direction": "callees"})
    assert resp.status.value == "success"
    assert resp.data["found"] is True
    (n,) = resp.data["neighbors"]
    assert n["qualified_name"] == "pkg.m.callee"
    assert n["relation"] == "CALLS ->"
    assert n["distance"] == 1
    assert n["snippet"] == "def callee():"
    assert n["signature"] == "def x()"
    assert n["lines"] == "1-5"


@pytest.mark.asyncio
async def test_explore_callers_follows_incoming_calls_only() -> None:
    resp = await _run(_explore_state(), "explore",
                      {"qualified_name": "pkg.m.seedfn", "repo_id": "acme",
                       "direction": "callers"})
    (n,) = resp.data["neighbors"]
    assert n["qualified_name"] == "pkg.m.caller"
    assert n["relation"] == "CALLS <-"


@pytest.mark.asyncio
async def test_explore_all_returns_both_with_edges() -> None:
    resp = await _run(_explore_state(), "explore",
                      {"qualified_name": "pkg.m.seedfn", "repo_id": "acme"})
    names = [n["qualified_name"] for n in resp.data["neighbors"]]
    assert names == ["pkg.m.callee", "pkg.m.caller"]
    assert resp.data["seed"]["qualified_name"] == "pkg.m.seedfn"
    assert {"src_id": "u-seed", "kind": "CALLS", "dst_id": "u-callee"} in \
        resp.data["edges"]


@pytest.mark.asyncio
async def test_explore_imports_surfaces_external_nodes() -> None:
    seed = _unit("pkg.m", kind="mod", unit_id="u-seed", signature=None)
    state = _state(
        routes={"qname": _qname_route(seed)},
        units=[seed],
        neighbors=[_gnode("external:numpy", "numpy",
                          kind=NodeKind.EXTERNAL, file_path=None)],
        edges=[("u-seed", "IMPORTS", "external:numpy")],
    )
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m", "repo_id": "acme",
                       "direction": "imports"})
    (n,) = resp.data["neighbors"]
    assert n["qualified_name"] == "numpy"
    assert n["kind"] == "External"


@pytest.mark.asyncio
async def test_explore_unknown_symbol_suggests_closest_qnames() -> None:
    state = _state(qname_matches=[QnameMatch("pkg.m.seedfn", "fn")])
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m.sedfn", "repo_id": "acme"})
    assert resp.data["found"] is False
    assert resp.data["suggestions"][0]["qualified_name"] == "pkg.m.seedfn"


@pytest.mark.asyncio
async def test_explore_degrades_when_edges_among_unavailable() -> None:
    state = _explore_state()
    del state.graph_repo.edges_among
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m.seedfn", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert "warning" in resp.data
    # direction=all degrades to undirected connectivity.
    assert {n["qualified_name"] for n in resp.data["neighbors"]} == \
        {"pkg.m.callee", "pkg.m.caller"}
    assert all(n["relation"] == "connected" for n in resp.data["neighbors"])


@pytest.mark.asyncio
async def test_explore_no_neighbors_hints_next_steps() -> None:
    seed = _unit("pkg.m.lonely", unit_id="u-seed")
    state = _state(routes={"qname": _qname_route(seed)}, units=[seed])
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m.lonely", "repo_id": "acme",
                       "direction": "callers"})
    assert resp.data["found"] is True
    assert resp.data["neighbors"] == []
    assert "hint" in resp.data


# ============================================================================
#                               find_symbol
# ============================================================================
def _symbol_rows(*units: IngestionUnit):
    return [
        {"unit_id": u.unit_id, "qualified_name": u.qualified_name,
         "kind": u.kind.value, "file_path": u.file_path,
         "line_start": u.line_start, "line_end": u.line_end}
        for u in units
    ]


@pytest.mark.asyncio
async def test_find_symbol_returns_enriched_matches() -> None:
    state = _state(routes={"symbol": _symbol_rows(
        _unit("pkg.m.login"), _unit("pkg.auth.login_helper"),
    )})
    resp = await _run(state, "find_symbol",
                      {"query": "login", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert [m["qualified_name"] for m in resp.data["matches"]] == \
        ["pkg.m.login", "pkg.auth.login_helper"]
    m = resp.data["matches"][0]
    assert m["kind"] == "fn"
    assert m["file_path"] == "pkg/m.py"
    assert m["lines"] == "1-5"
    assert m["repo_id"] == "acme"


@pytest.mark.asyncio
async def test_find_symbol_unknown_repo_lists_valid_ids() -> None:
    state = _state()
    resp = await _run(state, "find_symbol",
                      {"query": "login", "repo_id": "ghost"})
    assert resp.data["matches"] == []
    assert resp.data["valid_repo_ids"] == ["acme"]


@pytest.mark.asyncio
async def test_find_symbol_empty_hints_alternatives() -> None:
    state = _state()
    resp = await _run(state, "find_symbol",
                      {"query": "zzz", "repo_id": "acme"})
    assert resp.data["matches"] == []
    assert "search_code" in resp.data["hint"]


@pytest.mark.asyncio
async def test_find_symbol_truncated_flag_fires_at_limit() -> None:
    """30 fake matches, limit=10 → truncated=True, exactly 10 returned."""
    units_30 = [
        _unit(f"pkg.m.fn{i:02d}", unit_id=f"u{i:02d}") for i in range(30)
    ]
    state = _state(routes={"symbol": _symbol_rows(*units_30)})
    resp = await _run(state, "find_symbol",
                      {"query": "fn", "repo_id": "acme", "limit": 10})
    assert resp.status.value == "success"
    assert resp.data["truncated"] is True
    assert len(resp.data["matches"]) == 10


# ============================================================================
#                               list_repos
# ============================================================================
@pytest.mark.asyncio
async def test_list_repos_returns_sorted_repos_with_hint() -> None:
    state = _state(repos=[
        RepoSummary("zeta", 9, 3, ("go",)),
        RepoSummary("acme", 3, 2, ("python", "markdown")),
    ])
    resp = await _run(state, "list_repos", {})
    assert resp.status.value == "success"
    assert [r["repo_id"] for r in resp.data["repos"]] == ["acme", "zeta"]
    assert resp.data["repos"][0]["languages"] == ["markdown", "python"]
    assert "repo_overview" in resp.data["hint"]


@pytest.mark.asyncio
async def test_list_repos_empty_teaches_ingest() -> None:
    state = _state(repos=[])
    resp = await _run(state, "list_repos", {})
    assert resp.data["repos"] == []
    assert "ingest_repository" in resp.data["hint"]


# ============================================================================
#                               repo_overview
# ============================================================================
def _overview_rows() -> list[dict[str, Any]]:
    def row(qname, kind, file_path, language="python", ls=1, le=10):
        return {"qualified_name": qname, "kind": kind, "file_path": file_path,
                "language": language, "line_start": ls, "line_end": le}
    return [
        row("pkg", "mod", "pkg/__init__.py"),
        row("pkg.m", "mod", "pkg/m.py"),
        row("pkg.m.f", "fn", "pkg/m.py"),
        row("pkg.m.C", "cls", "pkg/m.py"),
        row("README", "mod", "README.md", language="markdown"),
        row("README.intro", "sec", "README.md", language="markdown"),
    ]


@pytest.mark.asyncio
async def test_repo_overview_aggregates_structure() -> None:
    state = _state(routes={"overview": _overview_rows()})
    state.graph_repo.repo_graph = AsyncMock(return_value=(
        [_gnode("u-a", "pkg.m.f"), _gnode("u-b", "pkg.m.C")],
        [("u-a", "CALLS", "u-b"), ("u-b", "CALLS", "u-a")],
    ))
    resp = await _run(state, "repo_overview", {"repo_id": "acme"})
    assert resp.status.value == "success"
    d = resp.data
    assert d["found"] is True
    assert d["units"] == 6
    assert d["files"] == 3
    assert d["languages"] == {"markdown": 2, "python": 4}
    assert d["unit_kinds"]["mod"] == 3
    assert d["doc_files"] == ["README.md"]
    # `pkg` has 4 descendant units; the tree lists its child modules.
    top = next(t for t in d["module_tree"] if t["name"] == "pkg")
    assert top["units"] == 4
    assert "pkg.m" in top["modules"]
    largest = d["largest_modules"][0]
    assert largest["qualified_name"] == "pkg"
    assert largest["units"] == 4
    assert d["most_connected"][0]["connections"] == 2


@pytest.mark.asyncio
async def test_repo_overview_unknown_repo_lists_valid_ids() -> None:
    state = _state()
    resp = await _run(state, "repo_overview", {"repo_id": "ghost"})
    assert resp.data["found"] is False
    assert resp.data["valid_repo_ids"] == ["acme"]


@pytest.mark.asyncio
async def test_repo_overview_survives_graph_backend_failure() -> None:
    state = _state(routes={"overview": _overview_rows()})
    state.graph_repo.repo_graph = AsyncMock(side_effect=RuntimeError("down"))
    resp = await _run(state, "repo_overview", {"repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is True
    assert "most_connected" not in resp.data
    assert "note" in resp.data


# ============================================================================
#                               read_file
# ============================================================================
@pytest.mark.asyncio
async def test_read_file_uses_module_unit_as_full_source() -> None:
    module = _unit("pkg.m", kind="mod", lines=(1, 30),
                   content="import os\n\ndef f():\n    return 1\n",
                   signature=None)
    fn = _unit("pkg.m.f", lines=(3, 4))
    state = _state(units_for_file=[fn, module])
    resp = await _run(state, "read_file",
                      {"file_path": "pkg/m.py", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is True
    assert resp.data["content"].startswith("import os")
    outline = resp.data["units"]
    assert [u["qualified_name"] for u in outline] == ["pkg.m", "pkg.m.f"]
    assert outline[1]["lines"] == "3-4"
    assert resp.data["truncated"] is False


@pytest.mark.asyncio
async def test_read_file_stitches_units_without_module_unit() -> None:
    cls = _unit("pkg.m.C", kind="cls", lines=(1, 10), content="class C:\n",
                signature=None)
    mth = _unit("pkg.m.C.run", kind="mth", lines=(3, 8),
                content="def run(self): ...\n", parent="pkg.m.C")
    tail = _unit("pkg.m.g", lines=(12, 14), content="def g(): ...\n")
    state = _state(units_for_file=[mth, tail, cls])
    resp = await _run(state, "read_file",
                      {"file_path": "pkg/m.py", "repo_id": "acme"})
    # Method is contained in the class span → only class + tail stitched.
    assert resp.data["content"] == "class C:\n\ndef g(): ..."


@pytest.mark.asyncio
async def test_read_file_miss_suggests_similar_paths() -> None:
    state = _state(routes={"paths": [{"file_path": "pkg/m.py"}]})
    resp = await _run(state, "read_file",
                      {"file_path": "src/m.py", "repo_id": "acme"})
    assert resp.data["found"] is False
    assert resp.data["similar_paths"] == ["pkg/m.py"]
    assert "similar_paths" in resp.data["hint"]


@pytest.mark.asyncio
async def test_read_file_unknown_repo_lists_valid_ids() -> None:
    state = _state()
    resp = await _run(state, "read_file",
                      {"file_path": "pkg/m.py", "repo_id": "ghost"})
    assert resp.data["found"] is False
    assert resp.data["valid_repo_ids"] == ["acme"]


# ============================================================================
#                          get_module_summary (kept)
# ============================================================================
def _module_rows() -> list[dict[str, Any]]:
    mod = _unit("pkg.m", kind="mod", signature=None)
    alpha = _unit("pkg.m.alpha", parent="pkg.m")
    beta = _unit("pkg.m.Beta", kind="cls", parent="pkg.m", signature=None)
    return [_unit_row(mod), _unit_row(alpha), _unit_row(beta)]


@pytest.mark.asyncio
async def test_get_module_summary_returns_dense_module() -> None:
    state = _state(routes={"module": _module_rows()})
    resp = await _run(state, "get_module_summary",
                      {"module": "pkg.m", "repo_id": "acme"})
    assert resp.status.value == "success"
    summary = resp.data["summary"]
    assert summary["id"] == "pkg.m"
    assert summary["t"] == "mod"
    assert "alpha" in summary["fn"]
    assert "Beta" in summary["cls"]


@pytest.mark.asyncio
async def test_get_module_summary_unknown_module_suggests() -> None:
    state = _state(qname_matches=[QnameMatch("pkg.m", "mod")])
    resp = await _run(state, "get_module_summary",
                      {"module": "ghost.module", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is False
    assert resp.data["suggestions"][0]["qualified_name"] == "pkg.m"


# ============================================================================
#                               get_risks (kept)
# ============================================================================
@pytest.mark.asyncio
async def test_get_risks_surfaces_external_neighbors() -> None:
    seed = _unit("pkg.m.thing", unit_id="u-seed")
    state = _state(
        routes={"qname": _qname_route(seed)},
        neighbors=[
            _gnode("external:numpy", "numpy", kind=NodeKind.EXTERNAL,
                   file_path=None),
            _gnode("u-internal", "pkg.m.helper"),
        ],
    )
    resp = await _run(state, "get_risks",
                      {"entity": "pkg.m.thing", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["risk_count"] == 1
    assert resp.data["risks"][0]["qualified_name"] == "numpy"


@pytest.mark.asyncio
async def test_get_risks_unknown_entity_suggests() -> None:
    state = _state(qname_matches=[QnameMatch("pkg.m.thing", "fn")])
    resp = await _run(state, "get_risks",
                      {"entity": "nope.q", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["found"] is False
    assert resp.data["suggestions"][0]["qualified_name"] == "pkg.m.thing"


# ============================================================================
#                        deprecated aliases → v2 internals
# ============================================================================
@pytest.mark.asyncio
async def test_get_context_delegates_to_search_code() -> None:
    state = _state(
        qdrant_hits=[_qhit("u1", 0.9, "pkg.m.login")],
        units=[_unit("pkg.m.login", unit_id="u1")],
    )
    resp = await _run(state, "get_context",
                      {"task": "auth flow", "repo_id": "acme", "top_k": 5})
    assert resp.status.value == "success"
    assert resp.data["deprecated"] == "use search_code"
    assert resp.data["results"][0]["qualified_name"] == "pkg.m.login"
    assert "snippet" in resp.data["results"][0]


@pytest.mark.asyncio
async def test_query_graph_delegates_to_explore() -> None:
    resp = await _run(_explore_state(), "query_graph",
                      {"node": "pkg.m.seedfn", "repo_id": "acme", "depth": 1})
    assert resp.status.value == "success"
    assert resp.data["deprecated"] == "use explore"
    assert {n["qualified_name"] for n in resp.data["neighbors"]} == \
        {"pkg.m.callee", "pkg.m.caller"}
    assert resp.data["edges"]
    # v1 compat: SDK consumers read candidates[].unit_id.
    assert {c["unit_id"] for c in resp.data["candidates"]} == \
        {"u-callee", "u-caller"}


@pytest.mark.asyncio
async def test_get_related_components_delegates_to_explore() -> None:
    resp = await _run(_explore_state(), "get_related_components",
                      {"component": "pkg.m.seedfn", "repo_id": "acme"})
    assert resp.status.value == "success"
    assert resp.data["deprecated"] == "use explore"
    # Seed itself is never in `neighbors`.
    assert "pkg.m.seedfn" not in {
        n["qualified_name"] for n in resp.data["neighbors"]
    }


def test_deprecated_tools_say_so_in_their_descriptions() -> None:
    registry = build_default_registry()
    for name, replacement in [
        ("get_context", "search_code"),
        ("query_graph", "explore"),
        ("get_related_components", "explore"),
    ]:
        tool = registry.get(name)
        assert tool is not None
        assert tool.description.startswith("DEPRECATED — use " + replacement)


# ============================================================================
#                          update_memory (kept, mutating)
# ============================================================================
@pytest.mark.asyncio
async def test_update_memory_appends_to_redis_list() -> None:
    captured: dict[str, Any] = {}
    state = _state(redis_calls=captured)
    resp = await _run(state, "update_memory",
                      {"session_id": "s1", "repo_id": "acme",
                       "session_data": {"k": "v", "n": 1}})
    assert resp.status.value == "success"
    assert resp.data["stored"] is True
    captured["client"].rpush.assert_awaited_once()
    captured["client"].expire.assert_awaited_once()
    args = captured["client"].rpush.call_args.args
    assert args[0] == "mcp:mem:acme:s1"
    # Encoded value is canonical JSON.
    assert "\"k\":\"v\"" in args[1]


# ============================================================================
#                       ingest_repository (kept, mutating)
# ============================================================================
def _wire_ingest_fakes(state) -> None:
    state.units_repo.list_units_for_file = AsyncMock(return_value=[])
    state.units_repo.delete_units_for_file = AsyncMock(return_value=0)
    state.units_repo.upsert_units = AsyncMock(side_effect=lambda u: len(list(u)))
    state.graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    state.graph_repo.upsert_nodes = AsyncMock(side_effect=lambda n: len(list(n)))
    state.graph_repo.upsert_edges = AsyncMock(side_effect=lambda e: len(list(e)))
    state.vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    state.vector_repo.upsert_payloads = AsyncMock(
        side_effect=lambda c, p: len(list(p))
    )


@pytest.mark.asyncio
async def test_ingest_repository_runs_pipeline(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(textwrap.dedent("""
        def f(): return 1
    """).lstrip())
    state = _state()
    _wire_ingest_fakes(state)

    resp = await _run(state, "ingest_repository",
                      {"path": str(tmp_path), "repo_id": "acme",
                       "commit_sha": "deadbeef"})
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
    state = _state()
    _wire_ingest_fakes(state)

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

    resp = await _run(state, "ingest_repository",
                      {"path": str(tmp_path), "repo_id": "acme",
                       "commit_sha": "deadbeef"})
    assert resp.status.value == "success"
    # Fresh repo: every emitted unit got embedded against the repo
    # collection, and the embedder was closed afterwards.
    assert fake_pipe.calls
    assert all(coll == "repo_acme" for _, coll in fake_pipe.calls)
    assert resp.data["metrics"]["units_embedded"] == \
        resp.data["metrics"]["units_emitted"]
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
    resp = await _run(state, "ingest_repository",
                      {"path": "/this/does/not/exist", "repo_id": "acme",
                       "commit_sha": "c"})
    assert resp.status.value == "failed"
    assert resp.error_code.value == "backend_error"


def test_mutating_tools_warn_in_their_descriptions() -> None:
    registry = build_default_registry()
    for name in ("ingest_repository", "update_memory"):
        tool = registry.get(name)
        assert tool is not None
        assert "MUTATES STATE" in tool.description


# ============================================================================
#                     registry shape + description quality bar
# ============================================================================
EXPECTED_TOOLS = [
    "explore",
    "find_symbol",
    "get_context",
    "get_module_summary",
    "get_related_components",
    "get_risks",
    "ingest_repository",
    "list_repos",
    "query_graph",
    "read_file",
    "read_unit",
    "repo_overview",
    "search_code",
    "update_memory",
]


def test_default_registry_exposes_v2_surface() -> None:
    registry = build_default_registry()
    assert registry.names() == EXPECTED_TOOLS


def test_every_tool_has_agent_facing_description_and_schema() -> None:
    registry = build_default_registry()
    for tool in registry.all():
        description = getattr(tool, "description", "")
        assert len(description) > 60, f"{tool.name} description too thin"
        schema = tool.request_schema.model_json_schema()
        # Every declared property carries a description (param docs).
        for prop_name, prop in schema.get("properties", {}).items():
            has_doc = "description" in prop or any(
                "description" in alt
                for alt in prop.get("anyOf", [])
            )
            assert has_doc or prop_name in {"top_k", "seed_unit_ids", "depth"}, (
                f"{tool.name}.{prop_name} lacks a description"
            )


def test_read_only_tools_say_read_only() -> None:
    registry = build_default_registry()
    for name in ("search_code", "read_unit", "read_file", "explore",
                 "find_symbol", "list_repos", "repo_overview",
                 "get_module_summary", "get_risks"):
        assert "Read-only" in registry.get(name).description, name
