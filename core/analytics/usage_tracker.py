"""Per-entity usage tracking, Redis-backed.

Two key families:
    `phase6:usage:<repo_id>:<entity_id>`        INCR-able counter
    `phase6:last_access:<repo_id>:<entity_id>`  ISO-8601 string

The tracker is **append-only** with respect to counter semantics —
INCR only increases; we never DECR. That preserves Phase-6's "never
delete data directly" invariant for usage history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _usage_key(repo_id: str, entity_id: str) -> str:
    return f"phase6:usage:{repo_id}:{entity_id}"


def _last_access_key(repo_id: str, entity_id: str) -> str:
    return f"phase6:last_access:{repo_id}:{entity_id}"


@dataclass(frozen=True, slots=True)
class UsageStats:
    """Read-side projection of per-entity counters."""

    entity_id: str
    usage_count: int
    last_access_at: datetime | None


class UsageTracker:
    """Stateless wrapper around the live Redis client.

    The class is intentionally tiny — it exists so callers don't bake
    Redis key conventions into their own code, and so future migrations
    away from Redis don't ripple through every analytics consumer.
    """

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    async def record_access(
        self,
        *,
        repo_id: str,
        entity_id: str,
        at: datetime | None = None,
    ) -> int:
        """Increment the usage counter and refresh `last_access_at`.

        Returns the new counter value so callers can decide whether to
        promote (e.g. unset a low_priority flag) without a follow-up
        round trip.
        """
        when = at or datetime.now(UTC)
        new_count = await self._client.incr(_usage_key(repo_id, entity_id))
        await self._client.set(
            _last_access_key(repo_id, entity_id),
            when.isoformat(),
        )
        return int(new_count)

    async def get_stats(self, repo_id: str, entity_id: str) -> UsageStats:
        raw_count = await self._client.get(_usage_key(repo_id, entity_id))
        raw_last = await self._client.get(_last_access_key(repo_id, entity_id))
        count = int(raw_count) if raw_count not in (None, "") else 0
        last = _parse_iso(raw_last) if raw_last else None
        return UsageStats(entity_id=entity_id, usage_count=count, last_access_at=last)

    async def bulk_get_stats(
        self, repo_id: str, entity_ids: list[str]
    ) -> dict[str, UsageStats]:
        """Fetch many at once. Single round-trip via mget when possible."""
        if not entity_ids:
            return {}
        ordered = sorted(set(entity_ids))
        usage_keys = [_usage_key(repo_id, e) for e in ordered]
        last_keys = [_last_access_key(repo_id, e) for e in ordered]
        usage_vals = await self._client.mget(usage_keys)
        last_vals = await self._client.mget(last_keys)
        out: dict[str, UsageStats] = {}
        for entity_id, u, la in zip(ordered, usage_vals, last_vals, strict=True):
            count = int(u) if u not in (None, "") else 0
            last = _parse_iso(la) if la else None
            out[entity_id] = UsageStats(
                entity_id=entity_id, usage_count=count, last_access_at=last,
            )
        return out


def _parse_iso(s: Any) -> datetime | None:
    """Tolerant ISO-8601 parser; returns None on bad input rather than raising."""
    if s is None:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


__all__ = ["UsageStats", "UsageTracker"]
