"""SQLite-backed organizations repo for lite mode.

Same surface as PostgresOrgRepository. SQLite differences:
  - created_at uses TEXT + DEFAULT (datetime('now')) — no TIMESTAMPTZ, no now()
  - ISO TEXT timestamps are hydrated to datetime via _parse_dt (same helper
    pattern as storage/lite/app_config_repo.py)
  - ON CONFLICT(org_id) DO NOTHING works identically in SQLite
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.org_repo import DEFAULT_ORG_ID, DEFAULT_ORG_SLUG, OrgRow

_DDL = """
CREATE TABLE IF NOT EXISTS organizations (
    org_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_org(row: Any) -> OrgRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return OrgRow(
        org_id=m["org_id"],
        name=m["name"],
        slug=m["slug"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteOrgRepository:
    name: str = "sqlite_org_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def create_org(self, *, org_id: str, name: str, slug: str) -> OrgRow:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO organizations (org_id, name, slug)"
                    " VALUES (:i, :n, :s)"
                    " ON CONFLICT(org_id) DO NOTHING"
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
            ).first()
        return _row_to_org(row) if row else None

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
            ).first()
        return _row_to_org(row) if row else None

    async def list_orgs(self) -> list[OrgRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT org_id, name, slug, created_at"
                        " FROM organizations ORDER BY created_at"
                    )
                )
            ).all()
        return [_row_to_org(r) for r in rows]

    async def ensure_default_org(self) -> OrgRow:
        existing = await self.get_org(DEFAULT_ORG_ID)
        if existing is not None:
            return existing
        return await self.create_org(
            org_id=DEFAULT_ORG_ID, name="Default", slug=DEFAULT_ORG_SLUG
        )


__all__ = ["SqliteOrgRepository"]
