import pytest
from datetime import datetime, timezone, timedelta
from storage.lite.engine import make_sqlite_engine
from storage.lite.session_repo import SqliteSessionRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "s.db"))
    r = SqliteSessionRepository(engine)
    await r.ensure_schema()
    return r

def _exp(secs): return datetime.now(timezone.utc) + timedelta(seconds=secs)

async def test_create_and_get_active(repo):
    await repo.create_session(session_id="h1", user_id="u1", active_org_id="acme", csrf_token="c1", expires_at=_exp(3600))
    s = await repo.get_active("h1")
    assert s is not None and s.user_id == "u1" and s.active_org_id == "acme" and s.csrf_token == "c1"

async def test_expired_not_returned(repo):
    await repo.create_session(session_id="h2", user_id="u1", active_org_id="acme", csrf_token="c", expires_at=_exp(-1))
    assert await repo.get_active("h2") is None

async def test_revoke(repo):
    await repo.create_session(session_id="h3", user_id="u1", active_org_id="acme", csrf_token="c", expires_at=_exp(3600))
    await repo.revoke("h3")
    assert await repo.get_active("h3") is None

async def test_list_active_session_ids(repo):
    await repo.create_session(session_id="h4", user_id="u1", active_org_id="acme", csrf_token="c", expires_at=_exp(3600))
    await repo.create_session(session_id="h5", user_id="u1", active_org_id="acme", csrf_token="c", expires_at=_exp(-1))
    active = await repo.list_active_session_ids()
    assert "h4" in active and "h5" not in active

async def test_get_active_returns_datetime_fields(repo):
    await repo.create_session(session_id="h6", user_id="u1", active_org_id="acme", csrf_token="c", expires_at=_exp(3600))
    s = await repo.get_active("h6")
    assert s.expires_at.tzinfo is not None and s.created_at is not None
