"""Freshness endpoint tests — fake registry, monkeypatched git/ingest."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.dependencies import (
    get_app_state,
    get_repo_registry,
    get_runtime_config,
)
from apps.api.freshness.managed import SyncResult
from apps.api.routers import freshness as fr
from core.config import Settings
from core.config_runtime import RuntimeConfig
from storage.repo_registry_repo import RepoRegistryRow


class _FakeRepoRow:
    pass


class FakeRegistry:
    def __init__(self, rows: list[RepoRegistryRow] | None = None) -> None:
        self.rows = {r.repo_id: r for r in (rows or [])}
        self.toggled: list[tuple[str, bool]] = []
        self.deleted: list[str] = []
        self.synced: list[tuple[str, str | None]] = []

    async def list_all(self):
        return list(self.rows.values())

    async def get(self, repo_id):
        return self.rows.get(repo_id)

    async def set_watch_enabled(self, repo_id, enabled):
        self.toggled.append((repo_id, enabled))

    async def delete(self, repo_id):
        self.deleted.append(repo_id)

    async def mark_synced(self, repo_id, commit_sha):
        self.synced.append((repo_id, commit_sha))

    async def mark_error(self, repo_id, message):
        pass


class _FakeConfigRepo:
    async def get(self):
        return None  # unconfigured -> ApiKeyDep dev mode -> mutations allowed


def _row(repo_id, source_type="managed", *, path=None, watch=True):
    return RepoRegistryRow(
        repo_id=repo_id, source_type=source_type,
        repo_path=path or f"/managed/{repo_id}",
        remote_url="https://github.com/x/y" if source_type == "managed" else None,
        branch="main" if source_type == "managed" else None,
        last_commit_sha="sha0", watch_enabled=watch, last_synced_at=None,
        last_change_at=None, last_error=None, created_at=datetime.now(UTC), updated_at=None,
    )


def _make_app(registry: FakeRegistry) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.runtime_config = RuntimeConfig(_FakeConfigRepo(), Settings())  # type: ignore[arg-type]
        await app.state.runtime_config.refresh()
        app.state.repo_registry = registry
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(fr.router)
    app.dependency_overrides[get_app_state] = lambda: object()
    app.dependency_overrides[get_runtime_config] = lambda: app.state.runtime_config
    app.dependency_overrides[get_repo_registry] = lambda: registry
    return app


# ---------------------------------------------------------------------------
def test_list_freshness_returns_rows() -> None:
    reg = FakeRegistry([_row("m1"), _row("loc", "local")])
    app = _make_app(reg)
    with TestClient(app) as client:
        body = client.get("/freshness").json()
    assert body["freshness_enabled"] is True
    ids = {r["repo_id"]: r["source_type"] for r in body["repos"]}
    assert ids == {"m1": "managed", "loc": "local"}


def test_toggle_unknown_is_404() -> None:
    app = _make_app(FakeRegistry())
    with TestClient(app) as client:
        assert client.post("/freshness/nope/toggle", json={"enabled": False}).status_code == 404


def test_toggle_pauses_repo() -> None:
    reg = FakeRegistry([_row("m1")])
    app = _make_app(reg)
    with TestClient(app) as client:
        assert client.post("/freshness/m1/toggle", json={"enabled": False}).status_code == 200
    assert reg.toggled == [("m1", False)]


def test_add_managed_invokes_clone_and_returns_id(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = FakeRegistry()
    calls: dict = {}

    async def fake_add(**kw):
        calls.update(kw)
        return SyncResult(repo_id="JA4M", changed=True, new_sha="deadbeef")

    monkeypatch.setattr(fr, "add_managed_repo", fake_add)
    app = _make_app(reg)
    with TestClient(app) as client:
        resp = client.post("/freshness/managed", json={"remote_url": "https://github.com/you/JA4M.git"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"repo_id": "JA4M", "commit_sha": "deadbeef"}
    assert calls["remote_url"] == "https://github.com/you/JA4M.git"


def test_add_managed_clone_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(fr, "add_managed_repo", boom)
    app = _make_app(FakeRegistry())
    with TestClient(app) as client:
        resp = client.post("/freshness/managed", json={"remote_url": "https://x/y.git"})
    assert resp.status_code == 502


def test_sync_managed_calls_syncer(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = FakeRegistry([_row("m1")])

    async def fake_sync(repo, **kw):
        return SyncResult(repo_id=repo.repo_id, changed=True, new_sha="newsha")

    monkeypatch.setattr(fr, "sync_managed_repo", fake_sync)
    app = _make_app(reg)
    with TestClient(app) as client:
        body = client.post("/freshness/m1/sync").json()
    assert body == {"repo_id": "m1", "changed": True, "new_sha": "newsha", "error": None}


def test_sync_local_reingests(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = FakeRegistry([_row("loc", "local", path="/repos/loc")])
    seen: dict = {}

    async def fake_run_ingest(state, settings, runtime, *, repo_id, repo_path, commit_sha):
        seen.update({"repo_id": repo_id, "repo_path": repo_path})

    monkeypatch.setattr(fr, "run_ingest", fake_run_ingest)
    app = _make_app(reg)
    with TestClient(app) as client:
        body = client.post("/freshness/loc/sync").json()
    assert body["repo_id"] == "loc" and body["changed"] is True
    assert seen == {"repo_id": "loc", "repo_path": "/repos/loc"}
    assert reg.synced == [("loc", "sha0")]


def test_delete_unknown_is_404() -> None:
    app = _make_app(FakeRegistry())
    with TestClient(app) as client:
        assert client.delete("/freshness/nope").status_code == 404


def test_delete_managed_deregisters() -> None:
    reg = FakeRegistry([_row("m1")])
    app = _make_app(reg)
    with TestClient(app) as client:
        assert client.delete("/freshness/m1").status_code == 200
    assert reg.deleted == ["m1"]
