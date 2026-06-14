"""The whole FastAPI app boots + serves in lite mode (no Docker, no model
download — LocalEmbedder loads lazily, and we don't embed here).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def lite_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path / ".memcl"))
    from core import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()  # restore server-mode settings for other tests


def test_lite_app_status_and_surfaces(lite_client: TestClient) -> None:
    # Boot completed -> /status serves and the boot gate passed.
    status = lite_client.get("/status")
    assert status.status_code == 200
    body = status.json()
    assert body["mcp_tool_count"] >= 10
    # Lite defaults to local embeddings (no key needed) -> enabled.
    assert body["embeddings_enabled"] is True

    # Empty embedded stores respond cleanly.
    assert lite_client.get("/repos").json()["repos"] == []
    assert lite_client.get("/freshness").json()["repos"] == []

    # MCP tool registry is served.
    tools = lite_client.get("/mcp/tools").json()
    assert len(tools["tools"]) >= 10

    # No MCP key configured in a fresh lite boot -> dev mode (open).
    r = lite_client.post("/mcp/tools/list_repos", json={})
    assert r.status_code == 200
