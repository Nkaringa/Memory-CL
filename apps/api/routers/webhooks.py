"""Inbound git-push webhooks — instant freshness for managed repos.

`POST /webhooks/git` accepts GitHub + GitLab push events. It's authed by
the provider's **signature** (not the API key — the endpoint is public-
facing): GitHub `X-Hub-Signature-256` (HMAC-SHA256 of the raw body) and
GitLab `X-Gitlab-Token`, both constant-time compared against the configured
webhook secret. With no secret set the endpoint rejects everything — it
never runs open.

On a verified push it maps the repo to a registered MANAGED repo by remote
URL, checks the pushed branch matches the tracked branch, and schedules the
SAME `sync_managed_repo` the poller runs (fetch + reingest) as a background
task — so the response acks fast. The signature / parse / match logic are
pure functions so they're unit-tested without HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from apps.api.dependencies import AppStateDep, RepoRegistryDep, RuntimeConfigDep
from apps.api.freshness.git import SubprocessGitRunner
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.managed import sync_managed_repo
from apps.api.routers.freshness import _make_ingest
from core.logging import get_logger
from storage.repo_registry_repo import RepoRegistryRepository, RepoRegistryRow

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_log = get_logger(__name__)

# Fallback locks if the freshness background tasks aren't running (e.g.
# FRESHNESS_ENABLED=false) — the webhook can still trigger a one-off sync.
_FALLBACK_LOCKS = RepoLocks()


class WebhookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True
    event: str | None = None
    matched: bool = False
    repo_id: str | None = None
    triggered: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class PushInfo:
    """The bits of a push event freshness needs."""

    candidate_urls: list[str]
    branch: str | None


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly)
# ---------------------------------------------------------------------------
def detect_provider_event(headers: object) -> tuple[str | None, str | None]:
    """Return (provider, event) from the request headers, or (None, None).

    `headers` is anything with case-insensitive `.get` (Starlette Headers).
    """
    get = headers.get  # type: ignore[attr-defined]
    if get("x-github-event") is not None:
        return "github", get("x-github-event")
    if get("x-gitlab-event") is not None:
        # GitLab events are like "Push Hook" / "Tag Push Hook".
        ev = get("x-gitlab-event") or ""
        return "gitlab", "push" if ev.strip().lower().startswith("push") else ev
    return None, None


def verify_signature(
    provider: str, headers: object, body: bytes, secret: str
) -> bool:
    """Constant-time-verify the provider signature against `secret`."""
    get = headers.get  # type: ignore[attr-defined]
    if provider == "github":
        sent = get("x-hub-signature-256")
        if not sent:
            return False
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(f"sha256={digest}", sent)
    if provider == "gitlab":
        sent = get("x-gitlab-token") or ""
        return hmac.compare_digest(sent, secret)
    return False


def _branch_from_ref(ref: str | None) -> str | None:
    """'refs/heads/main' -> 'main'; tag refs / None -> None."""
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return None


def parse_push(provider: str, payload: dict[str, Any]) -> PushInfo:
    """Extract candidate clone URLs + the pushed branch from a push payload."""
    urls: list[str] = []
    if provider == "github":
        repo = payload.get("repository") or {}
        for k in ("clone_url", "ssh_url", "git_url", "html_url"):
            v = repo.get(k)
            if isinstance(v, str) and v:
                urls.append(v)
        full = repo.get("full_name")
        if isinstance(full, str) and full:
            urls.append(full)
        branch = _branch_from_ref(payload.get("ref"))
    else:  # gitlab
        proj = payload.get("project") or {}
        for k in ("git_http_url", "git_ssh_url", "http_url", "ssh_url"):
            v = proj.get(k)
            if isinstance(v, str) and v:
                urls.append(v)
        path = proj.get("path_with_namespace") or payload.get("project", {}).get("path")
        if isinstance(path, str) and path:
            urls.append(path)
        branch = _branch_from_ref(payload.get("ref"))
    return PushInfo(candidate_urls=urls, branch=branch)


def normalize_repo_url(url: str) -> str:
    """Canonicalize a git URL to 'host/org/repo' for cross-form matching.

    Handles https://, ssh://, git@host:org/repo, embedded user creds, a
    trailing .git, and 'org/repo' shorthand (kept as-is, host-less)."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^ssh://", "", u)
    u = re.sub(r"^git@", "", u)
    u = re.sub(r"^[^@/]+@", "", u)  # strip user[:pass]@ credentials
    u = u.replace(":", "/")  # host:org/repo -> host/org/repo
    u = re.sub(r"/+", "/", u)
    u = u.rstrip("/")
    # Strip .git LAST so a trailing slash after it (…/repo.git/) is handled.
    return re.sub(r"\.git$", "", u)


def match_managed_repo(
    repos: Sequence[RepoRegistryRow], candidate_urls: Sequence[str]
) -> RepoRegistryRow | None:
    """Find the managed repo whose remote_url matches any candidate URL.

    Matches on the canonical 'host/org/repo' tail so https vs ssh vs the
    `full_name` shorthand all resolve to the same repo."""
    wanted = {normalize_repo_url(u) for u in candidate_urls if u}
    for r in repos:
        if r.source_type != "managed" or not r.remote_url:
            continue
        norm = normalize_repo_url(r.remote_url)
        if norm in wanted or any(c.endswith("/" + norm) or norm.endswith("/" + c) for c in wanted):
            return r
    return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
async def _run_sync(
    repo: RepoRegistryRow,
    registry: RepoRegistryRepository,
    git: SubprocessGitRunner,
    ingest: object,
    locks: RepoLocks,
) -> None:
    async with locks.get(repo.repo_id):
        res = await sync_managed_repo(
            repo, registry=registry, git=git, ingest=ingest  # type: ignore[arg-type]
        )
    _log.info(
        "webhook_sync_done",
        repo_id=repo.repo_id, changed=res.changed, error=res.error,
    )


@router.post("/git", response_model=WebhookResponse)
async def git_webhook(
    request: Request,
    background: BackgroundTasks,
    state: AppStateDep,
    runtime: RuntimeConfigDep,
    registry: RepoRegistryDep,
) -> WebhookResponse:
    secret = runtime.webhook_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook not configured — generate a secret in Settings",
        )
    raw = await request.body()
    provider, event = detect_provider_event(request.headers)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unrecognized webhook (no GitHub/GitLab event header)",
        )
    if not verify_signature(provider, request.headers, raw, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature"
        )
    # GitHub fires a 'ping' when the webhook is first added — ack it so the
    # setup test goes green.
    if event == "ping":
        return WebhookResponse(event="ping", detail="pong")
    if event != "push":
        return WebhookResponse(event=event, detail="ignored (not a push)")

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed JSON body"
        ) from exc

    info = parse_push(provider, payload)
    repo = match_managed_repo(await registry.list_all(), info.candidate_urls)
    if repo is None:
        return WebhookResponse(event="push", matched=False, detail="no managed repo matches")
    if repo.branch and info.branch and info.branch != repo.branch:
        return WebhookResponse(
            event="push", matched=True, repo_id=repo.repo_id,
            detail=f"push to {info.branch}, tracking {repo.branch} — skipped",
        )

    # Schedule the same sync the poller runs; ack fast.
    git = SubprocessGitRunner()
    ingest = _make_ingest(state, runtime)
    locks = getattr(request.app.state, "repo_locks", None) or _FALLBACK_LOCKS
    background.add_task(_run_sync, repo, registry, git, ingest, locks)
    _log.info("webhook_push_accepted", provider=provider, repo_id=repo.repo_id)
    return WebhookResponse(event="push", matched=True, repo_id=repo.repo_id, triggered=True)


__all__ = ["router"]
