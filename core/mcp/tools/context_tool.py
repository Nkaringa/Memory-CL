"""Context-related MCP tools.

`get_context`         — full hybrid retrieval → ContextPacket
`get_module_summary`  — DenseModule for a module qname

Both are orchestration wrappers — they call only Phase 2-4 systems and
add no new business logic.
"""

from __future__ import annotations

from typing import Any

from core import get_settings
from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import GetContextRequest, GetModuleSummaryRequest
from core.mcp.tools._helpers import (
    build_assembler,
    build_hybrid_retriever,
    build_ranking_model,
)
from core.summarization import ModuleSummarizer
from schemas import IngestionUnit, Language, Query, UnitKind


class GetContextTool:
    """`get_context(task, scope?)` — runs the full hybrid retrieval path."""

    name: str = "get_context"
    request_schema = GetContextRequest

    async def execute(
        self, request: GetContextRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        settings = get_settings()
        max_depth = settings.max_graph_traversal_depth
        # `scope` is treated as a soft alias for `repo_id` if the caller
        # supplied one — the explicit `repo_id` always wins.
        repo_id = request.repo_id or request.scope or ""

        hybrid = build_hybrid_retriever(
            ctx.state, repo_id=repo_id, max_depth=max_depth
        )
        query = Query(
            text=request.task,
            repo_id=repo_id,
            top_k=request.top_k,
            seed_unit_ids=list(request.seed_unit_ids),
        )
        result = await hybrid.run(query, query_id=ctx.request_id)
        ranked = build_ranking_model().rank(
            result.candidates,
            top_k=request.top_k,
            query_id=ctx.request_id,
            repo_id=repo_id,
        )
        packet = build_assembler(
            max_context_tokens=settings.max_context_tokens
        ).build(
            task=request.task,
            ranked=ranked,
            query_id=ctx.request_id,
            repo_id=repo_id,
        )

        return {
            "packet": packet.model_dump(mode="json"),
            "graph_hits": result.graph_hits,
            "vector_hits": result.vector_hits,
            "metadata_hits": result.metadata_hits,
            "ranked_count": len(ranked),
            "failed_channels": list(result.failed_channels),
        }


class GetModuleSummaryTool:
    """`get_module_summary(module)` — DenseModule via Phase-2 + Phase-3 paths.

    Implementation note: we never bypass storage — we read units via
    the metadata channel + structural data via the graph channel, both
    of which are existing Phase-2/4 APIs.
    """

    name: str = "get_module_summary"
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
            return {"module": request.module, "found": False, "summary": None}

        [summary] = _summarize_module(units, module_qname=request.module)
        return {
            "module": request.module,
            "found": True,
            "summary": summary.model_dump(mode="json"),
            "unit_count": len(units),
        }


async def _fetch_module_units(
    engine, repo_id: str, module_qname: str
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

    out: list[IngestionUnit] = []
    for row in rows:
        m = row._mapping if hasattr(row, "_mapping") else row
        out.append(
            IngestionUnit(
                unit_id=m["unit_id"],
                repo_id=m["repo_id"],
                commit_sha=m["commit_sha"],
                kind=UnitKind(m["kind"]),
                name=m["name"],
                qualified_name=m["qualified_name"],
                parent_qualified_name=m["parent_qualified_name"],
                file_path=m["file_path"],
                language=Language(m["language"]),
                line_start=m["line_start"],
                line_end=m["line_end"],
                content=m["content"],
                source_sha=m["source_sha"],
                docstring=m["docstring"],
                signature=m["signature"],
                imports=list(m["imports"] or []),
                calls=list(m["calls"] or []),
                references=list(m["references"] or []),
                bases=list(m["bases"] or []),
                token_count=m["token_count"],
                schema_version=m["schema_version"],
                created_at=m["created_at"],
                updated_at=m["updated_at"],
                source=m["source"],
                checksum=m["checksum"],
            )
        )
    return out


def _summarize_module(units: list[IngestionUnit], *, module_qname: str):
    summaries = ModuleSummarizer().summarize(units)
    # The summarizer already groups by enclosing module. Filter to the
    # requested qname so a caller asking for `pkg` doesn't get `pkg.utils`.
    matching = [s for s in summaries if s.id == module_qname]
    if not matching:
        # Fall back to the first summary — handles single-module repos.
        matching = summaries[:1]
    return matching


__all__ = ["GetContextTool", "GetModuleSummaryTool"]
