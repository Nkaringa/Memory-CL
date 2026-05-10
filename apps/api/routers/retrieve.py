from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep
from core import get_settings
from core.context import ContextAssembler
from core.context.context_assembler import AssemblyOptions
from core.ranking import RankingModel
from core.retrieval import (
    GraphRetriever,
    HybridRetriever,
    MetadataRetriever,
    QueryPlanner,
    RetrievalContext,
    VectorRetriever,
)
from core.retrieval.logevent import emit_phase4_event
from schemas import ContextPacket, Query

if TYPE_CHECKING:  # pragma: no cover
    from apps.api.state import AppState

router = APIRouter(prefix="/retrieve", tags=["retrieval"])


class RetrieveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(description="Deterministic id (sha256(repo_id + text))")
    repo_id: str
    packet: ContextPacket
    graph_hits: int
    vector_hits: int
    metadata_hits: int
    final_candidates: int
    ranked_count: int
    failed_channels: list[str]
    latency_ms: float


@router.post(
    "",
    response_model=RetrieveResponse,
    status_code=status.HTTP_200_OK,
)
async def retrieve(query: Query, state: AppStateDep) -> RetrieveResponse:
    """Run hybrid retrieval and return a packed context.

    The endpoint is purely deterministic given the same system state:
    same query text + same backend contents → identical packet bytes.
    Channel failures degrade gracefully (recorded in `failed_channels`).
    """
    start = time.perf_counter()
    settings = get_settings()
    ctx = RetrievalContext(repo_id=query.repo_id, query_text=query.text)

    hybrid = _build_hybrid(state, settings.max_graph_traversal_depth, query.repo_id)
    res = await hybrid.run(query, query_id=ctx.query_id)

    ranked = RankingModel().rank(
        res.candidates,
        top_k=query.top_k,
        query_id=ctx.query_id,
        repo_id=query.repo_id,
    )

    assembler = ContextAssembler(
        options=AssemblyOptions(max_context_tokens=settings.max_context_tokens),
    )
    packet = assembler.build(
        task=query.text,
        ranked=ranked,
        query_id=ctx.query_id,
        repo_id=query.repo_id,
    )

    elapsed = (time.perf_counter() - start) * 1000
    emit_phase4_event(
        event="retrieve_endpoint_done",
        operation="retrieve",
        status="degraded" if res.failed_channels else "success",
        latency_ms=elapsed,
        query_id=ctx.query_id,
        repo_id=query.repo_id,
        level="info",
        ranked=len(ranked),
        failed_channels=list(res.failed_channels),
    )

    return RetrieveResponse(
        query_id=ctx.query_id,
        repo_id=query.repo_id,
        packet=packet,
        graph_hits=res.graph_hits,
        vector_hits=res.vector_hits,
        metadata_hits=res.metadata_hits,
        final_candidates=len(res.candidates),
        ranked_count=len(ranked),
        failed_channels=list(res.failed_channels),
        latency_ms=elapsed,
    )


def _build_hybrid(
    state: AppState, max_depth: int, repo_id: str
) -> HybridRetriever:
    """Assemble the per-request HybridRetriever.

    The clients themselves are long-lived (lifespan-managed); each
    request creates fresh retriever wrappers around them. Wrappers are
    cheap, this keeps state per-request and makes mocking easy.
    """
    graph = GraphRetriever(state.graph_repo, max_depth=max_depth)
    vector = VectorRetriever(
        client=state.qdrant.client,
        embedder=state.embedder,
        collection=f"repo_{repo_id}",
    )
    metadata = MetadataRetriever(state.postgres.engine)
    return HybridRetriever(
        planner=QueryPlanner(default_max_depth=max_depth),
        graph=graph,
        vector=vector,
        metadata=metadata,
    )
