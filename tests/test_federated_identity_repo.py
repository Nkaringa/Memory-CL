import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.federated_identity_repo import SqliteFederatedIdentityRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "f.db"))
    r = SqliteFederatedIdentityRepository(engine)
    await r.ensure_schema()
    return r

async def test_add_and_get_by_subject(repo):
    row = await repo.add(id="i1", user_id="u1", provider="p-google", subject="sub-123", email="a@b.c")
    assert row.user_id == "u1"
    got = await repo.get_by_subject(provider="p-google", subject="sub-123")
    assert got is not None and got.user_id == "u1"

async def test_get_by_subject_absent(repo):
    assert await repo.get_by_subject(provider="p-google", subject="nope") is None

async def test_list_for_user(repo):
    await repo.add(id="i1", user_id="u1", provider="p-google", subject="s1", email="a@b.c")
    await repo.add(id="i2", user_id="u1", provider="p-github", subject="s2", email="a@b.c")
    assert len(await repo.list_for_user("u1")) == 2

async def test_unique_provider_subject(repo):
    await repo.add(id="i1", user_id="u1", provider="p-google", subject="s1", email="a@b.c")
    with pytest.raises(Exception):
        await repo.add(id="i2", user_id="u2", provider="p-google", subject="s1", email="x@y.z")
