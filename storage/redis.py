from __future__ import annotations

import time

from redis.asyncio import Redis, from_url

from storage.base import StorageHealth


class RedisClient:
    """Async Redis client wrapper for cache + session memory."""

    name: str = "redis"

    def __init__(self, url: str, *, decode_responses: bool = True) -> None:
        self._url = url
        self._decode = decode_responses
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("RedisClient not connected — call connect() first")
        return self._client

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = from_url(self._url, decode_responses=self._decode)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> StorageHealth:
        if self._client is None:
            return StorageHealth(self.name, ok=False, latency_ms=0.0, error="not connected")
        start = time.perf_counter()
        try:
            pong = await self._client.ping()
            ok = bool(pong)
            return StorageHealth(
                self.name,
                ok=ok,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=None if ok else "PING returned falsy",
            )
        except Exception as exc:
            return StorageHealth(
                self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )
