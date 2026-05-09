"""Phase-9 HTTP-surface tests.

Mounts a FastAPI app with the new routers and a stubbed lifespan,
then exercises the snapshot / audit / status endpoints.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import audit as audit_router
from apps.api.routers import snapshot as snapshot_router
from apps.api.routers import status as status_router
from apps.api.state import AppState
from apps.mcp.registry import build_default_registry
from core.config import Settings, get_settings
from core.governance import AuditAction, AuditActor, AuditLogger
from core.safety import FeatureFlagRegistry, SafeModeController


@pytest.fixture(autouse=True)
def _settings_cache_clear():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build_app(*, audit_logger: AuditLogger | None = None) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        state = AppState.with_default_embedder(
            postgres=AsyncMock(), qdrant=AsyncMock(),
            neo4j=AsyncMock(), redis=AsyncMock(),
            units_repo=AsyncMock(), graph_repo=AsyncMock(), vector_repo=AsyncMock(),
            embedding_dimension=32,
        )
        # Phase-9 attributes attached after construction.
        state.safe_mode = SafeModeController()  # type: ignore[attr-defined]
        state.feature_flags = FeatureFlagRegistry.from_settings(Settings())  # type: ignore[attr-defined]
        state.audit_logger = audit_logger or AuditLogger()  # type: ignore[attr-defined]
        registry = build_default_registry()
        state.mcp_registry = registry  # type: ignore[attr-defined]
        app.state.app_state = state
        app.state.mcp_registry = registry
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(snapshot_router.router)
    app.include_router(audit_router.router)
    app.include_router(status_router.router)
    return app


# ---- /snapshot ------------------------------------------------------------
def test_snapshot_build_returns_components_payload() -> None:
    app = _build_app()
    with TestClient(app) as client:
        resp = client.post("/snapshot/build", json={
            "tenant_id": "acme", "state_version_token": "v1",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "acme"
    # Spec-mandated component keys.
    assert set(body["components"]) == {
        "graph_state_hash", "embedding_index_hash",
        "retrieval_config_hash", "schema_version",
        "mcp_registry_hash", "state_version_token",
    }


def test_snapshot_build_id_is_deterministic_across_calls() -> None:
    app = _build_app()
    with TestClient(app) as client:
        a = client.post("/snapshot/build", json={
            "tenant_id": "acme", "state_version_token": "v0",
        }).json()
        b = client.post("/snapshot/build", json={
            "tenant_id": "acme", "state_version_token": "v0",
        }).json()
    # Same tenant + same registry + same state token → same components,
    # same id; only `captured_at` differs.
    assert a["components"] == b["components"]


def test_snapshot_replay_reports_match_for_equal_payload() -> None:
    app = _build_app()
    with TestClient(app) as client:
        resp = client.post("/snapshot/replay", json={
            "snapshot_id": "snap-x",
            "payload": {"a": 1, "b": [1, 2, 3]},
            "expected_output": {"b": [1, 2, 3], "a": 1},
        })
    body = resp.json()
    assert body["matches"] is True
    assert body["expected_hash"] == body["actual_hash"]


def test_snapshot_replay_reports_mismatch_for_drifted_payload() -> None:
    app = _build_app()
    with TestClient(app) as client:
        resp = client.post("/snapshot/replay", json={
            "snapshot_id": "snap-x",
            "payload": {"a": 999},
            "expected_output": {"a": 1},
        })
    body = resp.json()
    assert body["matches"] is False
    assert body["expected_hash"] != body["actual_hash"]


def test_snapshot_replay_rejects_missing_payload() -> None:
    app = _build_app()
    with TestClient(app) as client:
        resp = client.post("/snapshot/replay", json={
            "snapshot_id": "snap-x",
        })
    assert resp.status_code == 400


# ---- /audit ---------------------------------------------------------------
def test_audit_tail_returns_recent_chain_entries() -> None:
    logger = AuditLogger()
    for i in range(3):
        logger.record(
            actor=AuditActor.SYSTEM, action=AuditAction.UPDATE,
            entity_id=f"u{i}", tenant_id="acme",
            before_hash=f"b{i}", after_hash=f"a{i}",
        )
    app = _build_app(audit_logger=logger)
    with TestClient(app) as client:
        body = client.get("/audit/tail?limit=2").json()
    assert body["chain_length"] == 3
    assert len(body["entries"]) == 2
    # Most recent two are returned in chain order.
    assert [e["seq"] for e in body["entries"]] == [1, 2]


def test_audit_verify_reports_intact_chain() -> None:
    logger = AuditLogger()
    logger.record(
        actor=AuditActor.SYSTEM, action=AuditAction.UPDATE,
        entity_id="u", tenant_id="t", before_hash="x", after_hash="y",
    )
    app = _build_app(audit_logger=logger)
    with TestClient(app) as client:
        body = client.get("/audit/verify").json()
    assert body["intact"] is True
    assert body["chain_length"] == 1


# ---- /status --------------------------------------------------------------
def test_status_returns_full_posture() -> None:
    app = _build_app()
    with TestClient(app) as client:
        body = client.get("/status").json()
    assert body["service"] == "memory-cl"
    assert body["environment"] == "development"
    assert body["mcp_tool_count"] == 7
    assert body["safe_mode"]["enabled"] is False
    assert body["schema_version"] == "1"
    # FeatureFlagRegistry surfaced.
    flag_names = {f["name"] for f in body["feature_flags"]}
    assert "ui_enabled" in flag_names
