"""Managed-repo lifecycle: clone-by-URL and poll-driven sync.

A *managed* repo is a git URL Memory-CL clones into a writable workspace
(`/managed/<repo_id>`) and keeps in sync with its tracked branch. These
two operations are pure orchestration over an injected `GitRunner`, the
repo registry, and an `ingest` callable — no FastAPI, no real git in tests.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from apps.api.freshness.git import GitError, GitRunner
from storage.repo_registry_repo import RepoRegistryRepository, RepoRegistryRow

# Ingest a checked-out repo. Bound to `run_ingest(state, settings, runtime,
# ...)` by the lifespan; faked in tests. Returns the ingest outcome (unused
# by the sync logic, which only cares that it didn't raise).
IngestFn = Callable[..., Awaitable[Any]]

_REPO_ID_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")


def derive_repo_id(remote_url: str) -> str:
    """Best-effort repo_id from a git URL: last path segment, sans `.git`.

    Handles `https://host/org/repo.git` and `git@host:org/repo.git`.
    Sanitizes to the registry's allowed charset and caps at 128 chars.
    """
    url = remote_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    # Last segment after either "/" (https) or ":" (scp-like git@host:org/repo).
    seg = url.rsplit("/", 1)[-1]
    seg = seg.rsplit(":", 1)[-1]
    seg = _REPO_ID_SANITIZE.sub("-", seg).strip("-")
    if not seg:
        raise ValueError(f"could not derive a repo_id from {remote_url!r}")
    return seg[:128]


def build_clone_url(remote_url: str, github_token: str | None) -> str:
    """Inject a token into an https URL for private-repo access, else return
    the URL unchanged. The CLEAN url (no token) is what gets persisted in the
    registry; only the clone/origin carries the credential."""
    if github_token and remote_url.startswith("https://"):
        return f"https://x-access-token:{github_token}@{remote_url[len('https://'):]}"
    return remote_url


@dataclass(frozen=True)
class SyncResult:
    repo_id: str
    changed: bool
    new_sha: str | None = None
    error: str | None = None


async def add_managed_repo(
    *,
    registry: RepoRegistryRepository,
    git: GitRunner,
    ingest: IngestFn,
    remote_url: str,
    branch: str | None,
    repo_id: str | None,
    managed_root: str,
    github_token: str | None = None,
) -> SyncResult:
    """Clone (or update) a git URL into the managed workspace, register it,
    and run the initial ingest. Idempotent on repo_id: a re-add updates the
    existing clone to the branch tip.
    """
    rid = (repo_id or derive_repo_id(remote_url)).strip()
    if not rid:
        raise ValueError("repo_id resolved empty")
    # ASYNC240: single-shot blocking fs calls during a rare, user-initiated
    # add — same pragmatic exception the ingest endpoint takes for stat().
    os.makedirs(managed_root, exist_ok=True)
    dest = os.path.join(managed_root, rid)

    if os.path.isdir(os.path.join(dest, ".git")):  # noqa: ASYNC240
        # Already cloned — bring it to the branch tip rather than re-cloning.
        tracked = branch or await git.current_branch(dest)
        sha = await git.update_to_remote(dest, tracked)
    else:
        await git.clone(build_clone_url(remote_url, github_token), dest, branch)
        tracked = branch or await git.current_branch(dest)
        sha = await git.head_sha(dest)

    await registry.add_managed(rid, dest, remote_url, tracked, sha)
    await ingest(repo_id=rid, repo_path=dest, commit_sha=sha)
    await registry.mark_synced(rid, sha)
    return SyncResult(repo_id=rid, changed=True, new_sha=sha)


async def sync_managed_repo(
    repo: RepoRegistryRow,
    *,
    registry: RepoRegistryRepository,
    git: GitRunner,
    ingest: IngestFn,
) -> SyncResult:
    """Re-ingest a managed repo iff its tracked branch tip moved.

    Cheap `ls-remote` check first; only on a real change do we fetch, hard-
    reset the working tree, and reingest. All git/ingest failures are caught
    and recorded as `last_error` — a single bad repo must never crash the
    poll loop.
    """
    branch = repo.branch
    try:
        if not branch:
            branch = await git.current_branch(repo.repo_path)
        remote = await git.remote_sha(repo.repo_path, branch)
    except GitError as exc:
        await registry.mark_error(repo.repo_id, f"poll check failed: {exc}")
        return SyncResult(repo.repo_id, changed=False, error=str(exc))

    if remote == repo.last_commit_sha:
        return SyncResult(repo.repo_id, changed=False)

    await registry.mark_change(repo.repo_id)
    try:
        new_sha = await git.update_to_remote(repo.repo_path, branch)
        await ingest(
            repo_id=repo.repo_id, repo_path=repo.repo_path, commit_sha=new_sha
        )
    except Exception as exc:
        await registry.mark_error(repo.repo_id, str(exc))
        return SyncResult(repo.repo_id, changed=True, error=str(exc))

    await registry.mark_synced(repo.repo_id, new_sha)
    return SyncResult(repo.repo_id, changed=True, new_sha=new_sha)


__all__ = [
    "IngestFn",
    "SyncResult",
    "add_managed_repo",
    "build_clone_url",
    "derive_repo_id",
    "sync_managed_repo",
]
