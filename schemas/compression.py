"""Phase-3 dense schemas built ON TOP of the Phase-2 DenseRecord.

Phase-2 schemas (`schemas/dense.py::DenseRecord`) are immutable. The
records here either reuse `DenseRecord` directly (via the t-tag) or
introduce specialized record types whose keys also obey the global
DENSE_NOTATION_SPEC: max key length 5, sorted, deterministic.
"""

from __future__ import annotations

import json
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.dense import DENSE_VERSION


def _canonical_json(payload: dict[str, object]) -> str:
    """Single source of truth for canonical JSON in compression land.

    Defined here so every dense schema gets the exact same byte output
    without each schema rolling its own serializer.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _DenseBase(BaseModel):
    """Shared base for Phase-3 dense schemas.

    Every subclass declares `t` as a Literal so the type tag is part of
    the model's identity (no runtime override possible). Keys are
    pre-sorted at serialization time; arrays are validated to be sorted
    and deduplicated.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    MAX_KEY_LEN: ClassVar[int] = 5

    v: str = Field(default=DENSE_VERSION, description="Schema version")
    id: str = Field(min_length=1, description="Stable identifier (qname/node_id)")

    def to_dense_json(self, *, drop_empty: bool = True) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        if drop_empty:
            payload = {k: v for k, v in payload.items()
                       if not (isinstance(v, list) and not v) and v != ""}
        return _canonical_json(payload)


class DenseModule(_DenseBase):
    """Dense per-module summary.

    `cls`/`fn`/`const` arrays carry leaf names (not qnames) — the
    module's `id` already provides the qname prefix, so repeating it
    inflates token count without adding information.
    """

    t: Literal["mod"] = "mod"
    cls: list[str] = Field(default_factory=list, description="Class leaf names")
    fn: list[str] = Field(default_factory=list, description="Function leaf names")
    const: list[str] = Field(default_factory=list, description="Constant leaf names")
    imp: list[str] = Field(default_factory=list, description="Imported module paths")
    file: list[str] = Field(default_factory=list)

    @field_validator("cls", "fn", "const", "imp", "file")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class DenseApi(_DenseBase):
    """Dense API-surface summary for a module or service."""

    t: Literal["api"] = "api"
    api: list[str] = Field(default_factory=list, description="Public function leaf names")
    cls: list[str] = Field(default_factory=list, description="Public class leaf names")
    file: list[str] = Field(default_factory=list)

    @field_validator("api", "cls", "file")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class DenseGraphSlice(_DenseBase):
    """1-hop graph snapshot for a node — supports retrieval graph traversal.

    `i` and `o` are intentionally single-char (NOT 5-char) for token
    economy. The spec caps key length, not floors it.
    """

    t: Literal["gph"] = "gph"
    k: str = Field(description="NodeKind value")
    i: list[str] = Field(default_factory=list, description="Incoming neighbor node_ids")
    o: list[str] = Field(default_factory=list, description="Outgoing neighbor node_ids")
    deg: int = Field(default=0, ge=0)

    @field_validator("i", "o")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class EmbeddingChunk(BaseModel):
    """One embedding chunk (text + position + optional vector).

    Chunks are NOT in dense notation — they are intermediate artifacts
    produced for the embedder. Their byte size is irrelevant; their
    content is what gets embedded.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    seq: int = Field(ge=0, description="0-indexed position within the unit")
    content: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    token_estimate: int = Field(ge=0)
    vector: tuple[float, ...] | None = None

    @field_validator("char_end")
    @classmethod
    def _end_after_start(cls, v: int, info: object) -> int:
        data = getattr(info, "data", {}) or {}
        start = data.get("char_start")
        if start is not None and v < start:
            raise ValueError("char_end must be >= char_start")
        return v


class CompressionMetrics(BaseModel):
    """Per-run counters returned to API callers and emitted to PHASE_LOG."""

    model_config = ConfigDict(extra="forbid")

    units_encoded: int = 0
    modules_summarized: int = 0
    apis_summarized: int = 0
    graph_slices: int = 0
    chunks_emitted: int = 0
    embeddings_written: int = 0
    bytes_input: int = 0
    bytes_output: int = 0
    duration_ms: float = 0.0

    def token_reduction_ratio(self) -> float:
        """1 - (out_bytes / in_bytes). Returns 0.0 when no input bytes."""
        if self.bytes_input == 0:
            return 0.0
        return max(0.0, 1.0 - (self.bytes_output / self.bytes_input))

    def as_dict(self) -> dict[str, float | int]:
        d = self.model_dump(mode="json")
        d["token_reduction_ratio"] = round(self.token_reduction_ratio(), 6)
        d["duration_ms"] = round(self.duration_ms, 3)
        return d


__all__ = [
    "CompressionMetrics",
    "DenseApi",
    "DenseGraphSlice",
    "DenseModule",
    "EmbeddingChunk",
]
