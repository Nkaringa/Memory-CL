"""Context-related MCP tools.

`get_module_summary`  — DenseModule for a module qname (kept in v2)
`get_context`         — DEPRECATED alias delegating to the v2
                        `search_code` internals

Both are orchestration wrappers — they call only Phase 2-4 systems and
add no new business logic.
"""

from __future__ import annotations

from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import GetContextRequest, GetModuleSummaryRequest
from core.mcp.tools._helpers import hydrate_unit, qname_suggestions
from core.mcp.tools.search_tool import _search_impl
from core.summarization import ModuleSummarizer
from schemas import IngestionUnit


class GetContextTool:
    """DEPRECATED alias for `search_code`."""

    name: str = "get_context"
    description: str = (
        "DEPRECATED — use search_code. Equivalent to search_code("
        "question=<task>, repo_id=..., top_k=...): hybrid retrieval "
        "whose hits include file:line and code snippets."
    )
    request_schema = GetContextRequest

    async def execute(
        self, request: GetContextRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        # `scope` is treated as a soft alias for `repo_id` if the caller
        # supplied one — the explicit `repo_id` always wins.
        repo_id = request.repo_id or request.scope
        result = await _search_impl(
            ctx.state,
            question=request.task,
            repo_id=repo_id,
            top_k=min(request.top_k, 50),
            request_id=ctx.request_id,
        )
        result["deprecated"] = "use search_code"
        return result


class GetModuleSummaryTool:
    """`get_module_summary(module)` — DenseModule via Phase-2 + Phase-3 paths.

    Implementation note: we never bypass storage — we read units via a
    read-only projection over `ingestion_units`, NOT a new ingestion
    pathway.
    """

    name: str = "get_module_summary"
    description: str = (
        "Get a dense structural summary of one module: its classes, "
        "functions, imports and doc in a compact machine-readable form, "
        "e.g. get_module_summary(module='core.retrieval.hybrid_retriever', "
        "repo_id='memory-cl'). Cheaper than read_file when you only need "
        "the shape of a module, not its source. For full code use "
        "read_file/read_unit. Read-only."
    )
    request_schema = GetModuleSummaryRequest

    async def execute(
        self, request: GetModuleSummaryRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        # Walk the canonical store directly through the engine that
        # Phase-2 already exposes — this is a read-only projection over
        # ingestion_units, NOT a new ingestion pathway.
        units = await _fetch_module_units(
            ctx.state.postgres.engine, request.repo_id, request.module
        )
        if not units:
            suggestions = await qname_suggestions(
                ctx.state, request.repo_id, request.module
            )
            return {
                "module": request.module,
                "found": False,
                "summary": None,
                "suggestions": suggestions,
                "hint": (
                    "Unknown module. Closest qualified_names are in "
                    "`suggestions`; module qnames also appear in "
                    "repo_overview's module_tree."
                ),
            }

        [summary] = _summarize_module(units, module_qname=request.module)
        return {
            "module": request.module,
            "found": True,
            "summary": summary.model_dump(mode="json"),
            "unit_count": len(units),
        }


async def _fetch_module_units(
    engine: Any, repo_id: str, module_qname: str
) -> list[IngestionUnit]:
    """Read all units belonging to `module_qname` (the module + descendants).

    Reads only — no DDL, no writes. Uses the same column set + table
    name as the Phase-2 PostgresIngestionRepository so the row → model
    hydration mirrors the existing helper.
    """
    from sqlalchemy import text

    sql = text("""
        SELECT * FROM ingestion_units
         WHERE repo_id = :repo_id
           AND (qualified_name = :qname OR qualified_name LIKE :prefix)
         ORDER BY line_start, qualified_name
    """)
    async with engine.connect() as conn:
        result = await conn.execute(
            sql,
            {
                "repo_id": repo_id,
                "qname": module_qname,
                "prefix": f"{module_qname}.%",
            },
        )
        rows = result.all()
    return [hydrate_unit(row) for row in rows]


def _summarize_module(units: list[IngestionUnit], *, module_qname: str) -> list[Any]:
    summaries = ModuleSummarizer().summarize(units)
    # The summarizer already groups by enclosing module. Filter to the
    # requested qname so a caller asking for `pkg` doesn't get `pkg.utils`.
    matching = [s for s in summaries if s.id == module_qname]
    if not matching:
        # Fall back to the first summary — handles single-module repos.
        matching = summaries[:1]
    return matching


__all__ = ["GetContextTool", "GetModuleSummaryTool"]
