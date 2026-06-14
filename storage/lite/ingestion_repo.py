"""SQLite-backed `IngestionUnitRepository` for lite mode.

Same Protocol + behavior as `PostgresIngestionRepository`, translated off
Postgres-isms: TEXT[] arrays become JSON text, TIMESTAMPTZ becomes ISO TEXT,
the CTE/CAST dance disappears (SQLite is dynamically typed), pg_trgm is
replaced by a plain LIKE, and inserted-vs-skipped is detected with
`RETURNING` instead of `xmax`. The pipeline can't tell the difference — it
only sees the Protocol.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from schemas import IngestionUnit, Language, UnitKind
from storage.repositories import QnameMatch, RepoSummary

_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS ingestion_units (
        unit_id                 TEXT PRIMARY KEY,
        repo_id                 TEXT NOT NULL,
        commit_sha              TEXT NOT NULL,
        kind                    TEXT NOT NULL,
        name                    TEXT NOT NULL,
        qualified_name          TEXT NOT NULL,
        parent_qualified_name   TEXT,
        file_path               TEXT NOT NULL,
        language                TEXT NOT NULL,
        line_start              INTEGER NOT NULL,
        line_end                INTEGER NOT NULL,
        content                 TEXT NOT NULL,
        source_sha              TEXT NOT NULL,
        docstring               TEXT,
        signature               TEXT,
        imports                 TEXT NOT NULL DEFAULT '[]',
        calls                   TEXT NOT NULL DEFAULT '[]',
        "references"            TEXT NOT NULL DEFAULT '[]',
        bases                   TEXT NOT NULL DEFAULT '[]',
        token_count             INTEGER NOT NULL DEFAULT 0,
        schema_version          TEXT NOT NULL,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        source                  TEXT NOT NULL,
        checksum                TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_units_repo_file ON ingestion_units (repo_id, file_path)",
    "CREATE INDEX IF NOT EXISTS ix_units_repo_qname ON ingestion_units (repo_id, qualified_name)",
    "CREATE INDEX IF NOT EXISTS ix_units_repo_kind ON ingestion_units (repo_id, kind)",
)

# Upsert that only updates (and only RETURNS a row) when source_sha actually
# changed — drives the `units_changed` metric, exactly like the Postgres
# version's xmax trick. SQLite ON CONFLICT ... DO UPDATE ... WHERE skips the
# row (no RETURNING) when the predicate is false.
_UPSERT_SQL = text("""
INSERT INTO ingestion_units (
    unit_id, repo_id, commit_sha, kind, name, qualified_name,
    parent_qualified_name, file_path, language, line_start, line_end,
    content, source_sha, docstring, signature,
    imports, calls, "references", bases, token_count,
    schema_version, created_at, updated_at, source, checksum
) VALUES (
    :unit_id, :repo_id, :commit_sha, :kind, :name, :qualified_name,
    :parent_qualified_name, :file_path, :language, :line_start, :line_end,
    :content, :source_sha, :docstring, :signature,
    :imports, :calls, :references, :bases, :token_count,
    :schema_version, :created_at, :updated_at, :source, :checksum
)
ON CONFLICT(unit_id) DO UPDATE SET
    commit_sha            = excluded.commit_sha,
    kind                  = excluded.kind,
    name                  = excluded.name,
    qualified_name        = excluded.qualified_name,
    parent_qualified_name = excluded.parent_qualified_name,
    file_path             = excluded.file_path,
    language              = excluded.language,
    line_start            = excluded.line_start,
    line_end              = excluded.line_end,
    content               = excluded.content,
    source_sha            = excluded.source_sha,
    docstring             = excluded.docstring,
    signature             = excluded.signature,
    imports               = excluded.imports,
    calls                 = excluded.calls,
    "references"          = excluded."references",
    bases                 = excluded.bases,
    token_count           = excluded.token_count,
    schema_version        = excluded.schema_version,
    updated_at            = excluded.updated_at,
    source                = excluded.source,
    checksum              = excluded.checksum
WHERE ingestion_units.source_sha <> excluded.source_sha
RETURNING unit_id
""")

_SELECT_BY_ID = text("SELECT * FROM ingestion_units WHERE unit_id = :unit_id")
_SELECT_FOR_FILE = text(
    "SELECT * FROM ingestion_units "
    "WHERE repo_id = :repo_id AND file_path = :file_path "
    "ORDER BY line_start, qualified_name"
)
_SELECT_FOR_REPO = text(
    "SELECT * FROM ingestion_units "
    "WHERE repo_id = :repo_id "
    "ORDER BY file_path, line_start, qualified_name"
)
_DELETE_FOR_FILE = text(
    "DELETE FROM ingestion_units WHERE repo_id = :repo_id AND file_path = :file_path"
)
_LIST_REPOS = text(
    "SELECT repo_id, COUNT(*) AS units, "
    "COUNT(DISTINCT file_path) AS files, "
    "GROUP_CONCAT(DISTINCT language) AS languages "
    "FROM ingestion_units GROUP BY repo_id ORDER BY repo_id"
)
# SQLite LIKE is case-insensitive for ASCII by default — matches ILIKE for
# qualified names (which are ASCII identifiers).
_SEARCH_QNAMES = text(
    "SELECT qualified_name, kind FROM ingestion_units "
    "WHERE repo_id = :repo_id AND qualified_name LIKE :pattern ESCAPE '\\' "
    "ORDER BY length(qualified_name), qualified_name LIMIT :limit"
)


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _row_to_unit(row: Any) -> IngestionUnit:
    m = row._mapping if hasattr(row, "_mapping") else row
    return IngestionUnit(
        unit_id=m["unit_id"],
        repo_id=m["repo_id"],
        commit_sha=m["commit_sha"],
        kind=UnitKind(m["kind"]),
        name=m["name"],
        qualified_name=m["qualified_name"],
        parent_qualified_name=m["parent_qualified_name"],
        file_path=m["file_path"],
        language=Language(m["language"]),
        line_start=m["line_start"],
        line_end=m["line_end"],
        content=m["content"],
        source_sha=m["source_sha"],
        docstring=m["docstring"],
        signature=m["signature"],
        imports=json.loads(m["imports"] or "[]"),
        calls=json.loads(m["calls"] or "[]"),
        references=json.loads(m["references"] or "[]"),
        bases=json.loads(m["bases"] or "[]"),
        token_count=m["token_count"],
        schema_version=m["schema_version"],
        created_at=_parse_dt(m["created_at"]),
        updated_at=_parse_dt(m["updated_at"]),
        source=m["source"],
        checksum=m["checksum"],
    )


def _unit_to_params(unit: IngestionUnit) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "repo_id": unit.repo_id,
        "commit_sha": unit.commit_sha,
        "kind": unit.kind.value,
        "name": unit.name,
        "qualified_name": unit.qualified_name,
        "parent_qualified_name": unit.parent_qualified_name,
        "file_path": unit.file_path,
        "language": unit.language.value,
        "line_start": unit.line_start,
        "line_end": unit.line_end,
        "content": unit.content,
        "source_sha": unit.source_sha,
        "docstring": unit.docstring,
        "signature": unit.signature,
        "imports": json.dumps(list(unit.imports)),
        "calls": json.dumps(list(unit.calls)),
        "references": json.dumps(list(unit.references)),
        "bases": json.dumps(list(unit.bases)),
        "token_count": unit.token_count,
        "schema_version": unit.schema_version,
        "created_at": unit.created_at.isoformat(),
        "updated_at": unit.updated_at.isoformat(),
        "source": unit.source,
        "checksum": unit.checksum,
    }


class SqliteIngestionRepository:
    """`IngestionUnitRepository` over a SQLite file (lite mode)."""

    name: str = "sqlite_ingestion_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            for stmt in _DDL_STATEMENTS:
                await conn.execute(text(stmt))

    async def upsert_unit(self, unit: IngestionUnit) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(_UPSERT_SQL, _unit_to_params(unit))
            return result.first() is not None

    async def upsert_units(self, units: Iterable[IngestionUnit]) -> int:
        units = list(units)
        if not units:
            return 0
        changed = 0
        async with self._engine.begin() as conn:
            for u in units:
                result = await conn.execute(_UPSERT_SQL, _unit_to_params(u))
                if result.first() is not None:
                    changed += 1
        return changed

    async def get_unit(self, unit_id: str) -> IngestionUnit | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_BY_ID, {"unit_id": unit_id})
            row = result.first()
            return _row_to_unit(row) if row else None

    async def list_units_for_file(
        self, repo_id: str, file_path: str
    ) -> Sequence[IngestionUnit]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                _SELECT_FOR_FILE, {"repo_id": repo_id, "file_path": file_path}
            )
            return [_row_to_unit(r) for r in result.all()]

    async def list_units_for_repo(self, repo_id: str) -> Sequence[IngestionUnit]:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_FOR_REPO, {"repo_id": repo_id})
            return [_row_to_unit(r) for r in result.all()]

    async def list_repos(self) -> Sequence[RepoSummary]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(_LIST_REPOS)).all()
        out: list[RepoSummary] = []
        for row in rows:
            m: Any = row._mapping if hasattr(row, "_mapping") else row
            langs = tuple(sorted((m["languages"] or "").split(","))) if m["languages"] else ()
            out.append(
                RepoSummary(
                    repo_id=m["repo_id"],
                    units=int(m["units"]),
                    files=int(m["files"]),
                    languages=langs,
                )
            )
        return out

    async def search_qnames(
        self, repo_id: str, query: str, limit: int = 20
    ) -> Sequence[QnameMatch]:
        pattern = f"%{_escape_like(query)}%"
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                _SEARCH_QNAMES,
                {"repo_id": repo_id, "pattern": pattern, "limit": limit},
            )).all()
        out: list[QnameMatch] = []
        for r in rows:
            m: Any = r._mapping if hasattr(r, "_mapping") else r
            out.append(QnameMatch(qualified_name=m["qualified_name"], kind=m["kind"]))
        return out

    async def delete_units_for_file(self, repo_id: str, file_path: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _DELETE_FOR_FILE, {"repo_id": repo_id, "file_path": file_path}
            )
        return result.rowcount or 0


__all__ = ["SqliteIngestionRepository"]
