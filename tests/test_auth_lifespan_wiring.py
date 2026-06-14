"""Task 9: Verify identity repos + SessionCache are wired into lifespan (lite mode)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def lite_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path / ".memcl"))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_default_org_seeded_and_caches_present(lite_client: TestClient) -> None:
    """Identity repos land in app_state; session_cache on app.state; default org seeded."""
    import asyncio

    app = lite_client.app

    # session_cache lives on app.state (mirrors token_cache pattern)
    assert app.state.session_cache is not None

    state = app.state.app_state

    # The four identity repos exist on AppState
    assert state.org_repo is not None
    assert state.user_repo is not None
    assert state.membership_repo is not None
    assert state.session_repo is not None

    # Default org was seeded during lifespan startup
    org = asyncio.get_event_loop().run_until_complete(state.org_repo.get_org("default"))
    assert org is not None
    assert org.slug == "default"

    # user table is empty on a fresh boot
    count = asyncio.get_event_loop().run_until_complete(state.user_repo.count_users())
    assert count == 0
