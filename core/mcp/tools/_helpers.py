"""Shared helpers for MCP tools.

These helpers exist so each tool stays a thin wrapper. They build
Phase-4 retrievers from the live AppState — same wiring pattern as
`apps/api/routers/retrieve.py`, kept in one place to avoid drift.
"""

from __future__ import annotations

from core.context import ContextAssembler
from core.context.context_assembler import AssemblyOptions
from core.ranking import RankingModel
from core.retrieval import (
    GraphRetriever,
    HybridRetriever,
    MetadataRetriever,
    QueryPlanner,
    VectorRetriever,
)


def build_hybrid_retriever(state, *, repo_id: str, max_depth: int) -> HybridRetriever:
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
            collection=f"repo:{repo_id}",
        ),
        metadata=MetadataRetriever(state.postgres.engine),
    )


def build_assembler(*, max_context_tokens: int) -> ContextAssembler:
    return ContextAssembler(
        options=AssemblyOptions(max_context_tokens=max_context_tokens),
    )


def build_ranking_model() -> RankingModel:
    return RankingModel()
