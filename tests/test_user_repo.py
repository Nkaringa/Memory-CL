"""Round-trip tests for the lite SQLite user + local credentials repo."""

from __future__ import annotations

import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.user_repo import SqliteUserRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "u.db"))
    r = SqliteUserRepository(engine)
    await r.ensure_schema()
    return r


async def test_create_user_and_get_by_email_is_case_insensitive(repo):
    u = await repo.create_user(user_id="u1", email="A@Example.com", display_name="A")
    assert u.email == "a@example.com"
    assert (await repo.get_by_email("a@example.COM")).user_id == "u1"


async def test_set_and_get_password_hash(repo):
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    await repo.set_password(user_id="u1", password_hash="$argon2id$xxx")
    assert await repo.get_password_hash("u1") == "$argon2id$xxx"


async def test_count_users(repo):
    assert await repo.count_users() == 0
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    assert await repo.count_users() == 1


async def test_duplicate_email_rejected(repo):
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    with pytest.raises(Exception):
        await repo.create_user(user_id="u2", email="A@B.C", display_name="B")


async def test_get_user_by_id(repo):
    await repo.create_user(user_id="u1", email="a@b.c", display_name="Alice", avatar_url="http://x/a.png")
    got = await repo.get_user("u1")
    assert got.display_name == "Alice" and got.avatar_url == "http://x/a.png" and got.status == "active"
