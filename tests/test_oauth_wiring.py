"""Task 5: Verify auth-provider repos + OAuthRegistry + GET /auth/providers are wired (lite mode)."""

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


def test_provider_repos_and_registry_present(lite_client):
    app = lite_client.app
    assert app.state.oauth_registry is not None
    state = app.state.app_state
    assert state.auth_provider_repo is not None
    assert state.federated_identity_repo is not None


def test_providers_endpoint_empty_by_default(lite_client):
    r = lite_client.get("/auth/providers")
    assert r.status_code == 200 and r.json() == {"providers": []}
