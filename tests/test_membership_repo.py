import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.membership_repo import SqliteMembershipRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "m.db"))
    r = SqliteMembershipRepository(engine)
    await r.ensure_schema()
    return r

async def test_add_and_get_membership(repo):
    m = await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    assert m.role == "owner"
    got = await repo.get_membership(user_id="u1", org_id="acme")
    assert got.role == "owner"

async def test_list_orgs_for_user(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    await repo.add_member(membership_id="m2", user_id="u1", org_id="beta", role="member")
    orgs = await repo.list_orgs_for_user("u1")
    assert {o.org_id for o in orgs} == {"acme", "beta"}

async def test_list_members_of_org(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    await repo.add_member(membership_id="m2", user_id="u2", org_id="acme", role="member")
    assert len(await repo.list_members(org_id="acme")) == 2

async def test_set_role(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="member")
    await repo.set_role(user_id="u1", org_id="acme", role="admin")
    assert (await repo.get_membership(user_id="u1", org_id="acme")).role == "admin"

async def test_get_membership_absent_returns_none(repo):
    assert await repo.get_membership(user_id="nope", org_id="acme") is None
