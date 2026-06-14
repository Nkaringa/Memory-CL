"""Managed-repo add/sync + poller logic — fully faked (no real git/network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.api.freshness.git import GitError
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.managed import (
    add_managed_repo,
    build_clone_url,
    derive_repo_id,
    sync_managed_repo,
)
from apps.api.freshness.poller import FreshnessPoller
from storage.repo_registry_repo import RepoRegistryRow


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeGit:
    def __init__(
        self,
        *,
        branch: str = "main",
        head: str = "sha_head",
        remote: str = "sha_remote",
        updated: str = "sha_updated",
        fail_on: str | None = None,
    ) -> None:
        self._branch, self._head, self._remote, self._updated = (
            branch, head, remote, updated,
        )
        self._fail_on = fail_on
        self.clones: list[tuple[str, str, str | None]] = []
        self.updated_calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if self._fail_on == op:
            raise GitError(f"boom:{op}")

    async def clone(self, clone_url: str, dest: str, branch: str | None) -> None:
        self._maybe_fail("clone")
        self.clones.append((clone_url, dest, branch))

    async def current_branch(self, repo_path: str) -> str:
        self._maybe_fail("current_branch")
        return self._branch

    async def head_sha(self, repo_path: str) -> str:
        return self._head

    async def remote_sha(self, repo_path: str, branch: str) -> str:
        self._maybe_fail("remote_sha")
        return self._remote

    async def update_to_remote(self, repo_path: str, branch: str) -> str:
        self._maybe_fail("update_to_remote")
        self.updated_calls.append(repo_path)
        return self._updated


class FakeRegistry:
    def __init__(self, rows: list[RepoRegistryRow] | None = None) -> None:
        self.rows = rows or []
        self.added: list[tuple] = []
        self.synced: list[tuple[str, str | None]] = []
        self.changes: list[str] = []
        self.errors: list[tuple[str, str]] = []

    async def list_watched(self) -> list[RepoRegistryRow]:
        return [r for r in self.rows if r.watch_enabled]

    async def add_managed(self, repo_id, repo_path, remote_url, branch, commit_sha):
        self.added.append((repo_id, repo_path, remote_url, branch, commit_sha))

    async def mark_synced(self, repo_id, commit_sha):
        self.synced.append((repo_id, commit_sha))

    async def mark_change(self, repo_id):
        self.changes.append(repo_id)

    async def mark_error(self, repo_id, message):
        self.errors.append((repo_id, message))


def _managed_row(repo_id="r1", *, branch="main", last_sha="sha_old", watch=True, path=None):
    return RepoRegistryRow(
        repo_id=repo_id, source_type="managed", repo_path=path or f"/managed/{repo_id}",
        remote_url="https://github.com/x/r1", branch=branch, last_commit_sha=last_sha,
        watch_enabled=watch, last_synced_at=None, last_change_at=None,
        last_error=None, created_at=datetime.now(UTC), updated_at=None,
    )


class _Ingests:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def __call__(self, *, repo_id, repo_path, commit_sha):
        self.calls.append({"repo_id": repo_id, "repo_path": repo_path, "commit_sha": commit_sha})
        if self._fail:
            raise RuntimeError("ingest boom")


# ---------------------------------------------------------------------------
# derive_repo_id / build_clone_url
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/you/JA4M.git", "JA4M"),
        ("https://github.com/you/JA4M", "JA4M"),
        ("git@github.com:you/my-repo.git", "my-repo"),
        ("https://gitlab.com/org/sub/Cool.Repo/", "Cool.Repo"),
    ],
)
def test_derive_repo_id(url: str, expected: str) -> None:
    assert derive_repo_id(url) == expected


def test_derive_repo_id_rejects_unusable() -> None:
    with pytest.raises(ValueError):
        derive_repo_id("https://host/.git")


def test_build_clone_url_injects_token_only_for_https() -> None:
    assert build_clone_url("https://github.com/x/y.git", "tok") == (
        "https://x-access-token:tok@github.com/x/y.git"
    )
    assert build_clone_url("https://github.com/x/y.git", None) == (
        "https://github.com/x/y.git"
    )
    # scp-style URL is left untouched (no token injection path).
    assert build_clone_url("git@github.com:x/y.git", "tok") == "git@github.com:x/y.git"


# ---------------------------------------------------------------------------
# add_managed_repo
# ---------------------------------------------------------------------------
async def test_add_managed_clones_registers_ingests(tmp_path: Path) -> None:
    git = FakeGit(branch="main", head="sha_head")
    reg = FakeRegistry()
    ingest = _Ingests()
    res = await add_managed_repo(
        registry=reg, git=git, ingest=ingest,  # type: ignore[arg-type]
        remote_url="https://github.com/you/JA4M.git", branch=None, repo_id=None,
        managed_root=str(tmp_path), github_token=None,
    )
    assert res.repo_id == "JA4M"
    assert res.new_sha == "sha_head"
    # cloned into <root>/JA4M
    assert git.clones and git.clones[0][1].endswith("/JA4M")
    # registered as managed with the detected branch + sha
    assert reg.added == [("JA4M", str(tmp_path / "JA4M"), "https://github.com/you/JA4M.git", "main", "sha_head")]
    # initial ingest ran at that sha, then marked synced
    assert ingest.calls == [{"repo_id": "JA4M", "repo_path": str(tmp_path / "JA4M"), "commit_sha": "sha_head"}]
    assert reg.synced == [("JA4M", "sha_head")]


async def test_add_managed_uses_explicit_id_and_branch(tmp_path: Path) -> None:
    git = FakeGit(head="sha_head")
    reg = FakeRegistry()
    ingest = _Ingests()
    res = await add_managed_repo(
        registry=reg, git=git, ingest=ingest,  # type: ignore[arg-type]
        remote_url="https://github.com/you/thing.git", branch="develop", repo_id="custom",
        managed_root=str(tmp_path),
    )
    assert res.repo_id == "custom"
    assert git.clones[0][2] == "develop"  # cloned the requested branch
    assert reg.added[0][3] == "develop"


# ---------------------------------------------------------------------------
# sync_managed_repo
# ---------------------------------------------------------------------------
async def test_sync_noop_when_remote_unchanged() -> None:
    git = FakeGit(remote="sha_same")
    reg = FakeRegistry()
    ingest = _Ingests()
    repo = _managed_row(last_sha="sha_same")
    res = await sync_managed_repo(repo, registry=reg, git=git, ingest=ingest)  # type: ignore[arg-type]
    assert res.changed is False
    assert ingest.calls == []
    assert reg.synced == [] and reg.changes == []


async def test_sync_reingests_when_remote_moved() -> None:
    git = FakeGit(remote="sha_new", updated="sha_new")
    reg = FakeRegistry()
    ingest = _Ingests()
    repo = _managed_row(last_sha="sha_old")
    res = await sync_managed_repo(repo, registry=reg, git=git, ingest=ingest)  # type: ignore[arg-type]
    assert res.changed is True and res.new_sha == "sha_new"
    assert reg.changes == ["r1"]
    assert git.updated_calls == ["/managed/r1"]
    assert ingest.calls[0]["commit_sha"] == "sha_new"
    assert reg.synced == [("r1", "sha_new")]


async def test_sync_records_error_on_fetch_failure() -> None:
    git = FakeGit(fail_on="remote_sha")
    reg = FakeRegistry()
    ingest = _Ingests()
    res = await sync_managed_repo(_managed_row(), registry=reg, git=git, ingest=ingest)  # type: ignore[arg-type]
    assert res.changed is False and res.error
    assert reg.errors and reg.errors[0][0] == "r1"
    assert reg.synced == []


async def test_sync_records_error_when_ingest_fails() -> None:
    git = FakeGit(remote="sha_new", updated="sha_new")
    reg = FakeRegistry()
    ingest = _Ingests(fail=True)
    res = await sync_managed_repo(_managed_row(), registry=reg, git=git, ingest=ingest)  # type: ignore[arg-type]
    assert res.changed is True and res.error
    assert reg.changes == ["r1"]  # change was recorded before the failed ingest
    assert reg.synced == []  # never marked synced
    assert reg.errors and reg.errors[0][0] == "r1"


# ---------------------------------------------------------------------------
# FreshnessPoller
# ---------------------------------------------------------------------------
async def test_poller_only_touches_managed_watched_repos() -> None:
    local = RepoRegistryRow(
        repo_id="loc", source_type="local", repo_path="/repos/loc", remote_url=None,
        branch=None, last_commit_sha="x", watch_enabled=True, last_synced_at=None,
        last_change_at=None, last_error=None, created_at=datetime.now(UTC), updated_at=None,
    )
    paused = _managed_row("paused", watch=False)
    managed = _managed_row("m1", last_sha="old")
    reg = FakeRegistry([local, paused, managed])
    git = FakeGit(remote="new", updated="new")
    ingest = _Ingests()
    poller = FreshnessPoller(
        registry=reg, git=git, ingest=ingest, locks=RepoLocks(),  # type: ignore[arg-type]
        interval_seconds=60,
    )
    results = await poller.poll_all()
    # Only the managed + watch-enabled repo was synced.
    assert [r.repo_id for r in results] == ["m1"]
    assert ingest.calls[0]["repo_id"] == "m1"


async def test_poller_skips_under_safe_mode() -> None:
    reg = FakeRegistry([_managed_row("m1", last_sha="old")])
    git = FakeGit(remote="new")
    ingest = _Ingests()
    poller = FreshnessPoller(
        registry=reg, git=git, ingest=ingest, locks=RepoLocks(),  # type: ignore[arg-type]
        interval_seconds=60, safe_mode_active=lambda: True,
    )
    assert await poller.poll_all() == []
    assert ingest.calls == []


async def test_poller_isolates_one_repos_failure() -> None:
    bad = _managed_row("bad", last_sha="old")
    good = _managed_row("good", last_sha="old")
    reg = FakeRegistry([bad, good])

    class _PartlyFailingGit(FakeGit):
        async def remote_sha(self, repo_path: str, branch: str) -> str:
            if repo_path == "/managed/bad":
                raise GitError("bad repo")
            return "new"

    git = _PartlyFailingGit(updated="new")
    ingest = _Ingests()
    poller = FreshnessPoller(
        registry=reg, git=git, ingest=ingest, locks=RepoLocks(),  # type: ignore[arg-type]
        interval_seconds=60,
    )
    results = await poller.poll_all()
    by_id = {r.repo_id: r for r in results}
    assert by_id["bad"].error  # captured, not raised
    assert by_id["good"].changed is True  # the loop kept going
    assert ingest.calls and ingest.calls[0]["repo_id"] == "good"
