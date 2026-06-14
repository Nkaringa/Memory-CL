"""In-memory cache of active session IDs for O(1) session validity checks.

Mirrors the shape of TokenCache: the repo supplies the authoritative set on
refresh(); add() and invalidate() keep the local view consistent after writes
without requiring a round-trip back to the database.
"""

from __future__ import annotations
from typing import Protocol


class _Repo(Protocol):
    async def list_active_session_ids(self) -> set[str]: ...


class SessionCache:
    """Cached set of active (non-expired, non-revoked) session IDs."""

    def __init__(self, repo: _Repo) -> None:
        self.repo = repo
        self._active: set[str] = set()

    async def refresh(self) -> None:
        """Reload the active-session set from storage. Call after every write."""
        self._active = await self.repo.list_active_session_ids()

    def is_valid(self, session_id: str) -> bool:
        """True if session_id is present in the active set."""
        return session_id in self._active

    def add(self, session_id: str) -> None:
        """Optimistically add a newly-created session without a refresh round-trip."""
        self._active.add(session_id)

    def invalidate(self, session_id: str) -> None:
        """Remove a session on logout or revocation without a refresh round-trip."""
        self._active.discard(session_id)

    def active_count(self) -> int:
        return len(self._active)


__all__ = ["SessionCache"]
