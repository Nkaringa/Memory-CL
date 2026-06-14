"""SQLite + Python-BFS graph repository for lite mode.

Same `GraphRepository` Protocol + behavior as the Neo4j repo, in pure
Python: nodes/edges in SQLite, neighbors via bounded undirected BFS,
edges only written when BOTH endpoints exist (matching Neo4j's MATCH-drop
semantics), and the same `EdgeNotAllowed` validation. At lite's scale the
graph is a few thousand edges — a BFS is microseconds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from schemas import GraphEdge, GraphNode, NodeKind, is_edge_allowed
from storage.neo4j_repo import EdgeNotAllowed

_MAX_NEIGHBOR_DEPTH = 10
_MAX_REPO_GRAPH_NODES = 20_000

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        node_id        TEXT PRIMARY KEY,
        kind           TEXT NOT NULL,
        repo_id        TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        name           TEXT NOT NULL,
        file_path      TEXT,
        line_start     INTEGER,
        line_end       INTEGER,
        commit_sha     TEXT,
        source_sha     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_edges (
        src_id     TEXT NOT NULL,
        kind       TEXT NOT NULL,
        dst_id     TEXT NOT NULL,
        repo_id    TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        weight     REAL NOT NULL DEFAULT 1.0,
        PRIMARY KEY (src_id, kind, dst_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nodes_repo ON graph_nodes (repo_id)",
    "CREATE INDEX IF NOT EXISTS ix_nodes_repo_file ON graph_nodes (repo_id, file_path)",
    "CREATE INDEX IF NOT EXISTS ix_edges_src ON graph_edges (src_id)",
    "CREATE INDEX IF NOT EXISTS ix_edges_dst ON graph_edges (dst_id)",
)

_NODE_UPSERT = text("""
INSERT INTO graph_nodes (
    node_id, kind, repo_id, qualified_name, name,
    file_path, line_start, line_end, commit_sha, source_sha
) VALUES (
    :node_id, :kind, :repo_id, :qualified_name, :name,
    :file_path, :line_start, :line_end, :commit_sha, :source_sha
)
ON CONFLICT(node_id) DO UPDATE SET
    kind = excluded.kind, repo_id = excluded.repo_id,
    qualified_name = excluded.qualified_name, name = excluded.name,
    file_path = excluded.file_path, line_start = excluded.line_start,
    line_end = excluded.line_end, commit_sha = excluded.commit_sha,
    source_sha = excluded.source_sha
""")

_EDGE_UPSERT = text("""
INSERT INTO graph_edges (src_id, kind, dst_id, repo_id, commit_sha, weight)
VALUES (:src_id, :kind, :dst_id, :repo_id, :commit_sha, :weight)
ON CONFLICT(src_id, kind, dst_id) DO UPDATE SET
    repo_id = excluded.repo_id, commit_sha = excluded.commit_sha,
    weight = excluded.weight
""")


def _node_params(n: GraphNode) -> dict[str, Any]:
    return {
        "node_id": n.node_id, "kind": n.kind.value, "repo_id": n.repo_id,
        "qualified_name": n.qualified_name, "name": n.name,
        "file_path": n.file_path, "line_start": n.line_start,
        "line_end": n.line_end, "commit_sha": n.commit_sha, "source_sha": n.source_sha,
    }


def _row_to_node(row: Any) -> GraphNode:
    m = row._mapping if hasattr(row, "_mapping") else row
    return GraphNode(
        node_id=m["node_id"], kind=NodeKind(m["kind"]), repo_id=m["repo_id"],
        qualified_name=m["qualified_name"], name=m["name"], file_path=m["file_path"],
        line_start=m["line_start"], line_end=m["line_end"],
        commit_sha=m["commit_sha"], source_sha=m["source_sha"],
    )


class LiteGraphRepository:
    name: str = "lite_graph_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._kind_cache: dict[str, NodeKind] = {}

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))

    async def ensure_constraints(self) -> None:
        """Alias so the lifespan's `graph_repo.ensure_constraints()` (a
        Neo4j-ism) works unchanged in lite mode."""
        await self.ensure_schema()

    # ----- Writes -----
    async def upsert_node(self, node: GraphNode) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_NODE_UPSERT, _node_params(node))
        self._kind_cache[node.node_id] = node.kind

    async def upsert_nodes(self, nodes: Iterable[GraphNode]) -> int:
        nodes = list(nodes)
        if not nodes:
            return 0
        async with self._engine.begin() as conn:
            await conn.execute(_NODE_UPSERT, [_node_params(n) for n in nodes])
        for n in nodes:
            self._kind_cache[n.node_id] = n.kind
        return len(nodes)

    def _validate_edge(self, edge: GraphEdge) -> None:
        src_kind = self._kind_cache.get(edge.src_id)
        dst_kind = self._kind_cache.get(edge.dst_id)
        if src_kind and dst_kind and not is_edge_allowed(src_kind, edge.kind, dst_kind):
            raise EdgeNotAllowed(
                f"{src_kind.value}-[{edge.kind.value}]->{dst_kind.value} "
                "forbidden by EDGE_RULES"
            )

    async def _existing_nodes(
        self, conn: AsyncConnection, ids: set[str]
    ) -> set[str]:
        if not ids:
            return set()
        q = text("SELECT node_id FROM graph_nodes WHERE node_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await conn.execute(q, {"ids": list(ids)})).fetchall()
        return {r[0] for r in rows}

    async def upsert_edge(self, edge: GraphEdge) -> None:
        self._validate_edge(edge)
        async with self._engine.begin() as conn:
            present = await self._existing_nodes(conn, {edge.src_id, edge.dst_id})
            if edge.src_id in present and edge.dst_id in present:
                await conn.execute(_EDGE_UPSERT, _edge_params(edge))

    async def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        edges = list(edges)
        if not edges:
            return 0
        for e in edges:
            self._validate_edge(e)
        endpoint_ids = {e.src_id for e in edges} | {e.dst_id for e in edges}
        written = 0
        async with self._engine.begin() as conn:
            present = await self._existing_nodes(conn, endpoint_ids)
            for e in edges:
                if e.src_id in present and e.dst_id in present:
                    await conn.execute(_EDGE_UPSERT, _edge_params(e))
                    written += 1
        return written

    # ----- Reads -----
    async def get_node(self, node_id: str) -> GraphNode | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                text("SELECT * FROM graph_nodes WHERE node_id = :id"), {"id": node_id}
            )).first()
            return _row_to_node(row) if row else None

    async def neighbors(
        self,
        node_id: str,
        edge_kinds: Sequence[str] | None = None,
        depth: int = 1,
    ) -> Sequence[GraphNode]:
        depth = max(1, min(int(depth), _MAX_NEIGHBOR_DEPTH))
        kinds = list(edge_kinds or [])
        adj_sql = (
            "SELECT src_id, dst_id FROM graph_edges "
            "WHERE (src_id IN :ids OR dst_id IN :ids)"
        )
        params_extra: list[Any] = [bindparam("ids", expanding=True)]
        if kinds:
            adj_sql += " AND kind IN :kinds"
            params_extra.append(bindparam("kinds", expanding=True))
        adj_query = text(adj_sql).bindparams(*params_extra)

        visited = {node_id}
        frontier = {node_id}
        async with self._engine.connect() as conn:
            for _ in range(depth):
                if not frontier:
                    break
                params: dict[str, Any] = {"ids": list(frontier)}
                if kinds:
                    params["kinds"] = kinds
                rows = (await conn.execute(adj_query, params)).fetchall()
                nxt: set[str] = set()
                for src, dst in rows:
                    for other in (src, dst):
                        if other not in visited:
                            visited.add(other)
                            nxt.add(other)
                frontier = nxt
            visited.discard(node_id)
            if not visited:
                return []
            nodes = await self._fetch_nodes(conn, visited)
        return sorted(nodes, key=lambda n: n.node_id)

    async def _fetch_nodes(
        self, conn: AsyncConnection, ids: set[str]
    ) -> list[GraphNode]:
        q = text("SELECT * FROM graph_nodes WHERE node_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await conn.execute(q, {"ids": list(ids)})).fetchall()
        return [_row_to_node(r) for r in rows]

    async def edges_among(self, node_ids: Sequence[str]) -> list[tuple[str, str, str]]:
        ids = list(node_ids)
        if not ids:
            return []
        q = text(
            "SELECT src_id, kind, dst_id FROM graph_edges "
            "WHERE src_id IN :ids AND dst_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(q, {"ids": ids})).fetchall()
        return sorted({(r[0], r[1], r[2]) for r in rows})

    async def repo_graph(
        self,
        repo_id: str,
        *,
        include_external: bool = False,
        max_nodes: int = 5000,
    ) -> tuple[list[GraphNode], list[tuple[str, str, str]]]:
        max_nodes = max(1, min(int(max_nodes), _MAX_REPO_GRAPH_NODES))
        sql = "SELECT * FROM graph_nodes WHERE repo_id = :repo_id"
        if not include_external:
            sql += f" AND kind <> '{NodeKind.EXTERNAL.value}'"
        sql += " ORDER BY node_id LIMIT :max_nodes"
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                text(sql), {"repo_id": repo_id, "max_nodes": max_nodes}
            )).fetchall()
        nodes = sorted((_row_to_node(r) for r in rows), key=lambda n: n.node_id)
        edges = await self.edges_among([n.node_id for n in nodes])
        return nodes, edges

    async def delete_subgraph_for_file(self, repo_id: str, file_path: str) -> int:
        async with self._engine.begin() as conn:
            ids = {
                r[0] for r in (await conn.execute(
                    text(
                        "SELECT node_id FROM graph_nodes "
                        "WHERE repo_id = :repo_id AND file_path = :fp"
                    ),
                    {"repo_id": repo_id, "fp": file_path},
                )).fetchall()
            }
            if not ids:
                return 0
            del_nodes = text(
                "DELETE FROM graph_nodes WHERE node_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            del_edges = text(
                "DELETE FROM graph_edges WHERE src_id IN :ids OR dst_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            await conn.execute(del_edges, {"ids": list(ids)})
            await conn.execute(del_nodes, {"ids": list(ids)})
        for nid in ids:
            self._kind_cache.pop(nid, None)
        return len(ids)


def _edge_params(e: GraphEdge) -> dict[str, Any]:
    return {
        "src_id": e.src_id, "kind": e.kind.value, "dst_id": e.dst_id,
        "repo_id": e.repo_id, "commit_sha": e.commit_sha, "weight": e.weight,
    }


__all__ = ["EdgeNotAllowed", "LiteGraphRepository"]
