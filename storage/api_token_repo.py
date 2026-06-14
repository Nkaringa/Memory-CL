"""Named, revocable API tokens.

The single static MCP key still works (backward-compatible), but operators
can now issue MULTIPLE named tokens — one per agent/machine — and revoke any
one individually without rotating a shared secret. Only a SHA-256 **hash**
of each token is stored; the raw value is shown once at issue time and is
unrecoverable afterward. A high-entropy `secrets.token_urlsafe` token needs
no salt (it isn't a guessable password), so a plain content hash is the
right lookup key.

SQL lives only here; `ensure_schema()` is idempotent and runs in the same
bootstrap pass as the other tables. B14/B15: TIMESTAMPTZ binds are CAST.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.api_token_repo")

# token_urlsafe(32) -> ~43 url-safe chars of entropy.
_TOKEN_ENTROPY_BYTES = 32
# Short non-secret handle used to revoke a token (safe to show / put in URLs).
_ID_ENTROPY_BYTES = 8


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS api_tokens (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        token_hash   TEXT NOT NULL UNIQUE,
        token_hint   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_used_at TIMESTAMPTZ,
        revoked_at   TIMESTAMPTZ
    )
    """,
)


def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token — the stored + looked-up form."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _hint(raw: str) -> str:
    return "••••" + raw[-4:] if len(raw) > 4 else "••••"


@dataclass(frozen=True, slots=True)
class ApiTokenRow:
    """A token as exposed to the API/UI — NEVER carries the hash or raw value."""

    id: str
    name: str
    token_hint: str
    created_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


_INSERT = text("""
INSERT INTO api_tokens (id, name, token_hash, token_hint, created_at)
VALUES (:id, :name, :token_hash, :token_hint, CAST(:ts AS TIMESTAMPTZ))
""")

_SELECT_ALL = text("""
SELECT id, name, token_hint, created_at, last_used_at, revoked_at
FROM api_tokens ORDER BY created_at DESC
""")

_SELECT_ONE = text("""
SELECT id, name, token_hint, created_at, last_used_at, revoked_at
FROM api_tokens WHERE id = :id
""")

_SELECT_ACTIVE_HASHES = text(
    "SELECT token_hash FROM api_tokens WHERE revoked_at IS NULL"
)

_REVOKE = text("""
UPDATE api_tokens SET revoked_at = CAST(:ts AS TIMESTAMPTZ)
WHERE id = :id AND revoked_at IS NULL
""")

_TOUCH = text("""
UPDATE api_tokens SET last_used_at = CAST(:ts AS TIMESTAMPTZ)
WHERE token_hash = :token_hash
""")


def _row(r: Any) -> ApiTokenRow:
    m = r._mapping if hasattr(r, "_mapping") else r
    return ApiTokenRow(
        id=m["id"], name=m["name"], token_hint=m["token_hint"],
        created_at=m["created_at"], last_used_at=m["last_used_at"],
        revoked_at=m["revoked_at"],
    )


class ApiTokenRepository:
    """Concrete store for `api_tokens` over the injected AsyncEngine."""

    name: str = "api_token_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("api_token_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))

    async def issue(self, name: str) -> tuple[str, ApiTokenRow]:
        """Mint a new token. Returns (raw_token, row). The raw token is the
        ONLY time the secret is available — the caller shows it once."""
        raw = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
        token_id = secrets.token_urlsafe(_ID_ENTROPY_BYTES)
        clean_name = name.strip() or "unnamed"
        hint = _hint(raw)
        now = datetime.now(UTC)
        with _tracer.start_as_current_span("api_token_repo.issue"):
            async with self._engine.begin() as conn:
                await conn.execute(_INSERT, {
                    "id": token_id, "name": clean_name,
                    "token_hash": hash_token(raw), "token_hint": hint, "ts": now,
                })
        return raw, ApiTokenRow(
            id=token_id, name=clean_name, token_hint=hint,
            created_at=now, last_used_at=None, revoked_at=None,
        )

    async def list_all(self) -> list[ApiTokenRow]:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_ALL)
            return [_row(r) for r in result.fetchall()]

    async def get(self, token_id: str) -> ApiTokenRow | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_ONE, {"id": token_id})
            row = result.first()
            return _row(row) if row else None

    async def list_active_hashes(self) -> set[str]:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_ACTIVE_HASHES)
            return {r[0] for r in result.fetchall()}

    async def revoke(self, token_id: str) -> bool:
        """Revoke a token. Returns True if it was active and is now revoked."""
        with _tracer.start_as_current_span("api_token_repo.revoke"):
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    _REVOKE, {"id": token_id, "ts": datetime.now(UTC)}
                )
                return (result.rowcount or 0) > 0

    async def touch_last_used(self, token_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                _TOUCH, {"token_hash": token_hash, "ts": datetime.now(UTC)}
            )


__all__: Sequence[str] = ["ApiTokenRepository", "ApiTokenRow", "hash_token"]
