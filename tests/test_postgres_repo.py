from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from schemas import IngestionUnit, Language, UnitKind, content_sha, stable_unit_id
from storage import IngestionUnitRepository
from storage.postgres_repo import (
    _DDL_STATEMENTS,
    PostgresIngestionRepository,
    _unit_to_params,
)


def _unit() -> IngestionUnit:
    src = "def f(): return 1\n"
    return IngestionUnit(
        unit_id=stable_unit_id("r", "pkg/m.py", "pkg.m.f"),
        repo_id="r",
        commit_sha="c1",
        kind=UnitKind.FUNCTION,
        name="f",
        qualified_name="pkg.m.f",
        parent_qualified_name="pkg.m",
        file_path="pkg/m.py",
        language=Language.PYTHON,
        line_start=1,
        line_end=1,
        content=src,
        source_sha=content_sha(src),
        signature="def f()",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_repository_satisfies_protocol() -> None:
    repo = PostgresIngestionRepository(engine=AsyncMock())  # type: ignore[arg-type]
    assert isinstance(repo, IngestionUnitRepository)


def test_ddl_statements_are_idempotent() -> None:
    """Every DDL statement must be IF NOT EXISTS so re-running is a no-op."""
    for stmt in _DDL_STATEMENTS:
        normalized = " ".join(stmt.split())
        assert "IF NOT EXISTS" in normalized, f"non-idempotent: {normalized[:80]}"


def test_unit_to_params_preserves_all_fields() -> None:
    u = _unit()
    p = _unit_to_params(u)
    # Spot-check the contract — adding a column without updating
    # _unit_to_params would silently drop data.
    expected_keys = {
        "unit_id", "repo_id", "commit_sha", "kind", "name", "qualified_name",
        "parent_qualified_name", "file_path", "language", "line_start", "line_end",
        "content", "source_sha", "docstring", "signature",
        "imports", "calls", "references", "bases", "token_count",
        "schema_version", "created_at", "updated_at", "source", "checksum",
    }
    assert set(p.keys()) == expected_keys
    assert p["unit_id"] == u.unit_id
    assert p["language"] == "python"
    assert p["kind"] == "fn"


def test_upsert_uses_only_changed_predicate() -> None:
    """The UPSERT SQL must include the source_sha guard, otherwise every
    re-ingest would rewrite every row and break invalidation downstream.
    """
    from storage.postgres_repo import _UPSERT_SQL

    sql = str(_UPSERT_SQL).lower()
    assert "on conflict (unit_id) do update" in sql
    assert re.search(
        r"where\s+ingestion_units\.source_sha\s*<>\s*excluded\.source_sha",
        sql,
    )
    assert "returning (xmax = 0) as inserted" in sql


# ---------- Behavior tests with a mocked engine ----------
class _FakeResult:
    def __init__(self, row: Any | None = None, rowcount: int = 0,
                 rows: list[Any] | None = None) -> None:
        self._row = row
        self.rowcount = rowcount
        self._rows = rows or []

    def first(self) -> Any | None:
        return self._row

    def all(self) -> list[Any]:
        return self._rows


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.next_results: list[_FakeResult] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((str(stmt), params))
        if self.next_results:
            return self.next_results.pop(0)
        return _FakeResult()


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    @asynccontextmanager
    async def begin(self):
        yield self.conn

    @asynccontextmanager
    async def connect(self):
        yield self.conn


@pytest.mark.asyncio
async def test_ensure_schema_runs_every_ddl_statement() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]
    await repo.ensure_schema()
    assert len(engine.conn.calls) == len(_DDL_STATEMENTS)


@pytest.mark.asyncio
async def test_upsert_returns_true_only_when_row_changed() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]

    # Simulate "row changed": RETURNING produced one row.
    engine.conn.next_results = [_FakeResult(row={"inserted": True})]
    assert await repo.upsert_unit(_unit()) is True

    # Simulate "row unchanged": RETURNING produced nothing.
    engine.conn.next_results = [_FakeResult(row=None)]
    assert await repo.upsert_unit(_unit()) is False


@pytest.mark.asyncio
async def test_delete_units_returns_rowcount() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]
    engine.conn.next_results = [_FakeResult(rowcount=3)]
    deleted = await repo.delete_units_for_file("r", "pkg/m.py")
    assert deleted == 3


@pytest.mark.asyncio
async def test_list_repos_executes_aggregate_and_maps_rows() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]
    engine.conn.next_results = [_FakeResult(rows=[
        {"repo_id": "alpha", "units": 10, "files": 4, "languages": ["python"]},
        {"repo_id": "beta", "units": 2, "files": 1,
         "languages": ["python", "typescript"]},
    ])]
    repos = await repo.list_repos()

    # SQL shape: one aggregate over ingestion_units, no bind params.
    stmt, params = engine.conn.calls[0]
    sql = " ".join(stmt.lower().split())
    assert "from ingestion_units" in sql
    assert "group by repo_id" in sql
    assert "order by repo_id" in sql
    assert "count(distinct file_path)" in sql
    assert "array_agg(distinct language)" in sql
    assert params is None

    assert [r.repo_id for r in repos] == ["alpha", "beta"]
    assert repos[0].units == 10
    assert repos[0].files == 4
    assert repos[0].languages == ("python",)
    assert repos[1].languages == ("python", "typescript")


@pytest.mark.asyncio
async def test_list_repos_handles_empty_table_and_null_languages() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]

    engine.conn.next_results = [_FakeResult(rows=[])]
    assert await repo.list_repos() == []

    engine.conn.next_results = [_FakeResult(rows=[
        {"repo_id": "r", "units": 1, "files": 1, "languages": None},
    ])]
    repos = await repo.list_repos()
    assert repos[0].languages == ()


@pytest.mark.asyncio
async def test_upsert_units_counts_only_changed_rows() -> None:
    engine = _FakeEngine()
    repo = PostgresIngestionRepository(engine=engine)  # type: ignore[arg-type]
    # 3 inputs, 2 marked changed (RETURNING produced row), 1 unchanged.
    engine.conn.next_results = [
        _FakeResult(row={"inserted": True}),
        _FakeResult(row=None),
        _FakeResult(row={"inserted": False}),
    ]
    changed = await repo.upsert_units([_unit(), _unit(), _unit()])
    assert changed == 2
