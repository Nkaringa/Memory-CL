"""Monotonic per-tenant state-version tokens.

The `version_token` returned here drives Phase-7's RetrievalCache
invalidation and Phase-8's snapshot identity. It is monotonic per
tenant (`v0`, `v1`, …) and persisted in Redis so multiple processes
agree on the current version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _key(tenant_id: str) -> str:
    return f"phase8:state_version:{tenant_id}"


@dataclass(frozen=True, slots=True)
class StateVersion:
    tenant_id: str
    version: str  # "v0", "v1", …
    counter: int


class VersionTokenStore:
    """Async wrapper around a Redis INCR counter.

    The counter never decreases; reading the current version is a
    simple GET, advancing it is INCR. Both are atomic — concurrent
    callers get distinct version numbers.
    """

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    async def current(self, *, tenant_id: str) -> StateVersion:
        raw = await self._client.get(_key(tenant_id))
        counter = int(raw) if raw not in (None, "") else 0
        return StateVersion(
            tenant_id=tenant_id, version=f"v{counter}", counter=counter,
        )

    async def advance(self, *, tenant_id: str) -> StateVersion:
        counter = await self._client.incr(_key(tenant_id))
        return StateVersion(
            tenant_id=tenant_id, version=f"v{int(counter)}", counter=int(counter),
        )

    async def reset(self, *, tenant_id: str) -> None:
        """For tests + clean teardown only — never call in production."""
        await self._client.set(_key(tenant_id), "0")


__all__ = ["StateVersion", "VersionTokenStore"]
