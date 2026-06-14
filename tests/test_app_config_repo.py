"""Unit-level checks for AppConfigRepository (SQL shape + idempotency).

The wire-level round-trip against real Postgres is covered by the golden
integration test; here we pin the DDL idempotency and the B14/B15 CAST
discipline so a regression in the SQL string fails in the fast suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from storage.app_config_repo import _DDL_STATEMENTS, _UPSERT, AppConfigRepository


def test_ddl_is_idempotent() -> None:
    for stmt in _DDL_STATEMENTS:
        normalized = " ".join(stmt.split())
        assert "IF NOT EXISTS" in normalized, f"non-idempotent: {normalized[:80]}"


def test_ddl_defines_app_config_columns() -> None:
    ddl = " ".join(_DDL_STATEMENTS[0].split())
    for col in (
        "mcp_api_key", "openai_api_key", "embedding_mode",
        "embedding_model", "onboarding_completed", "webhook_secret", "updated_at",
    ):
        assert col in ddl, f"missing column {col}"
    # Single logical row pinned by INTEGER PRIMARY KEY.
    assert "id INTEGER PRIMARY KEY" in ddl
    # An ALTER carries the new column onto an already-deployed table.
    assert any(
        "ADD COLUMN IF NOT EXISTS webhook_secret" in s for s in _DDL_STATEMENTS
    )


def test_upsert_casts_non_text_binds_in_cte() -> None:
    """B14/B15: BOOLEAN + TIMESTAMPTZ binds must be CAST inside the CTE or
    asyncpg sends them as text and Postgres rejects the insert."""
    sql = str(_UPSERT)
    assert "CAST(:onboarding_completed AS BOOLEAN)" in sql
    assert "CAST(:updated_at AS TIMESTAMPTZ)" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql


def test_repo_constructs_with_engine() -> None:
    repo = AppConfigRepository(engine=AsyncMock())  # type: ignore[arg-type]
    assert repo.name == "app_config_repo"
