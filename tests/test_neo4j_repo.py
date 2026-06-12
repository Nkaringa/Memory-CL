from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from schemas import EdgeKind, GraphEdge, GraphNode, NodeKind
from storage import EdgeNotAllowed, GraphRepository, Neo4jGraphRepository
from storage.neo4j_repo import _constraint_stmts


def _node(node_id: str, kind: NodeKind, **overrides: Any) -> GraphNode:
    base: dict[str, Any] = {
        "node_id": node_id,
        "kind": kind,
        "repo_id": "r",
        "qualified_name": node_id,
        "name": node_id.rsplit(".", 1)[-1],
    }
    if kind != NodeKind.EXTERNAL:
        base.update(file_path="f.py", line_start=1, line_end=1,
                    commit_sha="c", source_sha="s")
    base.update(overrides)
    return GraphNode(**base)


# ---- Mocked driver / session ----------------------------------------------
class _FakeRecord(dict):
    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


class _FakeResult:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._records = records or []

    async def data(self) -> list[dict[str, Any]]:
        return self._records

    async def single(self) -> dict[str, Any] | None:
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self) -> None:
        self.runs: list[tuple[str, dict[str, Any]]] = []
        self.next_results: list[_FakeResult] = []

    async def run(self, stmt: str, params: dict[str, Any] | None = None) -> _FakeResult:
        self.runs.append((stmt, params or {}))
        if self.next_results:
            return self.next_results.pop(0)
        return _FakeResult()

    async def close(self) -> None:
        pass


class _FakeDriver:
    def __init__(self) -> None:
        self.session_obj = _FakeSession()

    def session(self, database: str | None = None):
        @asynccontextmanager
        async def _ctx():
            yield self.session_obj
        return _ctx()


# ---- Tests ----------------------------------------------------------------
def test_repository_satisfies_protocol() -> None:
    repo = Neo4jGraphRepository(driver=AsyncMock())  # type: ignore[arg-type]
    assert isinstance(repo, GraphRepository)


def test_constraint_stmts_cover_every_node_kind() -> None:
    stmts = _constraint_stmts()
    labels = [k.value for k in NodeKind]
    for label in labels:
        assert any(f"FOR (n:{label})" in s for s in stmts), f"missing for {label}"
    assert all("IF NOT EXISTS" in s for s in stmts)


@pytest.mark.asyncio
async def test_ensure_constraints_runs_each_statement() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    await repo.ensure_constraints()
    assert len(driver.session_obj.runs) == len(_constraint_stmts())


@pytest.mark.asyncio
async def test_upsert_node_uses_correct_label() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    n = _node("u1", NodeKind.FUNCTION)
    await repo.upsert_node(n)

    stmt, params = driver.session_obj.runs[0]
    assert "SET n:Function" in stmt
    assert params["node_id"] == "u1"
    assert params["props"]["repo_id"] == "r"


@pytest.mark.asyncio
async def test_upsert_edge_validates_against_edge_rules() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    # Seed kind cache by upserting nodes first.
    await repo.upsert_node(_node("m1", NodeKind.MODULE))
    await repo.upsert_node(_node("f1", NodeKind.FUNCTION))

    # MODULE -[CALLS]-> FUNCTION is NOT in EDGE_RULES.
    bad = GraphEdge(
        src_id="m1", kind=EdgeKind.CALLS, dst_id="f1",
        repo_id="r", commit_sha="c",
    )
    with pytest.raises(EdgeNotAllowed):
        await repo.upsert_edge(bad)


@pytest.mark.asyncio
async def test_upsert_edge_writes_relationship_with_provenance() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    await repo.upsert_node(_node("f1", NodeKind.FUNCTION))
    await repo.upsert_node(_node("f2", NodeKind.FUNCTION))
    n_runs_before = len(driver.session_obj.runs)

    edge = GraphEdge(
        src_id="f1", kind=EdgeKind.CALLS, dst_id="f2",
        repo_id="r", commit_sha="abc",
    )
    await repo.upsert_edge(edge)

    stmt, params = driver.session_obj.runs[n_runs_before]
    assert ":CALLS]" in stmt
    assert params["src_id"] == "f1"
    assert params["dst_id"] == "f2"
    assert params["commit_sha"] == "abc"
    assert params["weight"] == 1.0


@pytest.mark.asyncio
async def test_delete_subgraph_returns_deleted_count() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([{"deleted": 5}])]
    deleted = await repo.delete_subgraph_for_file("r", "pkg/m.py")
    assert deleted == 5


@pytest.mark.asyncio
async def test_get_node_returns_hydrated_node() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([
        {"n": {"node_id": "u1", "kind": "Function", "repo_id": "r",
               "qualified_name": "pkg.m.f", "name": "f",
               "file_path": "pkg/m.py"}},
    ])]
    node = await repo.get_node("u1")

    assert node is not None
    assert node.node_id == "u1"
    assert node.kind == NodeKind.FUNCTION
    assert node.qualified_name == "pkg.m.f"
    assert node.file_path == "pkg/m.py"

    stmt, params = driver.session_obj.runs[-1]
    assert "LIMIT 1" in stmt
    assert params["node_id"] == "u1"


@pytest.mark.asyncio
async def test_get_node_returns_none_when_missing() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([])]
    assert await repo.get_node("ghost") is None


@pytest.mark.asyncio
async def test_neighbors_sorted_by_node_id() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    rows = [
        {"b": {"node_id": "z", "kind": "Function", "repo_id": "r",
               "qualified_name": "z", "name": "z"}},
        {"b": {"node_id": "a", "kind": "Function", "repo_id": "r",
               "qualified_name": "a", "name": "a"}},
    ]
    driver.session_obj.next_results = [_FakeResult(rows)]
    neighbors = await repo.neighbors("u1", edge_kinds=["CALLS"], depth=1)
    assert [n.node_id for n in neighbors] == ["a", "z"]


@pytest.mark.asyncio
async def test_neighbors_cypher_inlines_depth_and_is_undirected() -> None:
    """Regression pin for the Gap-3 bug (2026-06-12).

    Neo4j rejects parameters inside variable-length bounds, so `$depth`
    in the MATCH pattern was a parse-time error — every neighbors() call
    failed and the retriever swallowed it as 'no neighbors'. The depth
    must be inlined as a validated int. The pattern must also be
    UNDIRECTED: a leaf function whose only outbound edges hit External
    nodes is still connected to the graph via inbound DEFINES/CONTAINS.
    """
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([])]
    await repo.neighbors("u1", edge_kinds=[], depth=2)

    stmt, params = driver.session_obj.runs[-1]
    assert "$depth" not in stmt          # the original bug
    assert "*1..2]" in stmt              # depth inlined as a literal int
    assert "]->" not in stmt             # undirected, not outbound-only
    assert "depth" not in params
    assert params["node_id"] == "u1"


@pytest.mark.asyncio
async def test_edges_among_query_uses_ids_param_and_directed_pattern() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([])]
    await repo.edges_among(["a", "b"])

    stmt, params = driver.session_obj.runs[-1]
    assert "a.node_id IN $ids" in stmt
    assert "b.node_id IN $ids" in stmt
    assert "]->" in stmt  # directed: real edge direction, not undirected
    assert params == {"ids": ["a", "b"]}


@pytest.mark.asyncio
async def test_edges_among_maps_sorts_and_dedupes() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    rows = [
        {"src": "b", "kind": "CALLS", "dst": "a"},
        {"src": "a", "kind": "DEFINES", "dst": "b"},
        {"src": "a", "kind": "CALLS", "dst": "b"},
        {"src": "a", "kind": "CALLS", "dst": "b"},  # duplicate
    ]
    driver.session_obj.next_results = [_FakeResult(rows)]
    edges = await repo.edges_among(["a", "b"])

    assert edges == [
        ("a", "CALLS", "b"),
        ("a", "DEFINES", "b"),
        ("b", "CALLS", "a"),
    ]


@pytest.mark.asyncio
async def test_edges_among_empty_input_short_circuits() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    assert await repo.edges_among([]) == []
    assert driver.session_obj.runs == []  # never touched the driver


@pytest.mark.asyncio
async def test_neighbors_depth_clamped_to_safe_bounds() -> None:
    driver = _FakeDriver()
    repo = Neo4jGraphRepository(driver=driver)  # type: ignore[arg-type]
    driver.session_obj.next_results = [_FakeResult([]), _FakeResult([])]

    await repo.neighbors("u1", depth=0)
    stmt, _ = driver.session_obj.runs[-1]
    assert "*1..1]" in stmt

    await repo.neighbors("u1", depth=999)
    stmt, _ = driver.session_obj.runs[-1]
    assert "*1..10]" in stmt
