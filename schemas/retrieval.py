"""Phase-4 retrieval schemas — query in, packet out.

These schemas are the public surface of the retrieval system: any agent
or MCP tool that talks to Memory-CL serializes one of these. They are
NOT dense-notation records — readability matters at the API boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.base import SCHEMA_VERSION


class RetrievalChannel(StrEnum):
    GRAPH = "graph"
    VECTOR = "vector"
    METADATA = "metadata"


class Query(BaseModel):
    """Inbound retrieval query."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8192)
    repo_id: str = Field(min_length=1, max_length=128)
    top_k: int = Field(default=10, gt=0, le=200)
    unit_kinds: list[str] = Field(
        default_factory=list,
        description="Optional filter: only return units of these kinds",
    )
    seed_unit_ids: list[str] = Field(
        default_factory=list,
        description="Optional graph-traversal seed nodes",
    )

    @field_validator("unit_kinds", "seed_unit_ids")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class RankingFeatures(BaseModel):
    """The five inputs to the ranking formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    graph_proximity: float = Field(default=0.0, ge=0.0, le=1.0)
    recency: float = Field(default=0.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    user_feedback: float = Field(default=0.0, ge=0.0, le=1.0)


class RetrievalCandidate(BaseModel):
    """A candidate produced by ONE retrieval channel before fusion.

    `raw_score` is the channel-native score (cosine for vector,
    1/(1+depth) for graph, etc.). Fusion + ranking convert it into a
    `RankingFeatures` value.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    channel: RetrievalChannel
    raw_score: float = Field(ge=0.0, le=1.0)
    file_path: str | None = None
    qualified_name: str | None = None
    kind: str | None = None
    extra: dict[str, str | int | float] = Field(default_factory=dict)


class RankedResult(BaseModel):
    """Final ranked result with the full feature breakdown.

    Keeping `breakdown` exposed makes the ranking formula auditable —
    the test suite asserts the components multiply out to `final_score`.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    final_score: float = Field(ge=0.0, le=1.0)
    breakdown: RankingFeatures
    channels: list[RetrievalChannel] = Field(default_factory=list)
    file_path: str | None = None
    qualified_name: str | None = None
    kind: str | None = None

    @field_validator("channels")
    @classmethod
    def _sorted_unique(cls, v: list[RetrievalChannel]) -> list[RetrievalChannel]:
        return sorted(set(v), key=lambda c: c.value)


# Priority bands for the context assembler. The order is mandated by
# RETRIEVAL_SYSTEM_SPEC.md and CONTEXT_ASSEMBLY_RULES of the Phase-4 spec.
ContextEntryType = Literal[
    "constraint", "risk", "architecture", "logic", "code"
]


class ContextEntry(BaseModel):
    """One entry inside a ContextPacket."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: ContextEntryType
    score: float = Field(ge=0.0, le=1.0)
    data: dict[str, object] = Field(default_factory=dict)


class ContextPacket(BaseModel):
    """Output format mandated by RETRIEVAL_SYSTEM_SPEC.md."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    task: str = ""
    context: list[ContextEntry] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


__all__ = [
    "ContextEntry",
    "ContextEntryType",
    "ContextPacket",
    "Query",
    "RankedResult",
    "RankingFeatures",
    "RetrievalCandidate",
    "RetrievalChannel",
]
