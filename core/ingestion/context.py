from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from storage.repositories import (
    GraphRepository,
    IngestionUnitRepository,
    VectorRepository,
)


@dataclass(slots=True)
class IngestionMetrics:
    """Per-run counters; appended verbatim into PHASE_LOG.md."""

    files_walked: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    units_emitted: int = 0
    units_changed: int = 0
    nodes_written: int = 0
    edges_written: int = 0
    vector_payloads_written: int = 0
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "files_walked": self.files_walked,
            "files_parsed": self.files_parsed,
            "files_failed": self.files_failed,
            "units_emitted": self.units_emitted,
            "units_changed": self.units_changed,
            "nodes_written": self.nodes_written,
            "edges_written": self.edges_written,
            "vector_payloads_written": self.vector_payloads_written,
            "duration_ms": round(self.duration_ms, 3),
        }


@dataclass(slots=True)
class IngestionContext:
    """Runtime context threaded through the pipeline.

    The context is immutable per-run except for `metrics` (which the
    pipeline updates as it advances). Holding repos here is what lets
    every layer stay free of any imports from `apps/`.
    """

    repo_id: str
    repo_path: Path
    commit_sha: str
    units_collection: str  # Qdrant collection name for this repo
    units_repo: IngestionUnitRepository
    graph_repo: GraphRepository
    vector_repo: VectorRepository
    metrics: IngestionMetrics = field(default_factory=IngestionMetrics)
