"""Request-level load balancer.

Two strategies:
    * `hash`        — deterministic by `key` (e.g. repo_id). Identical
                      requests always land on the same replica.
    * `round_robin` — strict round-robin across replicas. Useful for
                      stateless probes / health checks.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from enum import StrEnum


class RoutingStrategy(StrEnum):
    HASH = "hash"
    ROUND_ROBIN = "round_robin"


class LoadBalancer:
    """Stateless under HASH; stateful (cycle counter) under ROUND_ROBIN."""

    def __init__(
        self,
        *,
        replicas: Sequence[str],
        strategy: RoutingStrategy = RoutingStrategy.HASH,
    ) -> None:
        if not replicas:
            raise ValueError("replicas must be non-empty")
        # Sort to keep `route(...)` deterministic across processes.
        self._replicas = tuple(sorted(replicas))
        self._strategy = strategy
        self._rr_iter = itertools.cycle(self._replicas)

    @property
    def replicas(self) -> tuple[str, ...]:
        return self._replicas

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    def route(self, *, key: str = "") -> str:
        if self._strategy == RoutingStrategy.HASH:
            digest = hashlib.sha256(key.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:8], "big") % len(self._replicas)
            return self._replicas[idx]
        return next(self._rr_iter)

    def reset_round_robin(self) -> None:
        self._rr_iter = itertools.cycle(self._replicas)


__all__ = ["LoadBalancer", "RoutingStrategy"]
