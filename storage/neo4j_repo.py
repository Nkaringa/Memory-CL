from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

from neo4j import AsyncDriver

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from schemas import EdgeKind, GraphEdge, GraphNode, NodeKind, is_edge_allowed

_tracer = get_tracer("storage.neo4j_repo")


# Per-label uniqueness constraints. The same `node_id` MUST never appear
# under two labels — that would break the cross-store identity invariant.
# Constraint syntax is Neo4j 5+ (CREATE CONSTRAINT … IF NOT EXISTS).
def _constraint_stmts() -> tuple[str, ...]:
    return tuple(
        f"CREATE CONSTRAINT unit_id_unique_{label.lower()} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.node_id IS UNIQUE"
        for label in (k.value for k in NodeKind)
    )


def _node_props(node: GraphNode) -> dict[str, Any]:
    """Strip None fields so Neo4j doesn't store nulls explicitly."""
    raw = node.model_dump(mode="json")
    return {k: v for k, v in raw.items() if v is not None}


_NODE_MERGE = (
    "MERGE (n {{node_id: $node_id}}) "
    "SET n:{label} "
    "SET n += $props"
)

_EDGE_MERGE = (
    "MATCH (a {node_id: $src_id}) "
    "MATCH (b {node_id: $dst_id}) "
    "MERGE (a)-[r:%s]->(b) "
    "SET r.repo_id = $repo_id, "
    "    r.commit_sha = $commit_sha, "
    "    r.weight = $weight"
)

# Depth is inlined as a literal int (clamped in `neighbors()`): Neo4j
# rejects parameters inside variable-length bounds, so `*1..$depth` is a
# parse-time syntax error — the cause of the long-standing "0 neighbors"
# bug. The pattern is UNDIRECTED on purpose: a unit whose only outbound
# edges hit External nodes is still connected to the rest of the graph
# through inbound DEFINES/CONTAINS/CALLS edges.
_NEIGHBORS_QUERY_TEMPLATE = (
    "MATCH (a {{node_id: $node_id}})-[r*1..{depth}]-(b) "
    "WHERE size($edge_kinds) = 0 OR all(rel IN r WHERE type(rel) IN $edge_kinds) "
    "RETURN DISTINCT b"
)

_MAX_NEIGHBOR_DEPTH = 10  # mirrors the MCP request schema's upper bound

_GET_NODE_QUERY = "MATCH (n {node_id: $node_id}) RETURN n LIMIT 1"

# Directed on purpose — callers want the REAL edge direction among a node
# set (e.g. to render arrows), unlike `neighbors()` whose reachability
# pattern is undirected. A plain `$ids` list param in WHERE is fine; only
# variable-length bounds reject parameters.
_EDGES_AMONG_QUERY = (
    "MATCH (a)-[r]->(b) "
    "WHERE a.node_id IN $ids AND b.node_id IN $ids "
    "RETURN a.node_id AS src, type(r) AS kind, b.node_id AS dst"
)

_DELETE_FOR_FILE = (
    "MATCH (n {repo_id: $repo_id, file_path: $file_path}) "
    "DETACH DELETE n "
    "RETURN count(n) AS deleted"
)


class EdgeNotAllowed(RuntimeError):
    """Raised when callers attempt to write an edge that violates EDGE_RULES."""


class Neo4jGraphRepository:
    """Concrete `GraphRepository` over the Phase-1 Neo4jClient driver."""

    name: str = "neo4j_graph_repo"

    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database
        self._kind_cache: dict[str, NodeKind] = {}

    # ----- Bootstrap -----
    async def ensure_constraints(self) -> None:
        with _tracer.start_as_current_span("neo4j_repo.ensure_constraints"):
            async with self._driver.session(database=self._database) as session:
                for stmt in _constraint_stmts():
                    await session.run(stmt)

    # ----- Writes -----
    async def upsert_node(self, node: GraphNode) -> None:
        start = time.perf_counter()
        with _tracer.start_as_current_span("neo4j_repo.upsert_node") as span:
            span.set_attribute("node_id", node.node_id)
            span.set_attribute("kind", node.kind.value)
            span.set_attribute("repo_id", node.repo_id)
            stmt = _NODE_MERGE.format(label=node.kind.value)
            params = {"node_id": node.node_id, "props": _node_props(node)}
            async with self._driver.session(database=self._database) as session:
                await session.run(stmt, params)
            self._kind_cache[node.node_id] = node.kind
            emit_phase2_event(
                event="neo4j_upsert_node",
                operation="neo4j_repo.upsert_node",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                unit_id=node.node_id,
                file_path=node.file_path or "",
                content_hash=node.source_sha or "",
                kind=node.kind.value,
                level="debug",
            )

    async def upsert_nodes(self, nodes: Iterable[GraphNode]) -> int:
        nodes = list(nodes)
        if not nodes:
            return 0
        start = time.perf_counter()
        with _tracer.start_as_current_span("neo4j_repo.upsert_nodes") as span:
            span.set_attribute("count", len(nodes))
            async with self._driver.session(database=self._database) as session:
                for n in nodes:
                    stmt = _NODE_MERGE.format(label=n.kind.value)
                    await session.run(stmt, {"node_id": n.node_id, "props": _node_props(n)})
                    self._kind_cache[n.node_id] = n.kind
            emit_phase2_event(
                event="neo4j_upsert_nodes",
                operation="neo4j_repo.upsert_nodes",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                count=len(nodes),
                level="info",
            )
            return len(nodes)

    async def upsert_edge(self, edge: GraphEdge) -> None:
        await self._validate_edge(edge)
        start = time.perf_counter()
        with _tracer.start_as_current_span("neo4j_repo.upsert_edge") as span:
            span.set_attribute("kind", edge.kind.value)
            span.set_attribute("src_id", edge.src_id)
            span.set_attribute("dst_id", edge.dst_id)
            stmt = _EDGE_MERGE % edge.kind.value  # safe: EdgeKind enum, never user input
            async with self._driver.session(database=self._database) as session:
                await session.run(
                    stmt,
                    {
                        "src_id": edge.src_id,
                        "dst_id": edge.dst_id,
                        "repo_id": edge.repo_id,
                        "commit_sha": edge.commit_sha,
                        "weight": edge.weight,
                    },
                )
            emit_phase2_event(
                event="neo4j_upsert_edge",
                operation="neo4j_repo.upsert_edge",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                kind=edge.kind.value,
                level="debug",
            )

    async def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        edges = list(edges)
        if not edges:
            return 0
        for e in edges:
            await self._validate_edge(e)
        start = time.perf_counter()
        with _tracer.start_as_current_span("neo4j_repo.upsert_edges") as span:
            span.set_attribute("count", len(edges))
            async with self._driver.session(database=self._database) as session:
                for e in edges:
                    stmt = _EDGE_MERGE % e.kind.value
                    await session.run(
                        stmt,
                        {
                            "src_id": e.src_id,
                            "dst_id": e.dst_id,
                            "repo_id": e.repo_id,
                            "commit_sha": e.commit_sha,
                            "weight": e.weight,
                        },
                    )
            emit_phase2_event(
                event="neo4j_upsert_edges",
                operation="neo4j_repo.upsert_edges",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                count=len(edges),
                level="info",
            )
            return len(edges)

    # ----- Reads -----
    async def get_node(self, node_id: str) -> GraphNode | None:
        """Point-lookup of a single node; None when it doesn't exist.

        Used by the graph retriever to hydrate seed metadata so depth-0
        candidates carry qualified_name/kind/file_path.
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(_GET_NODE_QUERY, {"node_id": node_id})
            records = await result.data()
        if not records:
            return None
        return _record_to_node(records[0]["n"])

    async def neighbors(
        self,
        node_id: str,
        edge_kinds: Sequence[str] | None = None,
        depth: int = 1,
    ) -> Sequence[GraphNode]:
        depth = max(1, min(int(depth), _MAX_NEIGHBOR_DEPTH))
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                _NEIGHBORS_QUERY_TEMPLATE.format(depth=depth),
                {
                    "node_id": node_id,
                    "edge_kinds": list(edge_kinds or []),
                },
            )
            records = await result.data()
        out: list[GraphNode] = []
        for rec in records:
            n = rec["b"]
            out.append(_record_to_node(n))
        out.sort(key=lambda n: n.node_id)
        return out

    async def edges_among(self, node_ids: Sequence[str]) -> list[tuple[str, str, str]]:
        """All directed edges whose endpoints BOTH lie in `node_ids`.

        Returns sorted, deduplicated (src, kind, dst) tuples. Empty input
        short-circuits without touching the driver.
        """
        ids = list(node_ids)
        if not ids:
            return []
        async with self._driver.session(database=self._database) as session:
            result = await session.run(_EDGES_AMONG_QUERY, {"ids": ids})
            records = await result.data()
        edges = {(rec["src"], rec["kind"], rec["dst"]) for rec in records}
        return sorted(edges)

    async def delete_subgraph_for_file(self, repo_id: str, file_path: str) -> int:
        with _tracer.start_as_current_span("neo4j_repo.delete_subgraph_for_file") as span:
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)
            async with self._driver.session(database=self._database) as session:
                result = await session.run(
                    _DELETE_FOR_FILE, {"repo_id": repo_id, "file_path": file_path}
                )
                rec = await result.single()
                deleted = int(rec["deleted"]) if rec else 0
            return deleted

    # ----- Internal -----
    async def _validate_edge(self, edge: GraphEdge) -> None:
        """Cheap pre-flight: every edge passes EDGE_RULES.

        We don't query Neo4j for the kinds — caller (GraphBuilder) has
        already constructed nodes consistently. The cache is populated
        as upsert_node runs; if cache lookup misses (legitimate for
        cross-batch edges), we trust the construction site.
        """
        src_kind = self._kind_cache.get(edge.src_id)
        dst_kind = self._kind_cache.get(edge.dst_id)
        if src_kind and dst_kind and not is_edge_allowed(src_kind, edge.kind, dst_kind):
            raise EdgeNotAllowed(
                f"{src_kind.value}-[{edge.kind.value}]->{dst_kind.value} "
                f"forbidden by EDGE_RULES"
            )


def _record_to_node(n: dict[str, Any]) -> GraphNode:
    """Hydrate a GraphNode from a Neo4j result-record dict."""
    # Neo4j may surface labels via `__labels__` or driver-specific keys;
    # we prefer the explicit `kind` property we wrote at upsert time.
    return GraphNode(
        node_id=n["node_id"],
        kind=NodeKind(n["kind"]) if "kind" in n else NodeKind.EXTERNAL,
        repo_id=n["repo_id"],
        qualified_name=n["qualified_name"],
        name=n["name"],
        file_path=n.get("file_path"),
        line_start=n.get("line_start"),
        line_end=n.get("line_end"),
        commit_sha=n.get("commit_sha"),
        source_sha=n.get("source_sha"),
    )


__all__ = [
    "EdgeKind",  # convenience re-export
    "EdgeNotAllowed",
    "Neo4jGraphRepository",
]
