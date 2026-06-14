"""Real round-trip tests for the lite SQLite config/registry/token repos."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.api_token_repo import hash_token
from storage.lite.api_token_repo import SqliteApiTokenRepository
from storage.lite.app_config_repo import SqliteAppConfigRepository
from storage.lite.engine import make_sqlite_engine
from storage.lite.repo_registry_repo import SqliteRepoRegistryRepository

pytestmark = pytest.mark.asyncio


def _engine(tmp_path: Path):
    return make_sqlite_engine(tmp_path / "t.db")


# ---- app_config -----------------------------------------------------------
async def test_app_config_roundtrip(tmp_path: Path) -> None:
    repo = SqliteAppConfigRepository(_engine(tmp_path))
    await repo.ensure_schema()
    assert await repo.get() is None
    await repo.set_mcp_api_key("key-1")
    await repo.set_webhook_secret("wh-secret")
    await repo.set_embedding_mode("local")
    row = await repo.get()
    assert row is not None
    assert row.mcp_api_key == "key-1"
    assert row.webhook_secret == "wh-secret"
    assert row.embedding_mode == "local"
    assert row.onboarding_completed is False
    await repo.set_onboarding_completed(True)
    assert (await repo.get()).onboarding_completed is True
    # updates preserve other fields
    assert (await repo.get()).mcp_api_key == "key-1"


# ---- repo_registry --------------------------------------------------------
async def test_repo_registry_local_and_managed(tmp_path: Path) -> None:
    repo = SqliteRepoRegistryRepository(_engine(tmp_path))
    await repo.ensure_schema()
    await repo.upsert_local("loc", "/repos/loc", "sha1")
    await repo.add_managed("man", "/managed/man", "https://x/y.git", "main", "sha2")
    rows = {r.repo_id: r for r in await repo.list_all()}
    assert rows["loc"].source_type == "local"
    assert rows["man"].source_type == "managed" and rows["man"].branch == "main"
    # watch toggle + list_watched
    await repo.set_watch_enabled("man", False)
    assert {r.repo_id for r in await repo.list_watched()} == {"loc"}
    # mark_* + error
    await repo.mark_error("loc", "boom")
    assert (await repo.get("loc")).last_error == "boom"
    await repo.mark_synced("loc", "sha9")
    got = await repo.get("loc")
    assert got.last_commit_sha == "sha9" and got.last_error is None
    await repo.delete("man")
    assert await repo.get("man") is None


# ---- api_tokens -----------------------------------------------------------
async def test_api_token_lifecycle(tmp_path: Path) -> None:
    repo = SqliteApiTokenRepository(_engine(tmp_path))
    await repo.ensure_schema()
    raw, row = await repo.issue("laptop")
    assert row.name == "laptop" and row.token_hint.endswith(raw[-4:])
    # the issued token's hash is active
    assert hash_token(raw) in await repo.list_active_hashes()
    listed = await repo.list_all()
    assert len(listed) == 1 and listed[0].id == row.id
    # revoke drops it from the active set
    assert await repo.revoke(row.id) is True
    assert hash_token(raw) not in await repo.list_active_hashes()
    assert (await repo.get(row.id)).revoked is True
    # revoking again is a no-op
    assert await repo.revoke(row.id) is False
