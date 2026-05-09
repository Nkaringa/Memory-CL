from __future__ import annotations

import time
from collections.abc import Sequence

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer
from schemas import EmbeddingChunk, IngestionUnit

_tracer = get_tracer("core.embeddings.chunking_strategy")

# Phase 3 uses a deterministic, tokenizer-free heuristic: ~4 chars per
# token (close to BPE for English code/text). This avoids pulling in
# `tiktoken` or a model-specific tokenizer for the cost of a small
# constant approximation. The retrieval layer (Phase 4) can swap in a
# real tokenizer behind the same interface without changing chunk IDs.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Conservative ceil(len/4) token estimate."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN if text else 0


class ChunkingStrategy:
    """Deterministic, overlap-aware text chunker.

    Inputs: an `IngestionUnit` plus token-budget knobs from settings
    (`chunk_size`, `chunk_overlap`). Output: ordered list of
    `EmbeddingChunk`s sharing a stable id of the form `<unit_id>#cN`.

    Determinism: chunk boundaries are computed purely from `len(content)`
    and the byte-fixed knobs, so the same input always produces the
    same output.
    """

    def __init__(self, *, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be in [0, chunk_size)")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._chars_per_chunk = chunk_size * CHARS_PER_TOKEN
        self._chars_overlap = chunk_overlap * CHARS_PER_TOKEN

    def chunk_unit(self, unit: IngestionUnit) -> list[EmbeddingChunk]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("chunking.chunk_unit") as span:
            span.set_attribute("unit_id", unit.unit_id)
            content = unit.content
            chunks: list[EmbeddingChunk] = []
            if not content:
                return chunks

            stride = self._chars_per_chunk - self._chars_overlap
            seq = 0
            i = 0
            while i < len(content):
                end = min(i + self._chars_per_chunk, len(content))
                chunk_text = content[i:end]
                chunks.append(
                    EmbeddingChunk(
                        chunk_id=f"{unit.unit_id}#c{seq}",
                        unit_id=unit.unit_id,
                        repo_id=unit.repo_id,
                        seq=seq,
                        content=chunk_text,
                        char_start=i,
                        char_end=end,
                        token_estimate=estimate_tokens(chunk_text),
                    )
                )
                seq += 1
                if end == len(content):
                    break
                i += stride

            span.set_attribute("chunks", len(chunks))
            emit_phase3_event(
                event="chunking",
                operation="chunk",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                unit_id=unit.unit_id,
                level="debug",
                chunks=len(chunks),
            )
            return chunks

    def chunk_units(
        self, units: Sequence[IngestionUnit]
    ) -> list[EmbeddingChunk]:
        out: list[EmbeddingChunk] = []
        for u in sorted(units, key=lambda u: u.unit_id):
            out.extend(self.chunk_unit(u))
        return out
