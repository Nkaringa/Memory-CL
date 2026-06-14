"""Git operations for managed repos — a thin, injectable subprocess wrapper.

Managed repos are git URLs Memory-CL clones into a writable workspace and
keeps pulled. This module is the ONLY place that shells out to `git`; the
poller/add logic depend on the `GitRunner` Protocol so tests substitute a
fake (no real network, no real clones).

Security: `GIT_TERMINAL_PROMPT=0` makes a missing credential fail fast
instead of hanging on an interactive prompt. Auth for private repos is
carried in the clone URL by the caller (`managed.py`), never by this
runner — the runner just executes git.
"""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, runtime_checkable


class GitError(RuntimeError):
    """A git subprocess failed (non-zero exit, timeout, or not runnable)."""


@runtime_checkable
class GitRunner(Protocol):
    """The git surface the freshness code needs. Faked in tests."""

    async def clone(self, clone_url: str, dest: str, branch: str | None) -> None: ...

    async def current_branch(self, repo_path: str) -> str: ...

    async def head_sha(self, repo_path: str) -> str: ...

    async def remote_sha(self, repo_path: str, branch: str) -> str: ...

    async def update_to_remote(self, repo_path: str, branch: str) -> str: ...


class SubprocessGitRunner:
    """Concrete `GitRunner` over `git` via asyncio subprocesses."""

    name: str = "subprocess_git"

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    async def _run(self, args: list[str], *, cwd: str | None = None) -> str:
        env = dict(os.environ)
        # Never block on an interactive credential/known-hosts prompt — a
        # missing token must fail fast, not hang the poll loop.
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:  # git binary missing, etc.
            raise GitError(f"git could not be started: {exc}") from exc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError as exc:
            proc.kill()
            raise GitError(f"git {args[0]} timed out after {self._timeout}s") from exc
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip()[:500]
            raise GitError(f"git {args[0]} failed (exit {proc.returncode}): {detail}")
        return out.decode(errors="replace").strip()

    async def clone(self, clone_url: str, dest: str, branch: str | None) -> None:
        # --depth 1: we only need the working tree to walk + parse; full
        # history would waste disk for large repos.
        args = ["clone", "--depth", "1"]
        if branch:
            args += ["--branch", branch]
        args += [clone_url, dest]
        await self._run(args)

    async def current_branch(self, repo_path: str) -> str:
        return await self._run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)

    async def head_sha(self, repo_path: str) -> str:
        return await self._run(["rev-parse", "HEAD"], cwd=repo_path)

    async def remote_sha(self, repo_path: str, branch: str) -> str:
        """Latest sha of `origin/<branch>` WITHOUT touching the working tree.

        `ls-remote` is a lightweight network call — the poll loop uses it to
        decide whether anything changed before doing the heavier fetch.
        """
        out = await self._run(["ls-remote", "origin", branch], cwd=repo_path)
        if not out:
            raise GitError(f"branch {branch!r} not found on origin")
        # Output: "<sha>\t<ref>" (possibly multiple lines) — take the first.
        return out.split()[0]

    async def update_to_remote(self, repo_path: str, branch: str) -> str:
        """Fetch `origin/<branch>` and hard-reset the working tree to it.

        Returns the new HEAD sha. `reset --hard FETCH_HEAD` keeps the
        shallow clone shallow and discards any local drift, so the working
        tree exactly matches the remote tip the engine will walk.
        """
        await self._run(
            ["fetch", "--depth", "1", "--quiet", "origin", branch], cwd=repo_path
        )
        await self._run(["reset", "--hard", "FETCH_HEAD"], cwd=repo_path)
        return await self.head_sha(repo_path)


__all__ = ["GitError", "GitRunner", "SubprocessGitRunner"]
