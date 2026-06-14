"""Round-trip tests for the lite SQLite organization repo."""

from __future__ import annotations

import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.org_repo import SqliteOrgRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "t.db"))
    r = SqliteOrgRepository(engine)
    await r.ensure_schema()
    return r


async def test_create_and_get_org(repo):
    row = await repo.create_org(org_id="acme", name="Acme", slug="acme")
    assert row.org_id == "acme" and row.slug == "acme"
    got = await repo.get_org("acme")
    assert got is not None and got.name == "Acme"


async def test_get_by_slug_and_list(repo):
    await repo.create_org(org_id="o1", name="One", slug="one")
    assert (await repo.get_org_by_slug("one")).org_id == "o1"
    assert len(await repo.list_orgs()) == 1


async def test_default_org_idempotent(repo):
    a = await repo.ensure_default_org()
    b = await repo.ensure_default_org()
    assert a.org_id == b.org_id
    assert len(await repo.list_orgs()) == 1
