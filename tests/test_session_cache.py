import pytest
from core.auth.session_cache import SessionCache

class _FakeRepo:
    def __init__(self, ids): self._ids = set(ids)
    async def list_active_session_ids(self): return set(self._ids)

async def test_refresh_then_valid():
    cache = SessionCache(_FakeRepo({"a", "b"}))
    await cache.refresh()
    assert cache.is_valid("a") and not cache.is_valid("z")
    assert cache.active_count() == 2

async def test_add_and_invalidate_sync():
    cache = SessionCache(_FakeRepo(set()))
    cache.add("x")
    assert cache.is_valid("x")
    cache.invalidate("x")
    assert not cache.is_valid("x")
