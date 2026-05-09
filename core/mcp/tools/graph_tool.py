"""Graph-oriented MCP tools.

`get_related_components(component)`  — neighbors via GraphRetriever
`query_graph(node, depth)`           — bounded BFS via GraphRetriever
`get_risks(entity)`                  — heuristic risk projection from
                                        outgoing edges to EXTERNAL nodes

All three rely solely on `core.retrieval.GraphRetriever` and the
existing Phase-2 `Neo4jGraphRepository.neighbors` API.
"""

from __future__ import annotations

from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import (
    GetRelatedComponentsRequest,
    GetRisksRequest,
    QueryGraphRequest,
)
from core.retrieval import GraphRetriever
from schemas import RetrievalCandidate


class _GraphCallMixin:
    """Run the GraphRetriever with the resolved seed and return candidates.

    Two seed-resolution forms are supported by the agent surface:
    a unit_id (used as-is) or a qualified_name (resolved via metadata).
    """

    @staticmethod
    async def _resolve_seeds(
        state, *, repo_id: str, hint: str
    ) -> list[str]:
        # If the hint already looks like a unit_id (64-char hex), trust
        # it. Otherwise treat as qname and look up via Postgres.
        if len(hint) == 64 and all(c in "0123456789abcdef" for c in hint):
            return [hint]
        return await _seeds_for_qname(state, repo_id=repo_id, qname=hint)


async def _seeds_for_qname(state, *, repo_id: str, qname: str) -> list[str]:
    """Resolve a qname to its unit_id via the canonical Postgres store."""
    from sqlalchemy import text

    sql = text(
        "SELECT unit_id FROM ingestion_units "
        " WHERE repo_id = :repo_id AND qualified_name = :qname LIMIT 1"
    )
    async with state.postgres.engine.connect() as conn:
        result = await conn.execute(sql, {"repo_id": repo_id, "qname": qname})
        row = result.first()
    return [row[0]] if row else []


class GetRelatedComponentsTool(_GraphCallMixin):
    """`get_related_components(component, depth?)` — graph neighbors."""

    name: str = "get_related_components"
    request_schema = GetRelatedComponentsRequest

    async def execute(
        self, request: GetRelatedComponentsRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        seeds = await self._resolve_seeds(
            ctx.state, repo_id=request.repo_id, hint=request.component
        )
        if not seeds:
            return {"component": request.component, "found": False, "related": []}

        retriever = GraphRetriever(ctx.state.graph_repo, max_depth=request.depth)
        cands = await retriever.search(
            seeds, query_id=ctx.request_id, repo_id=request.repo_id
        )
        # Drop the seed itself from "related" — agents already know it.
        related = [c for c in cands if c.unit_id not in set(seeds)]
        return {
            "component": request.component,
            "found": True,
            "depth": request.depth,
            "related": [_candidate_to_dict(c) for c in related],
        }


class QueryGraphTool(_GraphCallMixin):
    """`query_graph(node, depth?)` — bounded BFS exposing seed + neighbors."""

    name: str = "query_graph"
    request_schema = QueryGraphRequest

    async def execute(
        self, request: QueryGraphRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        seeds = await self._resolve_seeds(
            ctx.state, repo_id=request.repo_id, hint=request.node
        )
        if not seeds:
            return {"node": request.node, "found": False, "candidates": []}

        retriever = GraphRetriever(ctx.state.graph_repo, max_depth=request.depth)
        cands = await retriever.search(
            seeds, query_id=ctx.request_id, repo_id=request.repo_id
        )
        return {
            "node": request.node,
            "found": True,
            "depth": request.depth,
            "candidates": [_candidate_to_dict(c) for c in cands],
        }


class GetRisksTool(_GraphCallMixin):
    """`get_risks(entity)` — heuristic risks via the existing graph layer.

    Phase-5 risks are STRUCTURAL (semantic risks come in Phase 6+):
        - external dependencies the entity directly imports/calls
        - high-degree neighbors that fan out into many EXTERNAL nodes

    The MCP layer adds nothing semantic — it composes Phase-2/4 APIs
    and reports the resulting node ids.
    """

    name: str = "get_risks"
    request_schema = GetRisksRequest

    async def execute(
        self, request: GetRisksRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        seeds = await self._resolve_seeds(
            ctx.state, repo_id=request.repo_id, hint=request.entity
        )
        if not seeds:
            return {"entity": request.entity, "found": False, "risks": []}

        # Pull the immediate 1-hop neighborhood: GraphRetriever's BFS
        # naturally captures CALLS / IMPORTS / DEFINES depending on
        # node kind, and it already drops EXTERNAL targets at depth>0.
        # For risk projection we DO want External targets back — so we
        # ask the underlying graph repo directly for 1-hop neighbors
        # without GraphRetriever's EXTERNAL filter.
        try:
            neighbors = await ctx.state.graph_repo.neighbors(seeds[0], depth=1)
        except Exception as exc:
            return {
                "entity": request.entity,
                "found": True,
                "risks": [],
                "warning": f"graph backend error: {exc}",
            }

        externals = [
            {"node_id": n.node_id, "qualified_name": n.qualified_name,
             "kind": n.kind.value}
            for n in sorted(neighbors, key=lambda n: n.node_id)
            if n.kind.value == "External"
        ]
        return {
            "entity": request.entity,
            "found": True,
            "risks": externals,
            "risk_count": len(externals),
        }


def _candidate_to_dict(c: RetrievalCandidate) -> dict[str, Any]:
    return {
        "unit_id": c.unit_id,
        "qualified_name": c.qualified_name,
        "kind": c.kind,
        "file_path": c.file_path,
        "raw_score": c.raw_score,
        "channel": c.channel.value,
        "depth": c.extra.get("depth"),
    }


__all__ = ["GetRelatedComponentsTool", "GetRisksTool", "QueryGraphTool"]
