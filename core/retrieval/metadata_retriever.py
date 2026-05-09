from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer
from core.retrieval.logevent import emit_phase4_event
from schemas import RetrievalCandidate, RetrievalChannel

_tracer = get_tracer("core.retrieval.metadata_retriever")


# Plain-text scoring against the canonical Postgres store. SQLAlchemy
# binding keeps the literal `query_text` parameterised — no string
# interpolation, so the only attack surface is the regex-like LIKE
# pattern, which Postgres handles natively.
_KEYWORD_QUERY = text("""
    SELECT unit_id, repo_id, qualified_name, kind, file_path, line_start,
           line_end, source_sha, updated_at
      FROM ingestion_units
     WHERE repo_id = :repo_id
       AND (
            qualified_name ILIKE :pattern
         OR name ILIKE :pattern
         OR docstring ILIKE :pattern
         OR signature ILIKE :pattern
       )
       AND (kind = ANY(:kinds) OR :no_kind_filter)
     ORDER BY updated_at DESC, unit_id ASC
     LIMIT :limit
""").bindparams(bindparam("kinds", expanding=False))


class MetadataRetriever:
    """Keyword + filter retrieval over the canonical Postgres store.

    Phase-4's role for metadata is twofold:
        - exact-match boost for queries that mention concrete symbols
        - source of `updated_at` for the recency feature

    The retriever returns deterministic results: ORDER BY updated_at
    DESC, unit_id ASC, plus a hard LIMIT.
    """

    name: str = "metadata_retriever"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def search(
        self,
        query_text: str,
        repo_id: str,
        *,
        top_k: int,
        unit_kinds: Sequence[str] | None = None,
        query_id: str = "",
    ) -> list[RetrievalCandidate]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("metadata_retriever.query") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("top_k", top_k)

            kinds = list(unit_kinds or [])
            params = {
                "repo_id": repo_id,
                "pattern": f"%{query_text}%",
                "kinds": kinds,
                "no_kind_filter": len(kinds) == 0,
                "limit": max(top_k, 1),
            }
            try:
                async with self._engine.connect() as conn:
                    result = await conn.execute(_KEYWORD_QUERY, params)
                    rows = result.all()
            except Exception as exc:
                emit_phase4_event(
                    event="metadata_query_failed",
                    operation="metadata_query",
                    status="degraded",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    query_id=query_id,
                    repo_id=repo_id,
                    level="warning",
                    error=str(exc),
                )
                return []

            candidates: list[RetrievalCandidate] = []
            for row in rows:
                m = row._mapping if hasattr(row, "_mapping") else row
                # Metadata raw_score is a uniform 0.5 — the exact match
                # already happened in SQL, ranking adds the per-feature
                # weights downstream (recency, importance, semantic).
                candidates.append(
                    RetrievalCandidate(
                        unit_id=str(m["unit_id"]),
                        channel=RetrievalChannel.METADATA,
                        raw_score=0.5,
                        file_path=m.get("file_path"),
                        qualified_name=m.get("qualified_name"),
                        kind=m.get("kind"),
                        extra={
                            "line_start": int(m["line_start"]) if m.get("line_start") is not None else 0,
                            "line_end": int(m["line_end"]) if m.get("line_end") is not None else 0,
                        },
                    )
                )

            elapsed = (time.perf_counter() - start) * 1000
            span.set_attribute("hits", len(candidates))
            emit_phase4_event(
                event="metadata_query_done",
                operation="metadata_query",
                status="success",
                latency_ms=elapsed,
                query_id=query_id,
                repo_id=repo_id,
                level="debug",
                hits=len(candidates),
            )
            return candidates


def _row_get(row: Any, key: str) -> Any:
    """Tolerant accessor for raw asyncpg rows (mappings) and SA rows."""
    if hasattr(row, "_mapping"):
        return row._mapping[key]
    return row[key]


__all__ = ["MetadataRetriever"]
