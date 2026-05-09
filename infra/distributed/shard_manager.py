"""Cluster-wide shard topology + lookups.

`ShardTopology` records the logical shard list (`shard-0`, …) and
which physical replica owns each shard. `ShardManager` provides a
single read entry point for the rest of the system — every other
Phase-7 module that needs to know "where does repo X live?" calls
through here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ShardTopology:
    """Static snapshot of the cluster.

    `shard_to_replica` is `shard_id -> replica_id`; `replicas` lists
    every physical replica known to the cluster. Both are sorted to
    keep iteration deterministic.
    """

    shard_count: int
    replicas: tuple[str, ...]
    shard_to_replica: dict[str, str] = field(default_factory=dict)

    def shard_ids(self) -> tuple[str, ...]:
        return tuple(f"shard-{i}" for i in range(self.shard_count))

    @classmethod
    def round_robin(
        cls, *, shard_count: int, replicas: tuple[str, ...],
    ) -> ShardTopology:
        if shard_count <= 0:
            raise ValueError("shard_count must be > 0")
        if not replicas:
            raise ValueError("replicas must be non-empty")
        replicas_sorted = tuple(sorted(replicas))
        mapping: dict[str, str] = {}
        for i in range(shard_count):
            mapping[f"shard-{i}"] = replicas_sorted[i % len(replicas_sorted)]
        return cls(
            shard_count=shard_count,
            replicas=replicas_sorted,
            shard_to_replica=mapping,
        )


class ShardManager:
    """Single entry point for shard placement and lookup."""

    def __init__(self, *, topology: ShardTopology) -> None:
        self._topology = topology

    @property
    def topology(self) -> ShardTopology:
        return self._topology

    def shard_for(self, repo_id: str) -> str:
        """Deterministic SHA-256 % shard_count → shard_id."""
        digest = hashlib.sha256(repo_id.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:8], "big") % self._topology.shard_count
        return f"shard-{idx}"

    def replica_for(self, repo_id: str) -> str:
        shard = self.shard_for(repo_id)
        replica = self._topology.shard_to_replica.get(shard)
        if replica is None:
            raise KeyError(f"no replica mapped for {shard}")
        return replica

    def shards_for_replica(self, replica_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                shard for shard, r in self._topology.shard_to_replica.items()
                if r == replica_id
            )
        )


__all__ = ["ShardManager", "ShardTopology"]
