"""Shared helpers for MCP tools.

These helpers exist so each tool stays a thin wrapper. They build
Phase-4 retrievers from the live AppState — same wiring pattern as
`apps/api/routers/retrieve.py`, kept in one place to avoid drift.

The v2 section (below `build_ranking_model`) carries the agent-first
plumbing every v2 tool shares: unit hydration from read-only SQL,
token-aware snippets, reference resolution, and the "teaching error"
payload builders (unknown repo → valid ids; unknown qname → closest
matches). Everything here is read-only composition over Phase 2-4.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.context import ContextAssembler
from core.context.context_assembler import AssemblyOptions
from core.embeddings.chunking_strategy import estimate_tokens
from core.ranking import RankingModel
from core.retrieval import (
    GraphRetriever,
    HybridRetriever,
    MetadataRetriever,
    QueryPlanner,
    VectorRetriever,
)
from schemas import IngestionUnit, Language, UnitKind

# Hard ceiling on the estimated token size of any single tool response.
# When the budget is exhausted the tool sets `truncated: true` and says
# how to narrow the call instead of silently dropping data.
MAX_RESPONSE_TOKENS = 8_000

# Default snippet length for search/explore hits.
SNIPPET_LINES = 40


def build_hybrid_retriever(state: Any, *, repo_id: str, max_depth: int) -> HybridRetriever:
    """Compose a HybridRetriever for `repo_id` from the live AppState.

    `state` is `apps.api.state.AppState` — typed loosely to keep this
    helper free of an upward apps→core import.
    """
    return HybridRetriever(
        planner=QueryPlanner(default_max_depth=max_depth),
        graph=GraphRetriever(state.graph_repo, max_depth=max_depth),
        vector=VectorRetriever(
            client=state.qdrant.client,
            embedder=state.embedder,
            collection=f"repo_{repo_id}",
        ),
        metadata=MetadataRetriever(state.postgres.engine),
    )


def build_assembler(*, max_context_tokens: int) -> ContextAssembler:
    return ContextAssembler(
        options=AssemblyOptions(max_context_tokens=max_context_tokens),
    )


def build_ranking_model() -> RankingModel:
    return RankingModel()


# ---------------------------------------------------------------------------
# v2 shared internals
# ---------------------------------------------------------------------------
def looks_like_unit_id(value: str) -> bool:
    """unit_ids are 64-char lowercase hex (sha256). Cheap, collision-safe."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def escape_like(query: str) -> str:
    """Escape LIKE/ILIKE metacharacters so user input matches literally.

    Mirrors `storage.postgres_repo._escape_like` — duplicated here so
    `core` keeps zero imports from the storage package.
    """
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _arr(v: Any) -> list[str]:
    """Array column tolerant of both backends: Postgres TEXT[] arrives as a
    list; lite/SQLite stores it as a JSON string."""
    if isinstance(v, str):
        return json.loads(v) if v else []
    return list(v or [])


def _ts(v: Any) -> Any:
    """Timestamp column: Postgres gives a datetime; SQLite gives ISO TEXT."""
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


def hydrate_unit(mapping: Any) -> IngestionUnit:
    """Hydrate an IngestionUnit from a SQLAlchemy row / row mapping."""
    m = mapping._mapping if hasattr(mapping, "_mapping") else mapping
    return IngestionUnit(
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
        imports=_arr(m["imports"]),
        calls=_arr(m["calls"]),
        references=_arr(m["references"]),
        bases=_arr(m["bases"]),
        token_count=m["token_count"],
        schema_version=m["schema_version"],
        created_at=_ts(m["created_at"]),
        updated_at=_ts(m["updated_at"]),
        source=m["source"],
        checksum=m["checksum"],
    )


async def fetch_unit_by_qname(
    engine: Any, repo_id: str, qname: str
) -> IngestionUnit | None:
    """Exact qualified_name → unit, via read-only SQL on the canonical store."""
    from sqlalchemy import text

    sql = text(
        "SELECT * FROM ingestion_units "
        " WHERE repo_id = :repo_id AND qualified_name = :qname "
        " ORDER BY line_start LIMIT 1"
    )
    async with engine.connect() as conn:
        result = await conn.execute(sql, {"repo_id": repo_id, "qname": qname})
        row = result.first()
    return hydrate_unit(row) if row is not None else None


async def repo_ids(state: Any) -> list[str]:
    """Sorted ids of every ingested repo (deterministic fan-in order)."""
    summaries = await state.units_repo.list_repos()
    return sorted(s.repo_id for s in summaries)


async def qname_suggestions(
    state: Any, repo_id: str, query: str, limit: int = 5
) -> list[dict[str, str]]:
    """Closest qualified_names for a miss — powers teaching errors.

    Tries the full query first, then its last dotted segment (agents
    often know the symbol name but guess the module path wrong).
    """
    try:
        matches = list(
            await state.units_repo.search_qnames(repo_id, query, limit=limit)
        )
        if not matches and "." in query:
            tail = query.rsplit(".", 1)[-1]
            if tail:
                matches = list(
                    await state.units_repo.search_qnames(
                        repo_id, tail, limit=limit
                    )
                )
    except Exception:
        return []
    return [
        {"qualified_name": m.qualified_name, "kind": m.kind} for m in matches
    ]


async def unknown_repo_payload(state: Any, repo_id: str) -> dict[str, Any]:
    """Teaching-error payload for an unknown repo_id."""
    valid = await repo_ids(state)
    return {
        "found": False,
        "error": f"unknown repo_id '{repo_id}'",
        "valid_repo_ids": valid,
        "hint": (
            "Call list_repos() to see every ingested repo with its "
            "unit/file counts, then retry with one of valid_repo_ids."
            if valid
            else "No repositories are ingested yet — use ingest_repository "
            "to add one."
        ),
    }


def snippet_of(
    content: str | None, max_lines: int = SNIPPET_LINES
) -> tuple[str, bool]:
    """First `max_lines` lines of `content` plus a truncation flag."""
    if not content:
        return "", False
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content.rstrip("\n"), False
    return "\n".join(lines[:max_lines]), True


def one_line_of(content: str | None) -> str:
    """First non-empty line — the 'what is this' glance for neighbors."""
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def line_range(unit: IngestionUnit) -> str:
    return f"{unit.line_start}-{unit.line_end}"


def truncate_to_budget(text_value: str, budget_tokens: int) -> tuple[str, bool]:
    """Clip `text_value` to roughly `budget_tokens` (4 chars ≈ 1 token)."""
    if estimate_tokens(text_value) <= budget_tokens:
        return text_value, False
    max_chars = max(budget_tokens, 1) * 4
    clipped = text_value[:max_chars]
    # Cut at the last full line so the agent never sees half a statement.
    if "\n" in clipped:
        clipped = clipped[: clipped.rfind("\n")]
    return clipped, True


async def resolve_seed_unit(
    state: Any, *, repo_id: str, reference: str
) -> IngestionUnit | None:
    """Resolve a qualified_name or unit_id to its IngestionUnit."""
    if looks_like_unit_id(reference):
        # A unit_id is globally unique — accept a cross-repo hit too so
        # ids copied from multi-repo search results just work.
        unit: IngestionUnit | None = await state.units_repo.get_unit(reference)
        return unit
    return await fetch_unit_by_qname(state.postgres.engine, repo_id, reference)


__all__ = [
    "MAX_RESPONSE_TOKENS",
    "SNIPPET_LINES",
    "build_assembler",
    "build_hybrid_retriever",
    "build_ranking_model",
    "escape_like",
    "estimate_tokens",
    "fetch_unit_by_qname",
    "hydrate_unit",
    "line_range",
    "looks_like_unit_id",
    "one_line_of",
    "qname_suggestions",
    "repo_ids",
    "resolve_seed_unit",
    "snippet_of",
    "truncate_to_budget",
    "unknown_repo_payload",
]
