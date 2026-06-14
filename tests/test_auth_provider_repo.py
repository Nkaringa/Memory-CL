import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.auth_provider_repo import SqliteAuthProviderRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "p.db"))
    r = SqliteAuthProviderRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_get_list(repo):
    row = await repo.create(id="p1", provider_type="google", display_name="Google", client_id="cid", client_secret="sec", discovery_url=None, scopes="openid email", enabled=True)
    assert row.provider_type == "google" and row.enabled is True
    assert (await repo.get("p1")).client_id == "cid"
    assert len(await repo.list_all()) == 1

async def test_list_enabled_only(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=True)
    await repo.create(id="p2", provider_type="github", display_name="GH", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=False)
    en = await repo.list_enabled()
    assert {p.id for p in en} == {"p1"}

async def test_update_and_set_enabled(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=False)
    await repo.update(id="p1", client_id="c2", client_secret="s2", scopes="openid", display_name="Google2", discovery_url=None)
    await repo.set_enabled(id="p1", enabled=True)
    row = await repo.get("p1")
    assert row.client_id == "c2" and row.enabled is True and row.display_name == "Google2"

async def test_delete(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=True)
    await repo.delete("p1")
    assert await repo.get("p1") is None

async def test_get_absent_returns_none(repo):
    assert await repo.get("nope") is None
