"""Unit-level checks for RepoRegistryRepository (SQL shape + idempotency).

The wire-level round-trip against real Postgres is covered by the golden
integration test; here we pin the DDL idempotency and the B14/B15 CAST
discipline so a regression in the SQL string fails in the fast suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from storage.repo_registry_repo import (
    _DDL_STATEMENTS,
    _MARK_SYNCED,
    _SET_WATCH,
    _UPSERT_LOCAL,
    _UPSERT_MANAGED,
    RepoRegistryRepository,
)


def test_ddl_is_idempotent() -> None:
    for stmt in _DDL_STATEMENTS:
        normalized = " ".join(stmt.split())
        assert "IF NOT EXISTS" in normalized, f"non-idempotent: {normalized[:80]}"


def test_ddl_defines_registry_columns() -> None:
    ddl = " ".join(_DDL_STATEMENTS[0].split())
    for col in (
        "repo_id", "source_type", "repo_path", "remote_url", "branch",
        "last_commit_sha", "watch_enabled", "last_synced_at",
        "last_change_at", "last_error", "created_at", "updated_at",
    ):
        assert col in ddl, f"missing column {col}"
    assert "repo_id TEXT PRIMARY KEY" in ddl
    assert "watch_enabled BOOLEAN NOT NULL DEFAULT true" in ddl


def test_upsert_local_casts_timestamp_and_preserves_managed_identity() -> None:
    sql = str(_UPSERT_LOCAL)
    # B14/B15: TIMESTAMPTZ bind CAST inside the CTE.
    assert "CAST(:ts AS TIMESTAMPTZ)" in sql
    assert "ON CONFLICT (repo_id) DO UPDATE" in sql
    # On conflict it must NOT clobber the managed identity or the user's
    # pause setting — only path + sync fields move.
    assert "source_type" not in sql.split("DO UPDATE")[1]
    assert "watch_enabled" not in sql.split("DO UPDATE")[1]
    assert "remote_url" not in sql.split("DO UPDATE")[1]


def test_upsert_managed_sets_source_and_casts() -> None:
    sql = str(_UPSERT_MANAGED)
    assert "CAST(:ts AS TIMESTAMPTZ)" in sql
    assert "ON CONFLICT (repo_id) DO UPDATE" in sql
    assert "source_type     = 'managed'" in sql or "source_type = 'managed'" in sql


def test_mutators_cast_non_text_binds() -> None:
    assert "CAST(:ts AS TIMESTAMPTZ)" in str(_MARK_SYNCED)
    assert "CAST(:watch_enabled AS BOOLEAN)" in str(_SET_WATCH)
    assert "CAST(:ts AS TIMESTAMPTZ)" in str(_SET_WATCH)


def test_repo_constructs_with_engine() -> None:
    repo = RepoRegistryRepository(engine=AsyncMock())  # type: ignore[arg-type]
    assert repo.name == "repo_registry_repo"
