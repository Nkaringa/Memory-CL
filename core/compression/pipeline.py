from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.compression.context import CompressionContext
from core.compression.dense_encoder import DenseEncoder, EncodedUnit
from core.compression.logevent import emit_phase3_event
from core.embeddings.chunking_strategy import ChunkingStrategy
from core.embeddings.embedder import Embedder
from core.embeddings.embedding_pipeline import EmbeddingPipeline
from core.observability import get_tracer
from core.summarization import ApiSummarizer, GraphSummarizer, ModuleSummarizer
from schemas import (
    DenseApi,
    DenseGraphSlice,
    DenseModule,
    EmbeddingChunk,
    GraphEdge,
    GraphNode,
    IngestionUnit,
)

_tracer = get_tracer("core.compression.pipeline")


@dataclass(frozen=True, slots=True)
class CompressionResult:
    """Compact output of one CompressionPipeline.run().

    The dense records are kept in-memory so the API layer can serve
    them directly to retrieval (Phase 4) without a round-trip to
    storage. Vectors are written to Qdrant inside `run()`.
    """

    encoded_units: tuple[EncodedUnit, ...]
    dense_modules: tuple[DenseModule, ...]
    dense_apis: tuple[DenseApi, ...]
    dense_graph_slices: tuple[DenseGraphSlice, ...]
    chunks: tuple[EmbeddingChunk, ...]
    metrics: dict[str, float | int]
    degraded_unit_ids: tuple[str, ...]


class CompressionPipeline:
    """Phase-3 orchestrator: dense-encode + summarize + embed.

    Failure isolation per spec: if encoding a single unit fails, the
    unit is added to `degraded_unit_ids` and the rest of the pipeline
    continues. The summarizers and the embedder operate on the
    survivors.
    """

    def __init__(
        self,
        *,
        encoder: DenseEncoder | None = None,
        module_summarizer: ModuleSummarizer | None = None,
        api_summarizer: ApiSummarizer | None = None,
        graph_summarizer: GraphSummarizer | None = None,
        chunker: ChunkingStrategy,
        embedder: Embedder,
    ) -> None:
        self._encoder = encoder or DenseEncoder()
        self._module_summarizer = module_summarizer or ModuleSummarizer()
        self._api_summarizer = api_summarizer or ApiSummarizer()
        self._graph_summarizer = graph_summarizer or GraphSummarizer()
        self._chunker = chunker
        self._embedder = embedder

    async def run(
        self,
        ctx: CompressionContext,
        *,
        units: Sequence[IngestionUnit],
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
    ) -> CompressionResult:
        run_start = time.perf_counter()
        with _tracer.start_as_current_span("compression.run") as span:
            span.set_attribute("repo_id", ctx.repo_id)
            span.set_attribute("commit_sha", ctx.commit_sha)
            span.set_attribute("units", len(units))

            emit_phase3_event(
                event="compression_start",
                operation="compress",
                status="success",
                duration_ms=0.0,
                level="info",
                repo_id=ctx.repo_id,
                commit_sha=ctx.commit_sha,
                unit_count=len(units),
            )

            # ---- Stage 1: dense encode (per-unit failure isolation) ----
            encoded: list[EncodedUnit] = []
            degraded: list[str] = []
            ordered_units = sorted(units, key=lambda u: u.unit_id)
            for u in ordered_units:
                try:
                    encoded.append(self._encoder.encode_unit(u))
                except Exception as exc:
                    degraded.append(u.unit_id)
                    emit_phase3_event(
                        event="dense_encode_failed",
                        operation="encode",
                        status="degraded",
                        duration_ms=0.0,
                        unit_id=u.unit_id,
                        level="error",
                        error=str(exc),
                    )

            healthy_units = [u for u in ordered_units if u.unit_id not in set(degraded)]
            ctx.metrics.units_encoded = len(encoded)
            ctx.metrics.bytes_input = sum(e.bytes_input for e in encoded)
            ctx.metrics.bytes_output = sum(e.bytes_output for e in encoded)

            # ---- Stage 2: structural summarization ----
            dense_modules = tuple(self._module_summarizer.summarize(healthy_units))
            ctx.metrics.modules_summarized = len(dense_modules)

            dense_apis = tuple(self._api_summarizer.summarize(healthy_units))
            ctx.metrics.apis_summarized = len(dense_apis)

            dense_slices = tuple(self._graph_summarizer.summarize(nodes, edges))
            ctx.metrics.graph_slices = len(dense_slices)

            # ---- Stage 3: chunk + embed + write ----
            embedding_pipe = EmbeddingPipeline(
                embedder=self._embedder,
                chunker=self._chunker,
                vector_repo=ctx.vector_repo,
            )
            embedding_res = await embedding_pipe.run(
                healthy_units, collection=ctx.units_collection
            )
            ctx.metrics.chunks_emitted = len(embedding_res.chunks)
            ctx.metrics.embeddings_written = embedding_res.vectors_written

            ctx.metrics.duration_ms = (time.perf_counter() - run_start) * 1000
            metrics_dict = ctx.metrics.as_dict()
            span.set_attribute("token_reduction_ratio",
                               metrics_dict["token_reduction_ratio"])

            emit_phase3_event(
                event="compression_end",
                operation="compress",
                status="degraded" if degraded else "success",
                duration_ms=ctx.metrics.duration_ms,
                level="info",
                token_reduction_ratio=metrics_dict["token_reduction_ratio"],
                degraded=len(degraded),
                **{k: v for k, v in metrics_dict.items()
                   if k not in {"duration_ms", "token_reduction_ratio"}},
            )

            return CompressionResult(
                encoded_units=tuple(encoded),
                dense_modules=dense_modules,
                dense_apis=dense_apis,
                dense_graph_slices=dense_slices,
                chunks=embedding_res.chunks,
                metrics=metrics_dict,
                degraded_unit_ids=tuple(sorted(set(degraded))),
            )
