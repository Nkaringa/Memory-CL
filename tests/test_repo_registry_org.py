"""TDD tests for org_id on repo_registry (Phase-3 RBAC Task 1).

Verifies that:
  - org_id roundtrips through upsert_local
  - org_id defaults to 'default' when omitted
Both via the SQLite lite backend (fast, no Postgres needed).
"""

from __future__ import annotations

import pytest

from storage.lite.engine import make_sqlite_engine
from storage.lite.repo_registry_repo import SqliteRepoRegistryRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "r.db"))
    r = SqliteRepoRegistryRepository(engine)
    await r.ensure_schema()
    return r


async def test_org_id_roundtrips(repo):
    await repo.upsert_local(repo_id="acme", org_id="team-1", repo_path="/x", commit_sha=None)
    row = await repo.get("acme")
    assert row is not None and row.org_id == "team-1"


async def test_default_org_when_omitted(repo):
    await repo.upsert_local(repo_id="acme2", repo_path="/x", commit_sha=None)
    assert (await repo.get("acme2")).org_id == "default"


async def test_org_id_on_add_managed(repo):
    """add_managed also respects org_id param and defaults to 'default'."""
    await repo.add_managed(
        repo_id="managed-1",
        org_id="team-2",
        repo_path="/managed/m1",
        remote_url="https://github.com/x/y.git",
        branch="main",
        commit_sha=None,
    )
    row = await repo.get("managed-1")
    assert row is not None and row.org_id == "team-2"


async def test_org_id_default_on_add_managed(repo):
    await repo.add_managed(
        repo_id="managed-2",
        repo_path="/managed/m2",
        remote_url="https://github.com/x/z.git",
        branch=None,
        commit_sha=None,
    )
    assert (await repo.get("managed-2")).org_id == "default"


async def test_existing_callers_still_work(repo):
    """Legacy call sites that pass positional args (repo_id, repo_path, commit_sha) work unchanged."""
    await repo.upsert_local("legacy", "/repos/legacy", "sha123")
    row = await repo.get("legacy")
    assert row is not None
    assert row.org_id == "default"
    assert row.last_commit_sha == "sha123"
