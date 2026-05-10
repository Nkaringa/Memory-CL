from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from storage.repositories import VectorPoint

_tracer = get_tracer("storage.qdrant_repo")

# Fixed namespace for derive-UUID-from-unit-id. Arbitrary uuid4 chosen
# at design time and pinned forever — same input must always produce
# the same UUID across every deploy. Do NOT regenerate this value.
_QDRANT_POINT_NAMESPACE = uuid.UUID("e8c4b8c3-3e5f-4d3a-8b1d-d8b3c4a2c1f0")


def _to_qdrant_point_id(unit_id: str) -> str:
    """Translate a Memory-CL ``unit_id`` (SHA-256 hex string) into a
    qdrant-accepted point ID.

    Qdrant ≥1.7 enforces that point IDs are either an unsigned integer
    or a UUID — bare hex strings get rejected with HTTP 400
    "Format error in JSON body: value <X> is not a valid point ID".

    We use ``uuid5`` over a pinned namespace because:

    * deterministic — same unit_id always yields the same UUID
    * collision-resistant for any practical corpus size
    * the original unit_id is preserved in the payload, so retrieval
      can join back to Postgres on the real key (see
      `core/retrieval/vector_retriever.py`)
    """
    return str(uuid.uuid5(_QDRANT_POINT_NAMESPACE, unit_id))


def _payload(point: VectorPoint) -> dict[str, Any]:
    """Stable payload schema written by Phase 2.

    Keys are sorted by Pydantic at write time? No — we control the dict
    here, so we sort manually to keep determinism guarantees.

    `unit_id` is included so retrieval can map a qdrant hit back to the
    Postgres `ingestion_units` row by the same key the rest of the
    system uses. The qdrant point's `id` field carries a derived UUID
    (qdrant rejects raw hex strings), so the original `unit_id` MUST
    travel with the payload.
    """
    raw: dict[str, Any] = {
        "repo_id": point.repo_id,
        "qualified_name": point.qualified_name,
        "kind": point.kind,
        "file_path": point.file_path,
        "line_start": point.line_start,
        "line_end": point.line_end,
        "commit_sha": point.commit_sha,
        "source_sha": point.source_sha,
        "has_vector": point.vector is not None,
        "unit_id": point.point_id,
    }
    return {k: raw[k] for k in sorted(raw)}


class QdrantVectorRepository:
    """Concrete `VectorRepository` over the Phase-1 QdrantStorageClient.

    Phase 2 writes payloads only. The collection's vector slot is filled
    with a deterministic placeholder (a zero vector) so retrieval cannot
    accidentally surface partially-indexed units before Phase 3 has had a
    chance to embed them. Search is intentionally not implemented here —
    that's Phase 4's concern.
    """

    name: str = "qdrant_vector_repo"

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client
        self._size_cache: dict[str, int] = {}

    # ----- Bootstrap -----
    async def ensure_collection(self, name: str, vector_size: int) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size must be > 0")
        with _tracer.start_as_current_span("qdrant_repo.ensure_collection") as span:
            span.set_attribute("collection", name)
            span.set_attribute("vector_size", vector_size)
            try:
                exists = await self._client.collection_exists(collection_name=name)
            except AttributeError:
                # older qdrant-client versions
                cols = await self._client.get_collections()
                exists = any(c.name == name for c in cols.collections)
            if exists:
                self._size_cache[name] = vector_size
                return
            await self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            self._size_cache[name] = vector_size

    # ----- Writes -----
    async def upsert_payload(self, collection: str, point: VectorPoint) -> None:
        await self.upsert_payloads(collection, [point])

    async def upsert_payloads(
        self, collection: str, points: Iterable[VectorPoint]
    ) -> int:
        points = list(points)
        if not points:
            return 0
        start = time.perf_counter()
        # Determinism: same input -> identical batch.
        points.sort(key=lambda p: p.point_id)

        with _tracer.start_as_current_span("qdrant_repo.upsert_payloads") as span:
            span.set_attribute("collection", collection)
            span.set_attribute("count", len(points))

            placeholder = await self._placeholder_vector(collection)
            structs: list[PointStruct] = []
            for p in points:
                vector = list(p.vector) if p.vector else placeholder
                structs.append(
                    PointStruct(
                        id=_to_qdrant_point_id(p.point_id),
                        vector=vector,
                        payload=_payload(p),
                    )
                )
            await self._client.upsert(collection_name=collection, points=structs)
            emit_phase2_event(
                event="qdrant_upsert_payloads",
                operation="qdrant_repo.upsert_payloads",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                count=len(points),
                collection=collection,
                level="info",
            )
            return len(points)

    async def delete_points_for_file(
        self, collection: str, repo_id: str, file_path: str
    ) -> int:
        with _tracer.start_as_current_span("qdrant_repo.delete_points_for_file") as span:
            span.set_attribute("collection", collection)
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)
            await self._client.delete(
                collection_name=collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
                            FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                        ]
                    )
                ),
            )
        # Qdrant's delete API doesn't return a count; we surface 0 to honour
        # the protocol contract but log the operation for auditability.
        return 0

    # ----- Internal -----
    async def _placeholder_vector(self, collection: str) -> list[float]:
        """Return a zero-vector matching the collection's configured size.

        Size is cached after `ensure_collection`; if a caller skipped
        bootstrap we recover it via `get_collection`. Phase 3 replaces
        these placeholders with real embeddings; payload `has_vector`
        tells retrieval to ignore them until then.
        """
        size = self._size_cache.get(collection)
        if size is None:
            info = await self._client.get_collection(collection_name=collection)
            params = info.config.params
            # Qdrant supports either a single VectorParams or a dict of
            # named vectors. We assume single (matches ensure_collection).
            vectors_cfg = params.vectors
            size = vectors_cfg.size if hasattr(vectors_cfg, "size") else next(
                iter(vectors_cfg.values())
            ).size
            self._size_cache[collection] = size
        return [0.0] * size


__all__ = ["QdrantVectorRepository"]
