"""Tests for the in-memory Redis stub used in lite mode."""

from __future__ import annotations

import pytest

from storage.lite.redis_stub import InMemoryRedis, LiteRedisClient

pytestmark = pytest.mark.asyncio


async def test_kv_get_set_incr_mget() -> None:
    r = InMemoryRedis()
    assert await r.get("k") is None
    await r.set("k", "v")
    assert await r.get("k") == "v"
    # incr stores + returns int; get returns the str form (decode_responses).
    assert await r.incr("n") == 1
    assert await r.incr("n", 4) == 5
    assert await r.get("n") == "5"
    assert await r.mget(["k", "n", "missing"]) == ["v", "5", None]


async def test_lists_rpush_llen_lrange() -> None:
    r = InMemoryRedis()
    assert await r.rpush("L", "a", "b") == 2
    assert await r.rpush("L", "c") == 3
    assert await r.llen("L") == 3
    assert await r.lrange("L", 0, -1) == ["a", "b", "c"]
    assert await r.lrange("L", 0, 1) == ["a", "b"]


async def test_delete_exists_expire() -> None:
    r = InMemoryRedis()
    await r.set("k", "v")
    await r.rpush("L", "x")
    assert await r.exists("k", "L", "no") == 2
    assert await r.expire("k", 10) is True
    assert await r.delete("k", "L") == 2
    assert await r.exists("k", "L") == 0


async def test_client_wrapper_health() -> None:
    c = LiteRedisClient()
    await c.connect()
    assert (await c.ping()).ok is True
    assert await c.client.ping() is True
    await c.disconnect()
