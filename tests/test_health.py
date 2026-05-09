from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.state import AppState


def test_liveness_returns_ok(app_factory, healthy_state: AppState) -> None:
    app = app_factory(healthy_state)
    with TestClient(app) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "memory-cl"
    assert body["schema_version"] == "1"


def test_readiness_all_ok(app_factory, healthy_state: AppState) -> None:
    app = app_factory(healthy_state)
    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"

    names = [c["name"] for c in body["components"]]
    # Deterministic alphabetical ordering required by ARCHITECTURE_RULES.
    assert names == sorted(names)
    # Phase-10: readiness covers the four storage backends AND the MCP
    # registry — an alive process with no tools is not ready to serve.
    assert set(names) == {"mcp_registry", "neo4j", "postgres", "qdrant", "redis"}
    assert all(c["status"] == "ok" for c in body["components"])


def test_readiness_returns_503_when_backend_down(app_factory, degraded_state: AppState) -> None:
    app = app_factory(degraded_state)
    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] in ("degraded", "down")

    by_name = {c["name"]: c for c in body["components"]}
    assert by_name["qdrant"]["status"] == "down"
    assert by_name["qdrant"]["error"] == "connection refused"
    assert by_name["postgres"]["status"] == "ok"
