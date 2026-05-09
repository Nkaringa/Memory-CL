from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, Protocol

from core.embeddings.embedder import Embedder
from core.observability import get_tracer
from core.retrieval.logevent import emit_phase4_event
from schemas import RetrievalCandidate, RetrievalChannel

_tracer = get_tracer("core.retrieval.vector_retriever")


class VectorSearchClient(Protocol):
    """Subset of the Qdrant async client we depend on.

    Defining a Protocol makes it trivial to swap in a fake during
    tests and avoids importing a concrete client class from `core/`,
    which would have crossed a layer boundary.
    """

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        query_filter: object | None = None,
        with_payload: bool = True,
    ) -> Sequence[Any]: ...


class VectorRetriever:
    """Cosine top-k search via Phase-3 dense embeddings.

    Optional `unit_kind` filter is applied client-side so we don't need
    a Qdrant `Filter` import in this layer (keeping the dependency
    surface minimal and decoupled from qdrant-client model changes).
    """

    name: str = "vector_retriever"

    def __init__(
        self,
        *,
        client: VectorSearchClient,
        embedder: Embedder,
        collection: str,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._collection = collection

    async def search(
        self,
        query_text: str,
        *,
        top_k: int,
        unit_kinds: Sequence[str] | None = None,
        query_id: str = "",
        repo_id: str = "",
    ) -> list[RetrievalCandidate]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("vector_retriever.search") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("collection", self._collection)
            span.set_attribute("top_k", top_k)

            [vector] = await self._embedder.embed_batch([query_text])

            try:
                hits = await self._client.search(
                    collection_name=self._collection,
                    query_vector=list(vector),
                    limit=max(top_k, 1),
                    with_payload=True,
                )
            except Exception as exc:
                emit_phase4_event(
                    event="vector_search_failed",
                    operation="vector_search",
                    status="degraded",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    query_id=query_id,
                    repo_id=repo_id,
                    level="warning",
                    error=str(exc),
                )
                return []

            allowed = set(unit_kinds or [])
            candidates: list[RetrievalCandidate] = []
            for h in hits:
                payload = _payload_of(h)
                if allowed and payload.get("kind") not in allowed:
                    continue
                # Skip points without a real vector (Phase-2 placeholders).
                if payload.get("has_vector") is False:
                    continue
                candidates.append(
                    RetrievalCandidate(
                        unit_id=str(_id_of(h)),
                        channel=RetrievalChannel.VECTOR,
                        raw_score=_score_to_unit_interval(_score_of(h)),
                        file_path=payload.get("file_path"),
                        qualified_name=payload.get("qualified_name"),
                        kind=payload.get("kind"),
                    )
                )

            # Determinism: stable sort by (-raw_score, unit_id) — Qdrant's
            # ordering is correct but we re-sort to avoid relying on
            # backend-specific tie-breakers.
            candidates.sort(key=lambda c: (-c.raw_score, c.unit_id))
            candidates = candidates[:top_k]

            span.set_attribute("hits", len(candidates))
            emit_phase4_event(
                event="vector_search_done",
                operation="vector_search",
                status="success",
                latency_ms=(time.perf_counter() - start) * 1000,
                query_id=query_id,
                repo_id=repo_id,
                level="debug",
                hits=len(candidates),
            )
            return candidates


def _id_of(hit: object) -> object:
    return getattr(hit, "id", None) or (
        hit["id"] if isinstance(hit, dict) else None
    )


def _score_of(hit: object) -> float:
    return float(getattr(hit, "score", None)
                 if not isinstance(hit, dict) else hit.get("score", 0.0))


def _payload_of(hit: object) -> dict[str, Any]:
    payload = getattr(hit, "payload", None)
    if payload is None and isinstance(hit, dict):
        payload = hit.get("payload")
    return dict(payload or {})


def _score_to_unit_interval(score: float) -> float:
    """Map cosine similarity from Qdrant into [0, 1].

    For unit-norm vectors, cosine ∈ [-1, 1]. Negative correlations are
    clipped at 0 so they never contribute to retrieval ranking.
    """
    if score < 0:
        return 0.0
    if score > 1:
        return 1.0
    return score


__all__ = ["VectorRetriever", "VectorSearchClient"]
