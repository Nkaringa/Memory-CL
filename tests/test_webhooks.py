"""Git-webhook tests — pure verify/parse/match helpers + the endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from apps.api.dependencies import (
    get_app_state,
    get_repo_registry,
    get_runtime_config,
)
from apps.api.freshness.managed import SyncResult
from apps.api.routers import webhooks as wh
from core.config import Settings
from core.config_runtime import RuntimeConfig
from storage.app_config_repo import AppConfigRow
from storage.repo_registry_repo import RepoRegistryRow

_SECRET = "test-webhook-secret"


def _managed(repo_id="JA4M", *, remote="https://github.com/you/JA4M.git", branch="main"):
    return RepoRegistryRow(
        repo_id=repo_id, source_type="managed", repo_path=f"/managed/{repo_id}",
        remote_url=remote, branch=branch, last_commit_sha="old", watch_enabled=True,
        last_synced_at=None, last_change_at=None, last_error=None,
        created_at=datetime.now(UTC), updated_at=None,
    )


# ---------------------------------------------------------------------------
# normalize_repo_url / match
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "a,b",
    [
        ("https://github.com/you/JA4M.git", "git@github.com:you/JA4M.git"),
        ("https://github.com/you/JA4M", "https://github.com/you/JA4M.git/"),
        ("ssh://git@github.com/you/JA4M.git", "https://github.com/you/JA4M"),
        ("https://x-access-token:tok@github.com/you/JA4M.git", "https://github.com/you/JA4M"),
    ],
)
def test_normalize_repo_url_canonicalizes_equivalent_forms(a: str, b: str) -> None:
    assert wh.normalize_repo_url(a) == wh.normalize_repo_url(b)


def test_match_managed_across_url_forms() -> None:
    repos = [_managed(remote="https://github.com/you/JA4M.git")]
    # push payload carries the ssh form + full_name shorthand
    m = wh.match_managed_repo(repos, ["git@github.com:you/JA4M.git", "you/JA4M"])
    assert m is not None and m.repo_id == "JA4M"


def test_match_ignores_local_and_unknown() -> None:
    local = RepoRegistryRow(
        repo_id="loc", source_type="local", repo_path="/repos/loc", remote_url=None,
        branch=None, last_commit_sha=None, watch_enabled=True, last_synced_at=None,
        last_change_at=None, last_error=None, created_at=datetime.now(UTC), updated_at=None,
    )
    assert wh.match_managed_repo([local], ["https://github.com/you/loc.git"]) is None
    assert wh.match_managed_repo([_managed()], ["https://github.com/other/repo.git"]) is None


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------
def _gh_sig(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_github_signature() -> None:
    body = b'{"a":1}'
    good = Headers({"x-hub-signature-256": _gh_sig(body)})
    bad = Headers({"x-hub-signature-256": "sha256=deadbeef"})
    missing = Headers({})
    assert wh.verify_signature("github", good, body, _SECRET) is True
    assert wh.verify_signature("github", bad, body, _SECRET) is False
    assert wh.verify_signature("github", missing, body, _SECRET) is False


def test_verify_gitlab_token() -> None:
    body = b"{}"
    assert wh.verify_signature("gitlab", Headers({"x-gitlab-token": _SECRET}), body, _SECRET) is True
    assert wh.verify_signature("gitlab", Headers({"x-gitlab-token": "nope"}), body, _SECRET) is False


# ---------------------------------------------------------------------------
# parse_push
# ---------------------------------------------------------------------------
def test_parse_github_push() -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/you/JA4M.git", "full_name": "you/JA4M"},
    }
    info = wh.parse_push("github", payload)
    assert info.branch == "main"
    assert "https://github.com/you/JA4M.git" in info.candidate_urls
    assert "you/JA4M" in info.candidate_urls


def test_parse_gitlab_push() -> None:
    payload = {
        "ref": "refs/heads/develop",
        "project": {"git_http_url": "https://gitlab.com/org/proj.git"},
    }
    info = wh.parse_push("gitlab", payload)
    assert info.branch == "develop"
    assert "https://gitlab.com/org/proj.git" in info.candidate_urls


def test_parse_push_tag_ref_has_no_branch() -> None:
    info = wh.parse_push("github", {"ref": "refs/tags/v1.0", "repository": {}})
    assert info.branch is None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
class _ConfigRepo:
    def __init__(self, secret: str | None) -> None:
        self._secret = secret

    async def get(self):
        if self._secret is None:
            return None
        return AppConfigRow(id=1, mcp_api_key=None, openai_api_key=None,
                            embedding_mode="openai", embedding_model=None,
                            onboarding_completed=False, webhook_secret=self._secret)


class _Registry:
    def __init__(self, rows):
        self.rows = rows

    async def list_all(self):
        return self.rows


def _make_app(secret: str | None, rows) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.runtime_config = RuntimeConfig(_ConfigRepo(secret), Settings())  # type: ignore[arg-type]
        await app.state.runtime_config.refresh()
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(wh.router)
    app.dependency_overrides[get_app_state] = lambda: object()
    app.dependency_overrides[get_runtime_config] = lambda: app.state.runtime_config
    app.dependency_overrides[get_repo_registry] = lambda: _Registry(rows)
    return app


def _push_body(branch="main", url="https://github.com/you/JA4M.git") -> bytes:
    return json.dumps({
        "ref": f"refs/heads/{branch}",
        "repository": {"clone_url": url, "full_name": "you/JA4M"},
    }).encode()


def test_webhook_no_secret_is_503() -> None:
    app = _make_app(None, [_managed()])
    with TestClient(app) as client:
        body = _push_body()
        r = client.post("/webhooks/git", content=body,
                        headers={"x-github-event": "push", "x-hub-signature-256": _gh_sig(body)})
    assert r.status_code == 503


def test_webhook_bad_signature_is_401() -> None:
    app = _make_app(_SECRET, [_managed()])
    with TestClient(app) as client:
        r = client.post("/webhooks/git", content=_push_body(),
                        headers={"x-github-event": "push", "x-hub-signature-256": "sha256=bad"})
    assert r.status_code == 401


def test_webhook_ping_acks() -> None:
    app = _make_app(_SECRET, [_managed()])
    with TestClient(app) as client:
        body = b"{}"
        r = client.post("/webhooks/git", content=body,
                        headers={"x-github-event": "ping", "x-hub-signature-256": _gh_sig(body)})
    assert r.status_code == 200
    assert r.json()["detail"] == "pong"


def test_webhook_push_triggers_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def fake_sync(repo, **kw):
        called.append(repo.repo_id)
        return SyncResult(repo.repo_id, changed=True, new_sha="new")

    monkeypatch.setattr(wh, "sync_managed_repo", fake_sync)
    app = _make_app(_SECRET, [_managed()])
    with TestClient(app) as client:
        body = _push_body(branch="main")
        r = client.post("/webhooks/git", content=body,
                        headers={"x-github-event": "push", "x-hub-signature-256": _gh_sig(body)})
    assert r.status_code == 200
    j = r.json()
    assert j["matched"] is True and j["triggered"] is True and j["repo_id"] == "JA4M"
    # background task ran the sync
    assert called == ["JA4M"]


def test_webhook_push_wrong_branch_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(wh, "sync_managed_repo",
                        lambda *a, **k: called.append("x"))  # should NOT run
    app = _make_app(_SECRET, [_managed(branch="main")])
    with TestClient(app) as client:
        body = _push_body(branch="feature")
        r = client.post("/webhooks/git", content=body,
                        headers={"x-github-event": "push", "x-hub-signature-256": _gh_sig(body)})
    assert r.status_code == 200
    assert r.json()["matched"] is True and r.json()["triggered"] is False
    assert called == []


def test_webhook_push_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(_SECRET, [_managed(remote="https://github.com/you/OTHER.git")])
    with TestClient(app) as client:
        body = _push_body(url="https://github.com/you/JA4M.git")
        r = client.post("/webhooks/git", content=body,
                        headers={"x-github-event": "push", "x-hub-signature-256": _gh_sig(body)})
    assert r.status_code == 200
    assert r.json()["matched"] is False
