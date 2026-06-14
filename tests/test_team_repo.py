import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.team_repo import SqliteTeamRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "t.db"))
    r = SqliteTeamRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_team_and_list_for_org(repo):
    t = await repo.create_team(team_id="t1", org_id="acme", name="Core", slug="core")
    assert t.slug == "core"
    assert [x.team_id for x in await repo.list_teams(org_id="acme")] == ["t1"]

async def test_add_member_and_list(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="Core", slug="core")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.add_team_member(team_id="t1", user_id="u2")
    assert {m for m in await repo.list_team_member_ids("t1")} == {"u1", "u2"}

async def test_add_member_idempotent(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="Core", slug="core")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.add_team_member(team_id="t1", user_id="u1")  # no error on dup (PK conflict ignored)
    assert await repo.list_team_member_ids("t1") == ["u1"]

async def test_team_ids_for_user(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="C", slug="core")
    await repo.create_team(team_id="t2", org_id="acme", name="D", slug="data")
    await repo.create_team(team_id="t3", org_id="other", name="E", slug="e")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.add_team_member(team_id="t2", user_id="u1")
    await repo.add_team_member(team_id="t3", user_id="u1")
    assert set(await repo.team_ids_for_user(user_id="u1", org_id="acme")) == {"t1", "t2"}  # org-scoped

async def test_remove_member_and_delete_team(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="C", slug="core")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.remove_team_member(team_id="t1", user_id="u1")
    assert await repo.list_team_member_ids("t1") == []
    await repo.delete_team("t1")
    assert await repo.get_team("t1") is None

async def test_unique_org_slug(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="C", slug="core")
    with pytest.raises(Exception):
        await repo.create_team(team_id="t2", org_id="acme", name="C2", slug="core")
