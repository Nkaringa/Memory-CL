"""In-process Redis replacement for lite mode.

Only the lifecycle analytics (usage/feedback/decay) and the `update_memory`
MCP tool touch Redis — not the core ingest/search path. Lite swaps the real
Redis for this dict-backed async stub so those features keep working with no
server. Single-process, process-lifetime state (TTLs are accepted but not
enforced — fine for a single-user laptop). `decode_responses=True` semantics:
everything stored + returned as `str`.
"""

from __future__ import annotations

import time

from storage.base import StorageHealth


class InMemoryRedis:
    """The narrow async subset of redis-py that lite callers use."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: object, ex: int | None = None) -> bool:
        self._kv[key] = str(value)
        return True

    async def setex(self, key: str, seconds: int, value: object) -> bool:
        self._kv[key] = str(value)
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        cur = int(self._kv.get(key, "0")) + amount
        self._kv[key] = str(cur)
        return cur

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._kv.get(k) for k in keys]

    async def rpush(self, key: str, *values: object) -> int:
        lst = self._lists.setdefault(key, [])
        lst.extend(str(v) for v in values)
        return len(lst)

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self._lists.get(key, [])
        # Redis end is inclusive; -1 means "to the end".
        stop = len(lst) if end == -1 else end + 1
        return lst[start:stop]

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._kv or key in self._lists

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            n += self._kv.pop(k, None) is not None
            n += self._lists.pop(k, None) is not None
        return n

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self._kv or k in self._lists)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class LiteRedisClient:
    """`RedisClient`-shaped wrapper over `InMemoryRedis` (lite mode)."""

    name: str = "redis"  # keep the same name so health output is identical

    def __init__(self) -> None:
        self._client = InMemoryRedis()

    @property
    def client(self) -> InMemoryRedis:
        return self._client

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def ping(self) -> StorageHealth:
        start = time.perf_counter()
        return StorageHealth(
            self.name, ok=True, latency_ms=(time.perf_counter() - start) * 1000
        )


__all__ = ["InMemoryRedis", "LiteRedisClient"]
