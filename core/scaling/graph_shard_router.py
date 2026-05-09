"""Deterministic graph-shard router.

A node's shard is a pure function of `(repo_id, shard_count)`. Per
the Phase-7 sharding rules:
    * shard by repo_id — every node within a repo lives on the same
      shard, which means edges within a repo never cross shards
    * preserve unit_id determinism — `node_id == unit_id` (Phase-2
      invariant) is unaffected
    * no cross-shard mutation without coordination — guaranteed by
      the per-repo placement
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def _shard_for(repo_id: str, shard_count: int) -> int:
    """SHA-256 % shard_count. Stable, deterministic, language-agnostic."""
    if shard_count <= 0:
        raise ValueError("shard_count must be > 0")
    digest = hashlib.sha256(repo_id.encode("utf-8")).digest()
    # Use the first 8 bytes as an unsigned int for the modulo.
    bucket = int.from_bytes(digest[:8], "big") % shard_count
    return bucket


@dataclass(frozen=True, slots=True)
class ShardAssignment:
    repo_id: str
    shard_id: str
    shard_index: int


class GraphShardRouter:
    """Routes per-repo graph operations to a specific shard label."""

    def __init__(self, *, shard_count: int) -> None:
        if shard_count <= 0:
            raise ValueError("shard_count must be > 0")
        self._shard_count = shard_count

    @property
    def shard_count(self) -> int:
        return self._shard_count

    def route(self, *, repo_id: str) -> ShardAssignment:
        idx = _shard_for(repo_id, self._shard_count)
        return ShardAssignment(
            repo_id=repo_id, shard_id=f"graph-{idx}", shard_index=idx,
        )

    def route_node(self, *, repo_id: str, node_id: str) -> ShardAssignment:
        """Repo-keyed shard. node_id is honored for the API symmetry but
        does not influence placement — preserves the spec invariant
        that all nodes in a repo land together."""
        _ = node_id
        return self.route(repo_id=repo_id)


__all__ = ["GraphShardRouter", "ShardAssignment"]
