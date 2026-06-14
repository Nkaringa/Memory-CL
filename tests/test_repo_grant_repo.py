import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.repo_grant_repo import SqliteRepoGrantRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "g.db"))
    r = SqliteRepoGrantRepository(engine)
    await r.ensure_schema()
    return r

async def test_grant_and_list_for_repo(repo):
    g = await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="write")
    assert g.access == "write"
    rows = await repo.list_for_repo(repo_id="r1")
    assert len(rows) == 1 and rows[0].subject_id == "t1"

async def test_regrant_updates_level(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="read")
    await repo.grant(id="g2", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="admin")
    rows = await repo.list_for_repo(repo_id="r1")
    assert len(rows) == 1 and rows[0].access == "admin"  # UNIQUE(repo,subject_type,subject_id) upsert

async def test_list_for_subjects(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="user", subject_id="u1", access="read")
    await repo.grant(id="g2", org_id="acme", repo_id="r2", subject_type="team", subject_id="t1", access="write")
    await repo.grant(id="g3", org_id="acme", repo_id="r3", subject_type="team", subject_id="t9", access="admin")  # not u1's team
    rows = await repo.list_for_subjects(org_id="acme", user_id="u1", team_ids=["t1"])
    assert {(r.repo_id, r.access) for r in rows} == {("r1", "read"), ("r2", "write")}

async def test_list_for_subjects_empty_team_ids(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="user", subject_id="u1", access="read")
    rows = await repo.list_for_subjects(org_id="acme", user_id="u1", team_ids=[])
    assert {(r.repo_id, r.access) for r in rows} == {("r1", "read")}  # empty team list still returns user grants

async def test_revoke_and_delete_for_repo(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="user", subject_id="u1", access="read")
    await repo.grant(id="g2", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="write")
    await repo.revoke("g1")
    assert len(await repo.list_for_repo(repo_id="r1")) == 1
    await repo.delete_for_repo("r1")
    assert await repo.list_for_repo(repo_id="r1") == []
