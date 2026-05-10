"""Deterministic vector-shard router.

Mirrors the graph router's hashing so a unit's vector and its graph
node always co-locate on the same shard index — that's the property
the retrieval layer relies on for cross-store joins to stay local.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VectorShardAssignment:
    repo_id: str
    shard_id: str
    shard_index: int
    collection_name: str


class VectorShardRouter:
    """Maps a `repo_id` to its Qdrant collection on a specific shard."""

    def __init__(self, *, shard_count: int) -> None:
        if shard_count <= 0:
            raise ValueError("shard_count must be > 0")
        self._shard_count = shard_count

    @property
    def shard_count(self) -> int:
        return self._shard_count

    def route(self, *, repo_id: str) -> VectorShardAssignment:
        digest = hashlib.sha256(repo_id.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:8], "big") % self._shard_count
        return VectorShardAssignment(
            repo_id=repo_id,
            shard_id=f"vector-{idx}",
            shard_index=idx,
            # Same name pattern Phase-2 already uses, with the shard
            # index suffix so the operator can pin collections to a
            # specific Qdrant cluster. Underscore separator (not ":")
            # because Qdrant ≥1.11 rejects ":" in collection names.
            collection_name=f"repo_{repo_id}_s{idx}",
        )


__all__ = ["VectorShardAssignment", "VectorShardRouter"]
