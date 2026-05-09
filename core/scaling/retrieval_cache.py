"""LRU+TTL retrieval cache with version-aware invalidation.

Cache keys are derived from `(repo_id, query_text, top_k,
unit_kinds, seed_unit_ids, version_token)`. The `version_token` is a
caller-supplied string the Phase-6 lifecycle can flip whenever an
entity gets downgraded / promoted — flipping it implicitly evicts
every stale entry that depended on the old version.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    value: Any
    inserted_at: float
    expires_at: float
    version_token: str


def cache_key_for_query(
    *,
    repo_id: str,
    query_text: str,
    top_k: int,
    unit_kinds: Iterable[str] = (),
    seed_unit_ids: Iterable[str] = (),
    version_token: str = "v0",
) -> str:
    """Deterministic cache key.

    Sorted + joined components ensure two semantically-equal queries
    that differ only by list order produce the same key — required
    for hit-rate to mean anything.
    """
    parts = [
        f"repo={repo_id}",
        f"q={query_text}",
        f"k={top_k}",
        f"kinds={','.join(sorted(set(unit_kinds)))}",
        f"seeds={','.join(sorted(set(seed_unit_ids)))}",
        f"v={version_token}",
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RetrievalCache:
    """LRU + TTL cache; version-token is the Phase-6 invalidation lever.

    Determinism: get/put are ordered, eviction is by oldest-key, no
    randomness anywhere. Two cache instances fed identical event
    streams produce identical state.
    """

    def __init__(self, *, max_size: int, ttl_seconds: float) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        return len(self._entries)

    def get(
        self, key: str, *, version_token: str, now: float | None = None,
    ) -> Any | None:
        when = now if now is not None else time.monotonic()
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if entry.expires_at <= when or entry.version_token != version_token:
            # Stale or invalidated — evict and report miss.
            self._entries.pop(key, None)
            self._misses += 1
            return None
        # Touch — moves the key to the most-recently-used end.
        self._entries.move_to_end(key)
        self._hits += 1
        return entry.value

    def put(
        self,
        key: str,
        value: Any,
        *,
        version_token: str,
        now: float | None = None,
    ) -> None:
        when = now if now is not None else time.monotonic()
        self._entries[key] = CacheEntry(
            key=key,
            value=value,
            inserted_at=when,
            expires_at=when + self._ttl,
            version_token=version_token,
        )
        self._entries.move_to_end(key)
        # Evict oldest until we're back within bounds.
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

    def invalidate_version(self, version_token: str) -> int:
        """Remove every entry that was inserted under `version_token`."""
        victims = [
            k for k, e in self._entries.items() if e.version_token == version_token
        ]
        for k in victims:
            self._entries.pop(k, None)
        return len(victims)

    def clear(self) -> None:
        self._entries.clear()
        self._hits = 0
        self._misses = 0


__all__ = ["CacheEntry", "RetrievalCache", "cache_key_for_query"]
