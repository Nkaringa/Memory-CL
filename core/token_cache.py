"""In-memory cache of active API-token hashes for synchronous auth.

Auth runs in places that can't cheaply await — the sync FastAPI dependency
(`apps.mcp.auth`) and the native-transport ASGI middleware
(`apps.mcp.native_auth`). Per-request DB lookups there would be a tax on
every MCP call. Instead we keep the set of active token hashes in memory,
load it once at startup, and reload it after every issue/revoke (the
endpoints call `refresh()`). Lookup is then an O(1) hashed-set membership
check — the same trick `RuntimeConfig` uses for the keys.
"""

from __future__ import annotations

import threading

from storage.api_token_repo import ApiTokenRepository, hash_token


class TokenCache:
    """Cached set of active (non-revoked) token hashes."""

    def __init__(self, repo: ApiTokenRepository) -> None:
        self._repo = repo
        self._hashes: frozenset[str] = frozenset()
        self._lock = threading.Lock()

    async def refresh(self) -> None:
        """Reload the active-hash set from Postgres. Call after every write."""
        hashes = await self._repo.list_active_hashes()
        with self._lock:
            self._hashes = frozenset(hashes)

    def is_valid(self, raw_key: str) -> bool:
        """True if `raw_key` hashes to an active, non-revoked token."""
        if not raw_key:
            return False
        digest = hash_token(raw_key)
        with self._lock:
            return digest in self._hashes

    def active_count(self) -> int:
        with self._lock:
            return len(self._hashes)

    @property
    def repo(self) -> ApiTokenRepository:
        return self._repo


__all__ = ["TokenCache"]
