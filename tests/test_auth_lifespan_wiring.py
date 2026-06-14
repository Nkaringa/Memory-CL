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
    app = lite_client.app

    # session_cache lives on app.state (mirrors token_cache pattern)
    assert app.state.session_cache is not None

    state = app.state.app_state

    # The four identity repos exist on AppState
    assert state.org_repo is not None
    assert state.user_repo is not None
    assert state.membership_repo is not None
    assert state.session_repo is not None

    # Verify the default org was seeded + the users table is empty on a fresh boot,
    # through HTTP (the sync TestClient drives requests on the app's own loop, so we
    # avoid both cross-loop repo access and async-fixture teardown cancel-scope errors).
    # The first registrant becomes "owner" of the seeded "default" org — only possible
    # if the org exists and the users table started empty.
    assert lite_client.get("/auth/me").json()["authenticated"] is False
    reg = lite_client.post(
        "/auth/register",
        json={"email": "a@b.c", "password": "password123", "display_name": "A"},
    )
    assert reg.status_code == 200
    user = reg.json()["user"]
    assert user["org_id"] == "default"
    assert user["roles"] == ["owner"]
