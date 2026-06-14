"""Task 6: Verify team/grant/invitation repos are wired into AppState (lite mode)."""

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


def test_rbac_repos_present(lite_client):
    state = lite_client.app.state.app_state
    assert state.team_repo is not None
    assert state.repo_grant_repo is not None
    assert state.invitation_repo is not None
