"""Graph-oriented MCP tools.

`get_risks(entity)` — heuristic structural risk projection (kept in v2).

`query_graph` and `get_related_components` are DEPRECATED v1 aliases:
they delegate to the v2 `explore` internals so existing sessions keep
working while new sessions should call `explore` directly.
"""

from __future__ import annotations

from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import (
    GetRelatedComponentsRequest,
    GetRisksRequest,
    QueryGraphRequest,
)
from core.mcp.tools._helpers import (
    qname_suggestions,
    resolve_seed_unit,
)
from core.mcp.tools.explore_tool import _explore_impl


def _legacy_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    """v1-shaped candidate dicts derived from v2 neighbors.

    Kept so pre-v2 consumers (e.g. the SDK's `query_graph` wrapper,
    which reads `candidates[].unit_id`) keep working against the
    deprecated aliases.
    """
    return [
        {
            "unit_id": n.get("node_id"),
            "qualified_name": n.get("qualified_name"),
            "kind": n.get("kind"),
            "file_path": n.get("file_path"),
        }
        for n in result.get("neighbors", [])
    ]


class GetRisksTool:
    """Structural external-dependency risks for one entity."""

    name: str = "get_risks"
    description: str = (
        "List the EXTERNAL dependencies (third-party imports/calls) a "
        "symbol touches directly — a structural risk surface, e.g. "
        "get_risks(entity='core.embeddings.openai_embedder.OpenAIEmbedder', "
        "repo_id='memory-cl'). Use when assessing blast radius of a "
        "change or auditing third-party exposure. Structural only (no "
        "semantic analysis); for general neighbors use explore. "
        "Read-only."
    )
    request_schema = GetRisksRequest

    async def execute(
        self, request: GetRisksRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        seed = await resolve_seed_unit(
            ctx.state, repo_id=request.repo_id, reference=request.entity
        )
        if seed is None:
            suggestions = await qname_suggestions(
                ctx.state, request.repo_id, request.entity
            )
            return {
                "entity": request.entity,
                "found": False,
                "risks": [],
                "suggestions": suggestions,
                "hint": (
                    "Unknown entity. Closest qualified_names are in "
                    "`suggestions`; or use find_symbol(query=...)."
                ),
            }

        # 1-hop neighborhood straight from the graph repo — we want the
        # EXTERNAL targets that GraphRetriever's BFS intentionally drops.
        try:
            neighbors = await ctx.state.graph_repo.neighbors(
                seed.unit_id, depth=1
            )
        except Exception as exc:
            return {
                "entity": request.entity,
                "found": True,
                "risks": [],
                "warning": f"graph backend error: {exc}",
            }

        externals = [
            {
                "node_id": n.node_id,
                "qualified_name": n.qualified_name,
                "kind": n.kind.value,
            }
            for n in sorted(neighbors, key=lambda n: n.node_id)
            if n.kind.value == "External"
        ]
        return {
            "entity": request.entity,
            "found": True,
            "risks": externals,
            "risk_count": len(externals),
        }


class GetRelatedComponentsTool:
    """DEPRECATED alias for `explore`."""

    name: str = "get_related_components"
    description: str = (
        "DEPRECATED — use explore. Equivalent to explore(qualified_name="
        "<component>, repo_id=..., direction='all'): returns the graph "
        "neighborhood with content-bearing entries."
    )
    request_schema = GetRelatedComponentsRequest

    async def execute(
        self, request: GetRelatedComponentsRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        clamped_depth = min(request.depth, 5)
        result = await _explore_impl(
            ctx.state,
            reference=request.component,
            repo_id=request.repo_id,
            direction="all",
            depth=clamped_depth,
            request_id=ctx.request_id,
        )
        result["deprecated"] = "use explore"
        result["related"] = _legacy_candidates(result)  # v1 key
        if clamped_depth < request.depth:
            result["warning"] = (
                "depth clamped to 5 (v1 compat); use explore for deeper traversal"
            )
        return result


class QueryGraphTool:
    """DEPRECATED alias for `explore`."""

    name: str = "query_graph"
    description: str = (
        "DEPRECATED — use explore. Equivalent to explore(qualified_name="
        "<node>, repo_id=..., direction='all', depth=<depth>): returns "
        "the graph neighborhood plus the real directed edges."
    )
    request_schema = QueryGraphRequest

    async def execute(
        self, request: QueryGraphRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        clamped_depth = min(request.depth, 5)
        result = await _explore_impl(
            ctx.state,
            reference=request.node,
            repo_id=request.repo_id,
            direction="all",
            depth=clamped_depth,
            request_id=ctx.request_id,
        )
        result["deprecated"] = "use explore"
        result["candidates"] = _legacy_candidates(result)  # v1 key
        if clamped_depth < request.depth:
            result["warning"] = (
                "depth clamped to 5 (v1 compat); use explore for deeper traversal"
            )
        return result


__all__ = ["GetRelatedComponentsTool", "GetRisksTool", "QueryGraphTool"]
