"""Federated identity store — OIDC/OAuth2 provider ↔ local user linkage.

Each row represents one external identity (Google, GitHub, …) linked to a
local user account.  The UNIQUE constraint on (provider, subject) enforces
that a given provider account can only be linked to one local user at a time.

`provider` matches an `auth_providers.id` value.
`subject`  is the provider's stable user identifier (OIDC `sub` claim).

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST.  The INSERT here is plain (no CTE), so no cast is needed for
the default `now()` column.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.federated_identity_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS federated_identities (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    subject    TEXT NOT NULL,
    email      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, subject)
)
"""

_SELECT_COLS = "id, user_id, provider, subject, email, created_at"

_INSERT = text(
    "INSERT INTO federated_identities (id, user_id, provider, subject, email)"
    " VALUES (:id, :user_id, :provider, :subject, :email)"
)

_SELECT_BY_SUBJECT = text(
    f"SELECT {_SELECT_COLS} FROM federated_identities"
    " WHERE provider = :provider AND subject = :subject"
)

_SELECT_FOR_USER = text(
    f"SELECT {_SELECT_COLS} FROM federated_identities"
    " WHERE user_id = :user_id ORDER BY created_at"
)


@dataclass(frozen=True, slots=True)
class FederatedIdentityRow:
    id: str
    user_id: str
    provider: str
    subject: str
    email: str
    created_at: datetime | None = None


def _row(r: object) -> FederatedIdentityRow:
    m = r._mapping if hasattr(r, "_mapping") else r  # type: ignore[union-attr]
    return FederatedIdentityRow(
        id=m["id"],
        user_id=m["user_id"],
        provider=m["provider"],
        subject=m["subject"],
        email=m["email"],
        created_at=m["created_at"],
    )


class PostgresFederatedIdentityRepository:
    """Concrete store for `federated_identities` over the injected AsyncEngine."""

    name: str = "federated_identity_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("federated_identity_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def add(
        self,
        *,
        id: str,
        user_id: str,
        provider: str,
        subject: str,
        email: str,
    ) -> FederatedIdentityRow:
        with _tracer.start_as_current_span("federated_identity_repo.add"):
            async with self._engine.begin() as conn:
                await conn.execute(_INSERT, {
                    "id": id,
                    "user_id": user_id,
                    "provider": provider,
                    "subject": subject,
                    "email": email,
                })
        got = await self.get_by_subject(provider=provider, subject=subject)
        if got is None:
            raise RuntimeError(f"federated_identity ({provider!r}, {subject!r}) vanished after insert")
        return got

    async def get_by_subject(self, *, provider: str, subject: str) -> FederatedIdentityRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(_SELECT_BY_SUBJECT, {"provider": provider, "subject": subject})
            ).first()
        return _row(row) if row else None

    async def list_for_user(self, user_id: str) -> list[FederatedIdentityRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(_SELECT_FOR_USER, {"user_id": user_id})
            ).fetchall()
        return [_row(r) for r in rows]


__all__ = ["FederatedIdentityRow", "PostgresFederatedIdentityRepository"]
