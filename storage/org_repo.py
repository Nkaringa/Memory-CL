"""Durable organization store — the tenant boundary row.

One `organizations` row exists per tenant. The default single-tenant
deployment seeds exactly one row (DEFAULT_ORG_ID) via `ensure_default_org`.

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST. This table only uses a DEFAULT now() column for `created_at`
(the caller never writes it), so no CTE is required here — a plain INSERT
avoids the asyncpg inference problem entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.org_repo")

DEFAULT_ORG_ID = "default"
DEFAULT_ORG_SLUG = "default"

_DDL = """
CREATE TABLE IF NOT EXISTS organizations (
    org_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class OrgRow:
    org_id: str
    name: str
    slug: str
    created_at: datetime | None = None


class PostgresOrgRepository:
    """Concrete store for `organizations` over a Postgres AsyncEngine.

    Mirrors `AppConfigRepository`: SQL lives only here, the engine is
    injected, and `ensure_schema()` is idempotent.
    """

    name: str = "org_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("org_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def create_org(self, *, org_id: str, name: str, slug: str) -> OrgRow:
        with _tracer.start_as_current_span("org_repo.create_org"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO organizations (org_id, name, slug)"
                        " VALUES (:i, :n, :s)"
                        " ON CONFLICT (org_id) DO NOTHING"
                    ),
                    {"i": org_id, "n": name, "s": slug},
                )
        got = await self.get_org(org_id)
        if got is None:
            raise RuntimeError(f"organization {org_id!r} vanished after upsert")
        return got

    async def get_org(self, org_id: str) -> OrgRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT org_id, name, slug, created_at"
                        " FROM organizations WHERE org_id = :i"
                    ),
                    {"i": org_id},
                )
            ).mappings().first()
        return OrgRow(**row) if row else None

    async def get_org_by_slug(self, slug: str) -> OrgRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT org_id, name, slug, created_at"
                        " FROM organizations WHERE slug = :s"
                    ),
                    {"s": slug},
                )
            ).mappings().first()
        return OrgRow(**row) if row else None

    async def list_orgs(self) -> list[OrgRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT org_id, name, slug, created_at"
                        " FROM organizations ORDER BY created_at"
                    )
                )
            ).mappings().all()
        return [OrgRow(**r) for r in rows]

    async def ensure_default_org(self) -> OrgRow:
        existing = await self.get_org(DEFAULT_ORG_ID)
        if existing is not None:
            return existing
        return await self.create_org(
            org_id=DEFAULT_ORG_ID, name="Default", slug=DEFAULT_ORG_SLUG
        )


__all__ = ["DEFAULT_ORG_ID", "DEFAULT_ORG_SLUG", "OrgRow", "PostgresOrgRepository"]
