"""Deterministic token-bucket rate limiter.

Tokens accrue at `rate` per second up to a bucket size. Time is
threaded in by the caller (`now`) so two runs with identical request
sequences produce identical decisions — required by Phase-7's
determinism rule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class _Bucket:
    tokens: float
    last_refill: datetime


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    tokens_remaining: float
    retry_after_ms: float


class RateLimiter:
    """Token-bucket per (caller, resource)."""

    def __init__(self, *, rate_per_second: float, burst: int | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self._rate = float(rate_per_second)
        # Default burst = 1 second's worth of tokens.
        self._burst = float(burst if burst is not None else max(1.0, rate_per_second))
        self._buckets: dict[tuple[str, str], _Bucket] = defaultdict(
            lambda: _Bucket(tokens=self._burst, last_refill=datetime.min)
        )

    def acquire(
        self, *, caller: str, resource: str, now: datetime, cost: float = 1.0,
    ) -> RateDecision:
        if cost < 0:
            raise ValueError("cost must be >= 0")
        bucket = self._buckets[(caller, resource)]
        # On first contact, set the refill anchor so we don't credit
        # huge accruals from the datetime.min sentinel.
        if bucket.last_refill == datetime.min:
            bucket.last_refill = now
        else:
            elapsed = (now - bucket.last_refill).total_seconds()
            if elapsed > 0:
                bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
                bucket.last_refill = now

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return RateDecision(
                allowed=True, tokens_remaining=bucket.tokens, retry_after_ms=0.0,
            )
        # Compute wait time until we have enough tokens.
        deficit = cost - bucket.tokens
        retry_after_ms = (deficit / self._rate) * 1000
        return RateDecision(
            allowed=False, tokens_remaining=bucket.tokens,
            retry_after_ms=retry_after_ms,
        )


__all__ = ["RateDecision", "RateLimiter"]
