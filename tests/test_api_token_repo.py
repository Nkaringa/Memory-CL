"""api_tokens SQL-shape + TokenCache logic (wire round-trip = golden test)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.token_cache import TokenCache
from storage.api_token_repo import (
    _DDL_STATEMENTS,
    _INSERT,
    _REVOKE,
    _TOUCH,
    ApiTokenRepository,
    hash_token,
)


def test_ddl_is_idempotent_and_defines_columns() -> None:
    ddl = " ".join(_DDL_STATEMENTS[0].split())
    assert "IF NOT EXISTS" in ddl
    assert "id TEXT PRIMARY KEY" in ddl
    for col in ("name", "token_hash", "token_hint", "created_at", "last_used_at", "revoked_at"):
        assert col in ddl
    assert "token_hash TEXT NOT NULL UNIQUE" in ddl


def test_timestamp_binds_are_cast() -> None:
    # B14/B15: TIMESTAMPTZ binds must be CAST.
    assert "CAST(:ts AS TIMESTAMPTZ)" in str(_INSERT)
    assert "CAST(:ts AS TIMESTAMPTZ)" in str(_REVOKE)
    assert "CAST(:ts AS TIMESTAMPTZ)" in str(_TOUCH)
    # Revoke only flips an ACTIVE token.
    assert "revoked_at IS NULL" in str(_REVOKE)


def test_hash_token_is_sha256_hex() -> None:
    import hashlib
    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(hash_token("x")) == 64


def test_repo_constructs() -> None:
    assert ApiTokenRepository(engine=AsyncMock()).name == "api_token_repo"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TokenCache
# ---------------------------------------------------------------------------
class _FakeTokenRepo:
    def __init__(self, hashes: set[str]) -> None:
        self._hashes = hashes

    async def list_active_hashes(self) -> set[str]:
        return set(self._hashes)


@pytest.mark.asyncio
async def test_token_cache_validates_active_hash() -> None:
    raw = "secret-token-value"
    cache = TokenCache(_FakeTokenRepo({hash_token(raw)}))  # type: ignore[arg-type]
    assert cache.is_valid(raw) is False  # not loaded yet
    await cache.refresh()
    assert cache.is_valid(raw) is True
    assert cache.is_valid("wrong") is False
    assert cache.is_valid("") is False
    assert cache.active_count() == 1


@pytest.mark.asyncio
async def test_token_cache_drops_revoked_on_refresh() -> None:
    raw = "tok"
    repo = _FakeTokenRepo({hash_token(raw)})
    cache = TokenCache(repo)  # type: ignore[arg-type]
    await cache.refresh()
    assert cache.is_valid(raw) is True
    # Simulate a revoke: the repo no longer returns the hash.
    repo._hashes = set()
    await cache.refresh()
    assert cache.is_valid(raw) is False
    assert cache.active_count() == 0
