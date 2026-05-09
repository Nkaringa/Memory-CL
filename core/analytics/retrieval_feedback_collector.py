"""Retrieval-outcome feedback collection.

Each MCP retrieval call (or any other consumer of the retrieval
engine) reports per-entity outcomes via `record_outcome`. Phase 6
does NOT modify Phase 5 / Phase 4 — wiring this collector into MCP
tool calls is left to a downstream phase. The collector is fully
functional standalone and is exercised by tests.

Storage layout (Redis):
    `phase6:fb:attempts:<repo_id>:<entity_id>`   INCR counter
    `phase6:fb:successes:<repo_id>:<entity_id>`  INCR counter
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _attempts_key(repo_id: str, entity_id: str) -> str:
    return f"phase6:fb:attempts:{repo_id}:{entity_id}"


def _successes_key(repo_id: str, entity_id: str) -> str:
    return f"phase6:fb:successes:{repo_id}:{entity_id}"


@dataclass(frozen=True, slots=True)
class FeedbackOutcome:
    entity_id: str
    attempts: int
    successes: int

    @property
    def success_rate(self) -> float:
        return 0.0 if self.attempts <= 0 else self.successes / self.attempts


class RetrievalFeedbackCollector:
    """Append-only counters for retrieval outcomes per entity.

    `record_outcome(success=True)` bumps both `attempts` and
    `successes`; `success=False` bumps only `attempts`. We never
    decrement, ensuring the historical signal is always preserved.
    """

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    async def record_outcome(
        self, *, repo_id: str, entity_id: str, success: bool
    ) -> FeedbackOutcome:
        attempts = await self._client.incr(_attempts_key(repo_id, entity_id))
        successes_key = _successes_key(repo_id, entity_id)
        if success:
            successes = await self._client.incr(successes_key)
        else:
            raw = await self._client.get(successes_key)
            successes = int(raw) if raw not in (None, "") else 0
        return FeedbackOutcome(
            entity_id=entity_id,
            attempts=int(attempts),
            successes=int(successes),
        )

    async def get_outcome(self, repo_id: str, entity_id: str) -> FeedbackOutcome:
        a = await self._client.get(_attempts_key(repo_id, entity_id))
        s = await self._client.get(_successes_key(repo_id, entity_id))
        return FeedbackOutcome(
            entity_id=entity_id,
            attempts=int(a) if a not in (None, "") else 0,
            successes=int(s) if s not in (None, "") else 0,
        )

    async def bulk_get(
        self, repo_id: str, entity_ids: list[str]
    ) -> dict[str, FeedbackOutcome]:
        if not entity_ids:
            return {}
        ordered = sorted(set(entity_ids))
        a_keys = [_attempts_key(repo_id, e) for e in ordered]
        s_keys = [_successes_key(repo_id, e) for e in ordered]
        a_vals = await self._client.mget(a_keys)
        s_vals = await self._client.mget(s_keys)
        out: dict[str, FeedbackOutcome] = {}
        for entity_id, a, s in zip(ordered, a_vals, s_vals, strict=True):
            out[entity_id] = FeedbackOutcome(
                entity_id=entity_id,
                attempts=int(a) if a not in (None, "") else 0,
                successes=int(s) if s not in (None, "") else 0,
            )
        return out


__all__ = ["FeedbackOutcome", "RetrievalFeedbackCollector"]
