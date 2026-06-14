"""Real tests for the lite SQLite + Python-BFS graph repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from schemas import EdgeKind, GraphEdge, GraphNode, NodeKind
from storage.lite.engine import make_sqlite_engine
from storage.lite.graph_repo import EdgeNotAllowed, LiteGraphRepository

pytestmark = pytest.mark.asyncio


def _node(node_id: str, kind: NodeKind, *, repo_id="r", file_path="m.py") -> GraphNode:
    return GraphNode(
        node_id=node_id, kind=kind, repo_id=repo_id,
        qualified_name=node_id, name=node_id, file_path=file_path,
    )


def _edge(src: str, kind: EdgeKind, dst: str, *, repo_id="r") -> GraphEdge:
    return GraphEdge(src_id=src, kind=kind, dst_id=dst, repo_id=repo_id, commit_sha="c")


async def _repo(tmp_path: Path) -> LiteGraphRepository:
    repo = LiteGraphRepository(make_sqlite_engine(tmp_path / "g.db"))
    await repo.ensure_schema()
    return repo


async def test_nodes_edges_and_neighbors(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([
        _node("f1", NodeKind.FUNCTION),
        _node("f2", NodeKind.FUNCTION),
        _node("f3", NodeKind.FUNCTION),
    ])
    # f1 -> f2 -> f3 (CALLS is valid Function->Function)
    assert await repo.upsert_edges([
        _edge("f1", EdgeKind.CALLS, "f2"),
        _edge("f2", EdgeKind.CALLS, "f3"),
    ]) == 2
    # depth 1 from f1 (undirected) -> f2 only
    n1 = await repo.neighbors("f1", depth=1)
    assert [n.node_id for n in n1] == ["f2"]
    # depth 2 -> f2, f3
    n2 = await repo.neighbors("f1", depth=2)
    assert {n.node_id for n in n2} == {"f2", "f3"}
    # undirected: from f3 we still reach f2 (inbound edge)
    assert {n.node_id for n in await repo.neighbors("f3", depth=1)} == {"f2"}


async def test_neighbors_kind_filter(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([
        _node("file", NodeKind.FILE),
        _node("fn", NodeKind.FUNCTION),
        _node("fn2", NodeKind.FUNCTION),
    ])
    await repo.upsert_edges([
        _edge("file", EdgeKind.CONTAINS, "fn"),
        _edge("fn", EdgeKind.CALLS, "fn2"),
    ])
    # only CALLS edges -> from 'file' nothing (its edge is CONTAINS)
    assert await repo.neighbors("file", edge_kinds=["CALLS"], depth=2) == []
    # only CONTAINS -> file reaches fn
    got = await repo.neighbors("file", edge_kinds=["CONTAINS"], depth=2)
    assert {n.node_id for n in got} == {"fn"}


async def test_invalid_edge_rejected(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([_node("fn", NodeKind.FUNCTION), _node("m", NodeKind.MODULE)])
    # Function-CALLS->Module is not allowed (CALLS targets Function/Method/External).
    with pytest.raises(EdgeNotAllowed):
        await repo.upsert_edge(_edge("fn", EdgeKind.CALLS, "m"))


async def test_edges_to_missing_nodes_are_dropped(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([_node("f1", NodeKind.FUNCTION)])  # f2 not inserted
    # f2 missing -> the edge is dropped, written count 0.
    assert await repo.upsert_edges([_edge("f1", EdgeKind.CALLS, "f2")]) == 0
    assert await repo.neighbors("f1", depth=1) == []


async def test_edges_among_and_repo_graph(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([
        _node("f1", NodeKind.FUNCTION),
        _node("f2", NodeKind.FUNCTION),
        _node("ext", NodeKind.EXTERNAL, file_path=None),
    ])
    await repo.upsert_edges([
        _edge("f1", EdgeKind.CALLS, "f2"),
        _edge("f1", EdgeKind.CALLS, "ext"),
    ])
    assert await repo.edges_among(["f1", "f2"]) == [("f1", "CALLS", "f2")]
    nodes, edges = await repo.repo_graph("r", include_external=False)
    assert {n.node_id for n in nodes} == {"f1", "f2"}  # external excluded
    assert edges == [("f1", "CALLS", "f2")]
    nodes_x, _ = await repo.repo_graph("r", include_external=True)
    assert "ext" in {n.node_id for n in nodes_x}


async def test_delete_subgraph_for_file(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_nodes([
        _node("f1", NodeKind.FUNCTION, file_path="a.py"),
        _node("f2", NodeKind.FUNCTION, file_path="b.py"),
    ])
    await repo.upsert_edges([_edge("f1", EdgeKind.CALLS, "f2")])
    assert await repo.delete_subgraph_for_file("r", "a.py") == 1
    assert await repo.get_node("f1") is None
    # the edge touching f1 is gone too
    assert await repo.edges_among(["f1", "f2"]) == []
