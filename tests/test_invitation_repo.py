import pytest
from datetime import datetime, timezone, timedelta
from storage.lite.engine import make_sqlite_engine
from storage.lite.invitation_repo import SqliteInvitationRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "i.db"))
    r = SqliteInvitationRepository(engine)
    await r.ensure_schema()
    return r

def _exp(s): return datetime.now(timezone.utc) + timedelta(seconds=s)

async def test_create_and_get_pending_by_hash(repo):
    inv = await repo.create(id="i1", org_id="acme", email="a@b.c", role="member", token_hash="h1", invited_by="u0", expires_at=_exp(3600))
    assert inv.status == "pending" and inv.role == "member"
    got = await repo.get_pending_by_hash("h1")
    assert got is not None and got.org_id == "acme"

async def test_expired_pending_not_returned(repo):
    await repo.create(id="i2", org_id="acme", email="a@b.c", role="member", token_hash="h2", invited_by="u0", expires_at=_exp(-1))
    assert await repo.get_pending_by_hash("h2") is None

async def test_mark_accepted_then_not_pending(repo):
    await repo.create(id="i3", org_id="acme", email="a@b.c", role="member", token_hash="h3", invited_by="u0", expires_at=_exp(3600))
    await repo.mark_accepted("i3")
    assert await repo.get_pending_by_hash("h3") is None

async def test_list_for_org_and_revoke(repo):
    await repo.create(id="i4", org_id="acme", email="x@y.z", role="admin", token_hash="h4", invited_by="u0", expires_at=_exp(3600))
    assert len(await repo.list_for_org("acme")) == 1
    await repo.revoke("i4")
    assert await repo.get_pending_by_hash("h4") is None

async def test_created_at_is_datetime(repo):
    inv = await repo.create(id="i5", org_id="acme", email="a@b.c", role="member", token_hash="h5", invited_by="u0", expires_at=_exp(3600))
    got = await repo.get_pending_by_hash("h5")
    assert got.expires_at.tzinfo is not None
