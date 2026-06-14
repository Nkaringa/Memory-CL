"""Filesystem-watcher logic — canned change stream, no real fs events."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.api.freshness.git import GitError
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.watcher import FreshnessWatcher, change_is_relevant
from storage.repo_registry_repo import RepoRegistryRow


class FakeRegistry:
    def __init__(self, rows: list[RepoRegistryRow]) -> None:
        self.rows = {r.repo_id: r for r in rows}
        self.synced: list[tuple[str, str]] = []
        self.changes: list[str] = []
        self.errors: list[tuple[str, str]] = []

    async def list_watched(self) -> list[RepoRegistryRow]:
        return [r for r in self.rows.values() if r.watch_enabled]

    async def get(self, repo_id: str) -> RepoRegistryRow | None:
        return self.rows.get(repo_id)

    async def mark_change(self, repo_id):
        self.changes.append(repo_id)

    async def mark_synced(self, repo_id, commit_sha):
        self.synced.append((repo_id, commit_sha))

    async def mark_error(self, repo_id, message):
        self.errors.append((repo_id, message))


class _Ingests:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def __call__(self, *, repo_id, repo_path, commit_sha):
        self.calls.append({"repo_id": repo_id, "repo_path": repo_path, "commit_sha": commit_sha})
        if self._fail:
            raise RuntimeError("ingest boom")


class _Git:
    def __init__(self, *, sha="sha_head", fail=False):
        self._sha, self._fail = sha, fail

    async def head_sha(self, repo_path: str) -> str:
        if self._fail:
            raise GitError("no git")
        return self._sha


def _local(repo_id, path, *, watch=True):
    return RepoRegistryRow(
        repo_id=repo_id, source_type="local", repo_path=path, remote_url=None,
        branch=None, last_commit_sha=None, watch_enabled=watch, last_synced_at=None,
        last_change_at=None, last_error=None, created_at=datetime.now(UTC), updated_at=None,
    )


def _watcher(registry, ingest, *, git=None, safe_mode=None):
    return FreshnessWatcher(
        registry=registry, ingest=ingest, locks=RepoLocks(), watch_root="/repos",
        awatch_factory=lambda *a, **k: iter(()),  # unused in handle_batch tests
        git=git, safe_mode_active=safe_mode,
    )


# ---------------------------------------------------------------------------
def test_change_is_relevant_ignores_vendor_and_vcs() -> None:
    assert change_is_relevant("/repos/a/src/main.py") is True
    assert change_is_relevant("/repos/a/.git/index") is False
    assert change_is_relevant("/repos/a/node_modules/x/y.js") is False
    assert change_is_relevant("/repos/a/__pycache__/m.pyc") is False


async def test_handle_batch_maps_path_to_repo_and_reingests() -> None:
    reg = FakeRegistry([_local("projA", "/repos/projA")])
    ingest = _Ingests()
    w = _watcher(reg, ingest, git=_Git(sha="abc123"))
    done = await w.handle_batch({"/repos/projA/src/main.py"})
    assert done == ["projA"]
    assert ingest.calls == [{"repo_id": "projA", "repo_path": "/repos/projA", "commit_sha": "abc123"}]
    assert reg.synced == [("projA", "abc123")]
    assert reg.changes == ["projA"]


async def test_handle_batch_longest_prefix_wins_for_nested_repos() -> None:
    reg = FakeRegistry([_local("outer", "/repos/outer"), _local("inner", "/repos/outer/inner")])
    ingest = _Ingests()
    w = _watcher(reg, ingest)
    done = await w.handle_batch({"/repos/outer/inner/file.py"})
    # The nested repo is the more specific match.
    assert done == ["inner"]


async def test_handle_batch_skips_paused_repo() -> None:
    reg = FakeRegistry([_local("p", "/repos/p", watch=False)])
    ingest = _Ingests()
    w = _watcher(reg, ingest)
    # Paused repos aren't in list_watched, so nothing maps.
    assert await w.handle_batch({"/repos/p/x.py"}) == []
    assert ingest.calls == []


async def test_handle_batch_skips_under_safe_mode() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests()
    w = _watcher(reg, ingest, safe_mode=lambda: True)
    assert await w.handle_batch({"/repos/a/x.py"}) == []
    assert ingest.calls == []


async def test_handle_batch_ignores_vendor_only_batch() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests()
    w = _watcher(reg, ingest)
    assert await w.handle_batch({"/repos/a/.git/index", "/repos/a/node_modules/p.js"}) == []
    assert ingest.calls == []


async def test_commit_sha_falls_back_to_auto_without_git() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests()
    w = _watcher(reg, ingest, git=None)  # no git runner
    await w.handle_batch({"/repos/a/x.py"})
    assert ingest.calls[0]["commit_sha"] == "auto"


async def test_commit_sha_auto_when_git_fails() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests()
    w = _watcher(reg, ingest, git=_Git(fail=True))
    await w.handle_batch({"/repos/a/x.py"})
    assert ingest.calls[0]["commit_sha"] == "auto"


async def test_reingest_failure_marks_error_not_synced() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests(fail=True)
    w = _watcher(reg, ingest, git=_Git(sha="s"))
    done = await w.handle_batch({"/repos/a/x.py"})
    assert done == []
    assert reg.errors and reg.errors[0][0] == "a"
    assert reg.synced == []


async def test_run_consumes_awatch_batches() -> None:
    reg = FakeRegistry([_local("a", "/repos/a")])
    ingest = _Ingests()

    async def fake_awatch(*_a, **_k):
        yield {(1, "/repos/a/one.py")}
        yield {(1, "/repos/a/two.py")}

    w = FreshnessWatcher(
        registry=reg, ingest=ingest, locks=RepoLocks(), watch_root="/repos",
        awatch_factory=fake_awatch,
    )
    await w.run()
    # Both batches were handled -> two reingests of repo 'a'.
    assert [c["repo_path"] for c in ingest.calls] == ["/repos/a", "/repos/a"]
