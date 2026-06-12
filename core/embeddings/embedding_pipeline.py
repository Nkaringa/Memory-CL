from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.compression.logevent import emit_phase3_event
from core.embeddings.chunking_strategy import ChunkingStrategy
from core.embeddings.embedder import Embedder
from core.observability import get_tracer
from schemas import EmbeddingChunk, IngestionUnit
from storage.repositories import VectorPoint, VectorRepository

_tracer = get_tracer("core.embeddings.embedding_pipeline")


def _embed_text(unit: IngestionUnit, chunk: EmbeddingChunk) -> str:
    """Text actually embedded for a unit's primary chunk.

    Prepends a deterministic identity header — qualified name, kind,
    and signature — so retrieval queries that mention a symbol by name
    match even when the chunk body doesn't repeat it. The header uses
    the same `or ''` line for missing signatures to keep the layout
    byte-stable across kinds.
    """
    return (
        f"{unit.qualified_name} ({unit.kind.value})\n"
        f"{unit.signature or ''}\n"
        f"{chunk.content}"
    )


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    chunks: tuple[EmbeddingChunk, ...]
    vectors_written: int


class EmbeddingPipeline:
    """Chunk → embed → upsert to Qdrant.

    Phase-3 invariant: there is exactly one written `VectorPoint` per
    `IngestionUnit`, with `point_id == unit_id`. The unit's vector is
    the embedding of its first chunk (which carries the signature +
    leading lines, the highest-density slice for retrieval). Multi-
    chunk indexing — needed for granular retrieval over very large
    units — is a Phase-4 enhancement and intentionally NOT enabled
    here.
    """

    def __init__(
        self,
        *,
        embedder: Embedder,
        chunker: ChunkingStrategy,
        vector_repo: VectorRepository,
    ) -> None:
        self._embedder = embedder
        self._chunker = chunker
        self._vector_repo = vector_repo

    async def run(
        self,
        units: Sequence[IngestionUnit],
        *,
        collection: str,
    ) -> EmbeddingResult:
        start = time.perf_counter()
        with _tracer.start_as_current_span("embedding_pipeline.run") as span:
            # Determinism: process units in unit_id order.
            ordered = sorted(units, key=lambda u: u.unit_id)
            span.set_attribute("count", len(ordered))
            span.set_attribute("collection", collection)

            await self._vector_repo.ensure_collection(
                collection, self._embedder.dimension
            )

            all_chunks: list[EmbeddingChunk] = []
            primary_chunks: list[EmbeddingChunk] = []
            primary_texts: list[str] = []
            for u in ordered:
                chunks = self._chunker.chunk_unit(u)
                all_chunks.extend(chunks)
                if chunks:
                    primary_chunks.append(chunks[0])
                    primary_texts.append(_embed_text(u, chunks[0]))

            # Batch-embed the primary chunks (one per unit), each with
            # its identity header prepended.
            vectors = await self._embedder.embed_batch(primary_texts)

            points: list[VectorPoint] = []
            primary_by_unit: dict[str, tuple[EmbeddingChunk, tuple[float, ...]]] = {}
            for chunk, vector in zip(primary_chunks, vectors, strict=True):
                primary_by_unit[chunk.unit_id] = (chunk, vector)

            for u in ordered:
                if u.unit_id not in primary_by_unit:
                    # Empty content (rare — usually __init__.py with
                    # docstring only). Emit a payload-only point so the
                    # cross-store identity invariant still holds.
                    points.append(
                        VectorPoint(
                            point_id=u.unit_id,
                            repo_id=u.repo_id,
                            qualified_name=u.qualified_name,
                            kind=u.kind.value,
                            file_path=u.file_path,
                            line_start=u.line_start,
                            line_end=u.line_end,
                            commit_sha=u.commit_sha,
                            source_sha=u.source_sha,
                            vector=None,
                        )
                    )
                    continue
                _, vec = primary_by_unit[u.unit_id]
                points.append(
                    VectorPoint(
                        point_id=u.unit_id,
                        repo_id=u.repo_id,
                        qualified_name=u.qualified_name,
                        kind=u.kind.value,
                        file_path=u.file_path,
                        line_start=u.line_start,
                        line_end=u.line_end,
                        commit_sha=u.commit_sha,
                        source_sha=u.source_sha,
                        vector=vec,
                    )
                )

            written = await self._vector_repo.upsert_payloads(collection, points)

            # Record vectors back into the chunk objects so callers can
            # consume them downstream (e.g. for storing chunk-level
            # caches in Phase 4).
            enriched_chunks: list[EmbeddingChunk] = []
            for c in all_chunks:
                if c.unit_id in primary_by_unit and primary_by_unit[c.unit_id][0].chunk_id == c.chunk_id:
                    enriched_chunks.append(c.model_copy(update={"vector": primary_by_unit[c.unit_id][1]}))
                else:
                    enriched_chunks.append(c)

            duration_ms = (time.perf_counter() - start) * 1000
            emit_phase3_event(
                event="embedding_run",
                operation="embed",
                status="success",
                duration_ms=duration_ms,
                level="info",
                units=len(ordered),
                chunks=len(all_chunks),
                vectors_written=written,
                embedder=self._embedder.name,
            )
            return EmbeddingResult(
                chunks=tuple(enriched_chunks),
                vectors_written=written,
            )
