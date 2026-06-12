from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from schemas import IngestionUnit, Language, UnitKind
from storage.repositories import QnameMatch, RepoSummary

_tracer = get_tracer("storage.postgres_repo")


# DDL is split into individual statements so it can run via execute() one
# at a time (asyncpg's prepared statements don't support multi-statement
# scripts). All statements are idempotent (`IF NOT EXISTS`).
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
        imports                 TEXT[] NOT NULL DEFAULT '{}',
        calls                   TEXT[] NOT NULL DEFAULT '{}',
        "references"            TEXT[] NOT NULL DEFAULT '{}',
        bases                   TEXT[] NOT NULL DEFAULT '{}',
        token_count             INTEGER NOT NULL DEFAULT 0,
        schema_version          TEXT NOT NULL,
        created_at              TIMESTAMPTZ NOT NULL,
        updated_at              TIMESTAMPTZ NOT NULL,
        source                  TEXT NOT NULL,
        checksum                TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_units_repo_file ON ingestion_units (repo_id, file_path)",
    "CREATE INDEX IF NOT EXISTS ix_units_repo_qname ON ingestion_units (repo_id, qualified_name)",
    "CREATE INDEX IF NOT EXISTS ix_units_repo_kind ON ingestion_units (repo_id, kind)",
)

# Idempotent upsert: only updates when source_sha actually changed. The
# CTE shape lets us tell, per row, whether it was inserted or updated
# vs. skipped — we use that to drive the `units_changed` metric.
_UPSERT_SQL = text("""
WITH input AS (
    SELECT
        :unit_id AS unit_id, :repo_id AS repo_id, :commit_sha AS commit_sha,
        :kind AS kind, :name AS name, :qualified_name AS qualified_name,
        :parent_qualified_name AS parent_qualified_name,
        :file_path AS file_path, :language AS language,
        -- Every non-TEXT column needs an explicit CAST in this CTE.
        -- asyncpg can't infer the target column type through the WITH
        -- wrapper, so a bare bind parameter arrives at the INSERT
        -- typed as TEXT and Postgres rejects with a "of type X but
        -- expression is of type text" error. TEXT and TEXT[] columns
        -- work as-is (asyncpg sends Python str/list[str] as text);
        -- the INTEGER and TIMESTAMPTZ columns below need the cast.
        --
        -- DO NOT put ":name"-style placeholders inside this comment
        -- block — SQLAlchemy's text() scans the entire string
        -- (comments included) for ":name" bind parameters and treats
        -- them as required, breaking execution at run time. (That's
        -- exactly how this comment got rewritten the second time.)
        CAST(:line_start AS INTEGER) AS line_start,
        CAST(:line_end AS INTEGER) AS line_end,
        :content AS content, :source_sha AS source_sha,
        :docstring AS docstring, :signature AS signature,
        CAST(:imports AS TEXT[]) AS imports,
        CAST(:calls AS TEXT[]) AS calls,
        CAST(:references AS TEXT[]) AS "references",
        CAST(:bases AS TEXT[]) AS bases,
        CAST(:token_count AS INTEGER) AS token_count,
        :schema_version AS schema_version,
        CAST(:created_at AS TIMESTAMPTZ) AS created_at,
        CAST(:updated_at AS TIMESTAMPTZ) AS updated_at,
        :source AS source, :checksum AS checksum
)
INSERT INTO ingestion_units (
    unit_id, repo_id, commit_sha, kind, name, qualified_name,
    parent_qualified_name, file_path, language, line_start, line_end,
    content, source_sha, docstring, signature,
    imports, calls, "references", bases, token_count,
    schema_version, created_at, updated_at, source, checksum
) SELECT
    unit_id, repo_id, commit_sha, kind, name, qualified_name,
    parent_qualified_name, file_path, language, line_start, line_end,
    content, source_sha, docstring, signature,
    imports, calls, "references", bases, token_count,
    schema_version, created_at, updated_at, source, checksum
  FROM input
ON CONFLICT (unit_id) DO UPDATE SET
    commit_sha            = EXCLUDED.commit_sha,
    kind                  = EXCLUDED.kind,
    name                  = EXCLUDED.name,
    qualified_name        = EXCLUDED.qualified_name,
    parent_qualified_name = EXCLUDED.parent_qualified_name,
    file_path             = EXCLUDED.file_path,
    language              = EXCLUDED.language,
    line_start            = EXCLUDED.line_start,
    line_end              = EXCLUDED.line_end,
    content               = EXCLUDED.content,
    source_sha            = EXCLUDED.source_sha,
    docstring             = EXCLUDED.docstring,
    signature             = EXCLUDED.signature,
    imports               = EXCLUDED.imports,
    calls                 = EXCLUDED.calls,
    "references"          = EXCLUDED."references",
    bases                 = EXCLUDED.bases,
    token_count           = EXCLUDED.token_count,
    schema_version        = EXCLUDED.schema_version,
    updated_at            = EXCLUDED.updated_at,
    source                = EXCLUDED.source,
    checksum              = EXCLUDED.checksum
WHERE ingestion_units.source_sha <> EXCLUDED.source_sha
RETURNING (xmax = 0) AS inserted
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
    "DELETE FROM ingestion_units "
    "WHERE repo_id = :repo_id AND file_path = :file_path"
)

# Pure aggregate — no bind parameters on purpose (and B16 reminder: no
# colon-prefixed tokens may appear anywhere in this string, comments
# included, or SQLAlchemy's text() will demand them as parameters).
_LIST_REPOS = text(
    "SELECT repo_id, "
    "COUNT(*) AS units, "
    "COUNT(DISTINCT file_path) AS files, "
    "ARRAY_AGG(DISTINCT language) AS languages "
    "FROM ingestion_units "
    "GROUP BY repo_id "
    "ORDER BY repo_id"
)


# Autocomplete over qualified names. Length-first ordering keeps the
# canonical short qname (e.g. a module) ahead of deep test paths that
# merely contain it. The bind parameters are all TEXT-compatible except
# LIMIT, which asyncpg types natively in a top-level statement — no
# CAST needed here (the CTE-typing issue only bites inside WITH blocks).
_SEARCH_QNAMES = text(
    "SELECT qualified_name, kind FROM ingestion_units "
    "WHERE repo_id = :repo_id AND qualified_name ILIKE :pattern "
    "ORDER BY length(qualified_name), qualified_name "
    "LIMIT :limit"
)


def _escape_like(query: str) -> str:
    """Escape LIKE/ILIKE metacharacters so user input matches literally.

    Backslash first (it is the escape character itself), then the two
    wildcards.
    """
    return (
        query.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _row_to_unit(row: Any) -> IngestionUnit:
    """Hydrate an IngestionUnit from an asyncpg/SQLAlchemy row mapping."""
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
        imports=list(m["imports"] or []),
        calls=list(m["calls"] or []),
        references=list(m["references"] or []),
        bases=list(m["bases"] or []),
        token_count=m["token_count"],
        schema_version=m["schema_version"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
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
        "imports": unit.imports,
        "calls": unit.calls,
        "references": unit.references,
        "bases": unit.bases,
        "token_count": unit.token_count,
        "schema_version": unit.schema_version,
        "created_at": unit.created_at,
        "updated_at": unit.updated_at,
        "source": unit.source,
        "checksum": unit.checksum,
    }


class PostgresIngestionRepository:
    """Concrete `IngestionUnitRepository` over the Phase-1 PostgresClient.

    The repository is the only place SQL appears outside of migrations
    so the rest of the system stays storage-agnostic.
    """

    name: str = "postgres_ingestion_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ----- Bootstrap -----
    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("postgres_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))

    # ----- Writes -----
    async def upsert_unit(self, unit: IngestionUnit) -> bool:
        start = time.perf_counter()
        with _tracer.start_as_current_span("postgres_repo.upsert_unit") as span:
            span.set_attribute("unit_id", unit.unit_id)
            span.set_attribute("repo_id", unit.repo_id)
            span.set_attribute("file_path", unit.file_path)
            async with self._engine.begin() as conn:
                result = await conn.execute(_UPSERT_SQL, _unit_to_params(unit))
                row = result.first()
            changed = row is not None
            emit_phase2_event(
                event="postgres_upsert_unit",
                operation="postgres_repo.upsert_unit",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                unit_id=unit.unit_id,
                file_path=unit.file_path,
                content_hash=unit.source_sha,
                changed=changed,
                level="debug",
            )
            return changed

    async def upsert_units(self, units: Iterable[IngestionUnit]) -> int:
        start = time.perf_counter()
        units = list(units)
        if not units:
            return 0
        changed = 0
        with _tracer.start_as_current_span("postgres_repo.upsert_units") as span:
            span.set_attribute("count", len(units))
            async with self._engine.begin() as conn:
                for u in units:
                    result = await conn.execute(_UPSERT_SQL, _unit_to_params(u))
                    if result.first() is not None:
                        changed += 1
            emit_phase2_event(
                event="postgres_upsert_batch",
                operation="postgres_repo.upsert_units",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                count=len(units),
                changed=changed,
                level="info",
            )
            return changed

    # ----- Reads -----
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
            result = await conn.execute(_LIST_REPOS)
            rows = result.all()
        out: list[RepoSummary] = []
        for row in rows:
            m: Any = row._mapping if hasattr(row, "_mapping") else row
            out.append(
                RepoSummary(
                    repo_id=m["repo_id"],
                    units=int(m["units"]),
                    files=int(m["files"]),
                    languages=tuple(m["languages"] or ()),
                )
            )
        return out

    async def search_qnames(
        self, repo_id: str, query: str, limit: int = 20
    ) -> Sequence[QnameMatch]:
        pattern = f"%{_escape_like(query)}%"
        async with self._engine.connect() as conn:
            result = await conn.execute(
                _SEARCH_QNAMES,
                {"repo_id": repo_id, "pattern": pattern, "limit": limit},
            )
            rows = result.all()
        out: list[QnameMatch] = []
        for row in rows:
            m: Any = row._mapping if hasattr(row, "_mapping") else row
            out.append(
                QnameMatch(
                    qualified_name=m["qualified_name"],
                    kind=m["kind"],
                )
            )
        return out

    async def delete_units_for_file(self, repo_id: str, file_path: str) -> int:
        with _tracer.start_as_current_span("postgres_repo.delete_units_for_file") as span:
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    _DELETE_FOR_FILE, {"repo_id": repo_id, "file_path": file_path}
                )
            return result.rowcount or 0
