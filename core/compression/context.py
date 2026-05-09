from __future__ import annotations

from dataclasses import dataclass, field

from schemas import CompressionMetrics
from storage.repositories import VectorRepository


@dataclass(slots=True)
class CompressionContext:
    """Runtime context threaded through the Phase-3 pipeline.

    Holding the vector repo + collection name on the context lets the
    embedding step write back to Qdrant without any layer above `core/`
    needing to know which concrete client is in use.
    """

    repo_id: str
    commit_sha: str
    units_collection: str
    vector_repo: VectorRepository
    metrics: CompressionMetrics = field(default_factory=CompressionMetrics)
