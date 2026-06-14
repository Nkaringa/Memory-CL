"""Memory-CL CLI — `memcl` console script (v2).

Human-first by default (rich tables / colors / spinners, auto-degrading
on NO_COLOR and non-TTY), `--json` on every command for pipelines
(canonical JSON: sorted keys, compact separators — v1's contract).

Inference over flags: `memcl ingest` infers repo-id from the directory
basename and commit-sha from git. Errors teach instead of tracebacking.
Deprecated v1 spellings (`query`, `graph`, bare `snapshot`, `replay`)
keep working as real subcommands that delegate to their v2 successors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.cli import render
from apps.cli.config import (
    INGEST_DEFAULT_TIMEOUT,
    CliSettings,
    ConfigError,
    config_path,
    resolve_settings,
    write_config_file,
)
from apps.cli.render import UI
from sdk import AsyncMemoryClient, IngestResult, MemoryClientError


class CliFailure(Exception):
    """Expected, user-facing failure — rendered as one line, exit 1."""


# ---------------------------------------------------------------------------
# JSON emit (v1 canonical contract, preserved verbatim for --json mode)
# ---------------------------------------------------------------------------
def _emit(payload: Any) -> None:
    """Stable JSON to stdout (sorted keys, compact separators)."""
    sys.stdout.write(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
        ensure_ascii=False,
    ))
    sys.stdout.write("\n")


def _emit_error(payload: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ))
    sys.stderr.write("\n")


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------
def infer_repo_id(path: str) -> str:
    """Repo id = directory basename of the (resolved) path."""
    return Path(path).expanduser().resolve().name or "repo"


def infer_commit_sha(path: str) -> str:
    """`git rev-parse HEAD` in `path`, falling back to "manual"."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(path).expanduser()), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "manual"
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else "manual"


def resolve_server_path(path: str, override: str | None) -> tuple[str, bool]:
    """Map a CLI path onto the API container's filesystem.

    Returns (server_path, mapped) — `mapped` is True when a local-looking
    path was rewritten to /repos/<basename> and the user should be told
    about the server-path model.
    """
    if override:
        return override, False
    posix = path.replace("\\", "/")
    if posix == "/repos" or posix.startswith("/repos/"):
        return path, False
    return f"/repos/{infer_repo_id(path)}", True


def _explain_server_path(
    ui: UI, local_path: str, server_path: str, base_url: str,
    ssh_user: str = "memcl",
) -> None:
    host = urlparse(base_url).hostname or "<server-host>"
    local = Path(local_path).expanduser().resolve()
    repo_name = local.name or "repo"
    ui.note("The Memory-CL API ingests from ITS OWN filesystem, not this machine.")
    ui.note(f"Mapped local path {local} -> server path {server_path}.")
    ui.note("If the code is not on the server yet, sync it first:")
    ui.note(
        f"  rsync -a --delete {local}/ "
        f"{ssh_user}@{host}:~/repos/{repo_name}/"
    )
    ui.note("(adjust the target if ~/repos is mounted from another host dir)")
    ui.note("Then re-run. Use --server-path to choose a different container path.")


async def _resolve_repo(client: AsyncMemoryClient, explicit: str | None) -> str:
    """Use the explicit repo, or the sole ingested repo, or fail helpfully."""
    if explicit:
        return explicit
    repos = (await client.get_repos()).repos
    if len(repos) == 1:
        return repos[0].repo_id
    if not repos:
        raise CliFailure(
            "no repositories ingested yet — run: memcl ingest <path>"
        )
    raise CliFailure(
        "several repos are ingested — pick one with -r/--repo. Ingested: "
        + ", ".join(r.repo_id for r in repos)
    )


def _fmt_elapsed(seconds: float) -> str:
    whole = int(seconds)
    if whole >= 60:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole}s"


async def _tool_json(
    client: AsyncMemoryClient, tool: str, payload: dict[str, Any],
) -> int:
    """--json path for MCP-backed commands: emit the tool's raw data."""
    res = await client.run_mcp_tool(tool=tool, payload=payload)
    if res.status != "success":
        raise MemoryClientError(
            status_code=200, body=res.error or res.error_code,
            url=f"/mcp/tools/{tool}",
        )
    _emit(res.data)
    return 0


# ---------------------------------------------------------------------------
# Command handlers — signature: (client, args, ui, settings) -> exit code
# ---------------------------------------------------------------------------
async def _cmd_ingest(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    path: str = args.path
    repo_id: str = args.repo_id or infer_repo_id(path)
    commit_sha: str = args.commit_sha or infer_commit_sha(path)
    server_path, mapped = resolve_server_path(path, args.server_path)
    if mapped:
        _explain_server_path(
            ui, path, server_path, settings.base_url_value,
            settings.ssh_user_value,
        )

    try:
        if _json_mode(args):
            res = await client.ingest_repository(
                repo_id=repo_id, repo_path=server_path, commit_sha=commit_sha,
            )
            _emit(res.model_dump(mode="json"))
            return 0
        ui.note(
            f"Ingesting {server_path} as '{repo_id}' @ {commit_sha[:12]} …"
        )
        start = time.monotonic()
        res = await _ingest_with_progress(
            client, ui, repo_id=repo_id, server_path=server_path,
            commit_sha=commit_sha,
        )
        return render.render_ingest(ui, res, _fmt_elapsed(time.monotonic() - start))
    except httpx.TimeoutException:
        if _json_mode(args):
            _emit_error({"error": "timeout", "url": "/ingest"})
        else:
            ui.error(
                "client timed out, but the server is still ingesting — "
                "run `memcl repos` to watch progress"
            )
        return 1
    except MemoryClientError as exc:
        if exc.status_code == 400 and not _json_mode(args):
            detail = (
                exc.body.get("detail") if isinstance(exc.body, dict) else exc.body
            )
            ui.error(str(detail))
            if not mapped:
                _explain_server_path(
                    ui, path, server_path, settings.base_url_value,
                    settings.ssh_user_value,
                )
            return 1
        raise


_POLL_TIMEOUT = 10.0  # short timeout so a stalled /repos never blocks the spinner


async def _poll_unit_count(
    client: AsyncMemoryClient, repo_id: str,
) -> int | None:
    """Best-effort unit count for `repo_id` — progress signal only.

    Returns None on any failure so the caller can fall back to its last
    known value rather than treating 0 as a meaningful update.
    """
    try:
        repos_result = await asyncio.wait_for(
            client.get_repos(), timeout=_POLL_TIMEOUT
        )
        for repo in repos_result.repos:
            if repo.repo_id == repo_id:
                return repo.units
    except Exception:
        return None
    return None


async def _ingest_with_progress(
    client: AsyncMemoryClient, ui: UI, *,
    repo_id: str, server_path: str, commit_sha: str,
) -> IngestResult:
    """Run the (minutes-long) ingest request under an elapsed-time spinner.

    GET /repos is polled every ~4 s; the repo's growing unit count is the
    only progress signal the API exposes today.
    """
    task = asyncio.ensure_future(client.ingest_repository(
        repo_id=repo_id, repo_path=server_path, commit_sha=commit_sha,
    ))
    start = time.monotonic()
    units: int | None = None
    tick = 0
    with ui.err.status(f"Ingesting {repo_id} …") as status:
        while not task.done():
            done, _pending = await asyncio.wait({task}, timeout=2.0)
            if done:
                break
            tick += 1
            if tick % 2 == 0:
                count = await _poll_unit_count(client, repo_id)
                units = count if count is not None else units
            text = (
                f"Ingesting {repo_id} — "
                f"{_fmt_elapsed(time.monotonic() - start)} elapsed"
            )
            if units:
                text += f" · {units} units stored so far"
            status.update(text)
    return task.result()


async def _cmd_repos(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    res = await client.get_repos()
    if _json_mode(args):
        _emit(res.model_dump(mode="json"))
        return 0
    return render.render_repos(ui, res)


async def _cmd_freshness(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    cmd = getattr(args, "freshness_cmd", None) or "status"
    if cmd == "add":
        res = await client.add_managed_repo(
            remote_url=args.url, branch=args.branch, repo_id=args.id,
        )
        if _json_mode(args):
            _emit(res)
            return 0
        ui.out.print(
            f"[green]✓[/green] added managed repo [bold]{res['repo_id']}[/bold] "
            f"@ {(res.get('commit_sha') or '?')[:12]} — kept fresh by polling"
        )
        return 0
    if cmd in ("pause", "resume"):
        await client.set_freshness_watch(repo_id=args.repo_id, enabled=(cmd == "resume"))
        if _json_mode(args):
            _emit({"repo_id": args.repo_id, "watch_enabled": cmd == "resume"})
            return 0
        ui.out.print(f"[green]✓[/green] {cmd}d freshness for [bold]{args.repo_id}[/bold]")
        return 0
    if cmd == "sync":
        res = await client.sync_freshness(repo_id=args.repo_id)
        if _json_mode(args):
            _emit(res)
            return 0
        word = "re-ingested" if res.get("changed") else "already up to date"
        ui.out.print(f"[green]✓[/green] {args.repo_id}: {word}")
        return 0
    if cmd == "remove":
        await client.remove_freshness(repo_id=args.repo_id)
        if _json_mode(args):
            _emit({"repo_id": args.repo_id, "removed": True})
            return 0
        ui.out.print(f"[green]✓[/green] removed [bold]{args.repo_id}[/bold] from freshness")
        return 0
    # default: status table
    data = await client.get_freshness()
    if _json_mode(args):
        _emit(data)
        return 0
    return render.render_freshness(ui, data)


async def _cmd_token(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    cmd = getattr(args, "token_cmd", None) or "list"
    if cmd == "create":
        res = await client.issue_token(name=args.name)
        if _json_mode(args):
            _emit(res)
            return 0
        ui.out.print(
            f"[green]✓[/green] token [bold]{res['name']}[/bold] created — "
            "save it now, it won't be shown again:"
        )
        ui.out.print(res["token"])
        return 0
    if cmd == "revoke":
        res = await client.revoke_token(token_id=args.id)
        if _json_mode(args):
            _emit(res)
            return 0
        ui.out.print(f"[green]✓[/green] revoked token [bold]{args.id}[/bold]")
        return 0
    # default: list
    data = await client.list_tokens()
    if _json_mode(args):
        _emit(data)
        return 0
    return render.render_tokens(ui, data)


async def _cmd_search(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    payload: dict[str, Any] = {"question": args.question, "top_k": args.top_k}
    if args.repo:
        payload["repo_id"] = args.repo
    if _json_mode(args):
        return await _tool_json(client, "search_code", payload)
    res = await client.search_code(
        question=args.question, repo_id=args.repo, top_k=args.top_k,
    )
    return render.render_search(ui, res)


async def _cmd_read(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    payload: dict[str, Any] = {"reference": args.reference}
    if args.repo:
        payload["repo_id"] = args.repo
    if _json_mode(args):
        return await _tool_json(client, "read_unit", payload)
    res = await client.read_unit(reference=args.reference, repo_id=args.repo)
    return render.render_read(ui, res)


async def _cmd_explore(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    repo = await _resolve_repo(client, args.repo)
    payload: dict[str, Any] = {
        "qualified_name": args.qualified_name,
        "repo_id": repo,
        "direction": args.direction,
        "depth": args.depth,
    }
    if _json_mode(args):
        return await _tool_json(client, "explore", payload)
    res = await client.explore(
        qualified_name=args.qualified_name, repo_id=repo,
        direction=args.direction, depth=args.depth,
    )
    return render.render_explore(ui, res)


async def _cmd_symbols(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    payload: dict[str, Any] = {"query": args.query, "limit": args.limit}
    if args.repo:
        payload["repo_id"] = args.repo
    if _json_mode(args):
        return await _tool_json(client, "find_symbol", payload)
    res = await client.find_symbol(
        query=args.query, repo_id=args.repo, limit=args.limit,
    )
    return render.render_symbols(ui, res)


async def _cmd_overview(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    repo = await _resolve_repo(client, args.repo)
    if _json_mode(args):
        return await _tool_json(client, "repo_overview", {"repo_id": repo})
    res = await client.repo_overview(repo_id=repo)
    return render.render_overview(ui, res)


async def _cmd_status(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    res = await client.get_status()
    if _json_mode(args):
        _emit(res.model_dump(mode="json"))
        return 0
    return render.render_status(ui, res)


async def _cmd_doctor(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """Ordered diagnosis: config → reachability → auth → embeddings → repos."""
    checks: list[dict[str, Any]] = []

    def record(ok: bool, name: str, detail: str = "", fix: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    # 1. config
    has_file = config_path().is_file()
    explicit = (
        has_file
        or settings.base_url.source in ("flag", "env")
        or settings.api_key.source in ("flag", "env")
    )
    record(
        explicit, "config",
        f"base_url {settings.base_url_value} (from {settings.base_url.source})",
        "run: memcl config init",
    )

    # 2. server reachable
    status_res = None
    try:
        status_res = await client.get_status()
        record(
            True, "server",
            f"{status_res.service} @ {status_res.environment}, "
            f"boot {'ok' if status_res.boot_overall_ok else 'FAILED'}",
        )
    except Exception:
        record(
            False, "server", f"can't reach {settings.base_url_value}",
            "is the server up? verify the URL with `memcl config show`",
        )

    if status_res is not None:
        # 3. auth — cheapest authed call on the /mcp surface
        try:
            tool_res = await client.run_mcp_tool(tool="list_repos", payload={})
            ok = tool_res.status == "success"
            record(
                ok, "auth",
                "API key accepted" if ok else f"tool error: {tool_res.error}",
                "" if ok else "check the server logs",
            )
        except MemoryClientError as exc:
            if exc.status_code == 401:
                record(
                    False, "auth", "API key rejected",
                    "set MEMCL_API_KEY or run: memcl config init",
                )
            else:
                record(
                    False, "auth",
                    f"HTTP {exc.status_code} from /mcp/tools/list_repos",
                    "check the server logs",
                )
        except Exception:
            record(False, "auth", "request failed", "run: memcl status")

        # 4. embeddings
        record(
            status_res.embeddings_enabled, "embeddings",
            "on" if status_res.embeddings_enabled
            else "off — semantic search is degraded",
            "" if status_res.embeddings_enabled
            else "set OPENAI_API_KEY on the server, then: memcl reembed <repo>",
        )

        # 5. repos present
        try:
            repos = (await client.get_repos()).repos
            names = ", ".join(r.repo_id for r in repos[:5])
            record(
                bool(repos), "repos",
                f"{len(repos)} ingested" + (f": {names}" if names else ""),
                "" if repos else "run: memcl ingest <path>",
            )
        except Exception:
            record(False, "repos", "GET /repos failed", "check the server logs")

    overall = all(bool(c["ok"]) for c in checks)
    if _json_mode(args):
        _emit({"ok": overall, "checks": checks})
        return 0 if overall else 1
    for c in checks:
        render.render_check(
            ui, bool(c["ok"]), str(c["name"]), str(c["detail"]), str(c["fix"]),
        )
    return 0 if overall else 1


async def _cmd_reembed(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    repo = await _resolve_repo(client, args.repo or args.repo_id)
    res = await client.reembed_repository(repo_id=repo)
    if _json_mode(args):
        _emit(res.model_dump(mode="json"))
        return 0
    return render.render_reembed(ui, res)


async def _cmd_snapshot_build(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    res = await client.get_snapshot(
        tenant_id=args.tenant_id,
        state_version_token=args.state_version or "v0",
    )
    if _json_mode(args):
        _emit(res.model_dump(mode="json"))
        return 0
    ui.out.print(
        f"[green]✓[/green] snapshot [bold]{res.snapshot_id}[/bold] "
        f"(tenant {res.tenant_id}) captured at {res.captured_at}"
    )
    for key, value in sorted(res.components.items()):
        ui.out.print(f"   [dim]{key}[/dim] {value}")
    return 0


async def _cmd_snapshot_replay(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    try:
        payload: Any = json.loads(args.payload) if args.payload else None
    except json.JSONDecodeError as exc:
        if _json_mode(args):
            _emit_error({"error": "bad_json", "field": "payload", "detail": str(exc)})
        else:
            ui.error(f"--payload is not valid JSON: {exc}")
        return 2
    try:
        expected: Any = json.loads(args.expected) if args.expected else None
    except json.JSONDecodeError as exc:
        if _json_mode(args):
            _emit_error({"error": "bad_json", "field": "expected", "detail": str(exc)})
        else:
            ui.error(f"--expected is not valid JSON: {exc}")
        return 2
    res = await client.replay_snapshot(
        snapshot_id=args.snapshot_id, payload=payload, expected_output=expected,
    )
    if _json_mode(args):
        _emit(res.model_dump(mode="json"))
        return 0
    if res.matches:
        ui.out.print(
            f"[green]✓[/green] replay matched (hash {res.actual_hash})"
        )
        return 0
    ui.out.print(
        f"[red]✗[/red] replay MISMATCH — expected {res.expected_hash}, "
        f"got {res.actual_hash}"
    )
    if res.notes:
        ui.note(res.notes)
    return 1


async def _cmd_snapshot_legacy(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """v1 spelling `memcl snapshot --tenant-id X` → snapshot build."""
    if not args.tenant_id:
        ui.error("usage: memcl snapshot build --tenant-id <id>")
        return 2
    ui.note("bare `memcl snapshot` is deprecated — use: memcl snapshot build")
    return await _cmd_snapshot_build(client, args, ui, settings)


async def _cmd_replay_legacy(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    ui.note("memcl replay is deprecated — use: memcl snapshot replay")
    return await _cmd_snapshot_replay(client, args, ui, settings)


async def _cmd_query_legacy(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    ui.note("memcl query is deprecated — use: memcl search")
    if args.seed_unit_ids or args.unit_kinds:
        ui.note(
            "--seed-unit-ids / --unit-kinds have no search_code equivalent "
            "and were ignored"
        )
    return await _cmd_search(client, args, ui, settings)


async def _cmd_graph_legacy(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    ui.note("memcl graph is deprecated — use: memcl explore")
    if not getattr(args, "direction", None):
        args.direction = "all"
    return await _cmd_explore(client, args, ui, settings)


# ---------------------------------------------------------------------------
# setup / key / config-set commands
# ---------------------------------------------------------------------------
def _print_connect_command(ui: UI, base_url: str, api_key: str) -> None:
    """Print the pre-filled claude mcp add command."""
    cmd = (
        f'claude mcp add --transport sse --scope user memory-cl '
        f'{base_url}/mcp/sse --header "X-API-Key: {api_key}"'
    )
    ui.out.print(f"\n[bold]Run this to connect Claude:[/bold]\n  {cmd}\n")


async def _cmd_setup(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """Interactive first-run wizard."""
    cfg = await client.get_config()
    base_url = settings.base_url_value

    if cfg.configured:
        # Already set up — show hint and how to get the connect command.
        if _json_mode(args):
            _emit({"configured": True, "mcp_key_hint": cfg.mcp_key_hint})
            return 0
        ui.out.print(
            f"[green]Already set up[/green] — your key is [bold]{cfg.mcp_key_hint}[/bold]. "
            "Use [bold]memcl key rotate[/bold] to replace it."
        )
        _print_connect_command(ui, base_url, cfg.mcp_key_hint or "(key hidden)")
        return 0

    # --- Step 1: generate key ---
    ui.out.print("[bold]Step 1/3[/bold] — Generating MCP access key …")
    key_res = await client.generate_mcp_key()
    api_key = key_res.api_key

    # Save to config file (write_config_file preserves existing base_url / timeout).
    write_config_file(
        base_url=base_url,
        api_key=api_key,
        timeout=settings.timeout_value,
    )
    ui.out.print(
        f"\n[bold green]Your MCP key:[/bold green] [bold]{api_key}[/bold]\n"
        f"[dim]Saved to {config_path()} — keep it secret.[/dim]\n"
    )

    # --- Step 2: OpenAI key (optional) ---
    ui.out.print("[bold]Step 2/3[/bold] — Embeddings (optional)")
    openai_key: str = ""
    if sys.stdin.isatty():
        from rich.prompt import Prompt
        openai_key = Prompt.ask(
            "OpenAI API key (sk-…) [dim]empty to skip[/dim]",
            default="",
            console=ui.err,
        )
    if openai_key.strip():
        await client.set_openai_key(openai_key.strip())
        await client.set_embedding_mode("openai")
        ui.out.print("[green]✓[/green] OpenAI key saved — semantic search enabled.")
    else:
        ui.out.print(
            "[dim]Skipped — add one later with: "
            "memcl config set openai-key <sk-…>[/dim]"
        )

    # --- Step 3: connect command ---
    ui.out.print("\n[bold]Step 3/3[/bold] — Connect your agent")
    _print_connect_command(ui, base_url, api_key)
    ui.out.print(
        "\n[dim]Next step:[/dim] [bold]memcl ingest <path>[/bold]"
    )

    await client.complete_onboarding()
    if _json_mode(args):
        _emit({"configured": True, "api_key": api_key})
    return 0


async def _cmd_key_generate(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """Generate the MCP key (only valid when unconfigured)."""
    try:
        key_res = await client.generate_mcp_key()
    except MemoryClientError as exc:
        if exc.status_code in (401, 409):
            if _json_mode(args):
                _emit_error({"error": "already_configured", "status_code": exc.status_code})
            else:
                ui.error(
                    "A key is already configured — use [bold]memcl key rotate[/bold] "
                    "to replace it."
                )
            return 1
        raise
    api_key = key_res.api_key
    write_config_file(
        base_url=settings.base_url_value,
        api_key=api_key,
        timeout=settings.timeout_value,
    )
    if _json_mode(args):
        _emit({"api_key": api_key})
        return 0
    ui.out.print(
        f"[bold green]MCP key:[/bold green] [bold]{api_key}[/bold]\n"
        f"[dim]Saved to {config_path()}[/dim]"
    )
    return 0


async def _cmd_key_show(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """Print the masked key hint from GET /config."""
    cfg = await client.get_config()
    if not cfg.configured:
        if _json_mode(args):
            _emit({"configured": False, "mcp_key_hint": None})
        else:
            ui.error("No key configured yet — run: memcl setup")
        return 1
    if _json_mode(args):
        _emit({"configured": True, "mcp_key_hint": cfg.mcp_key_hint})
        return 0
    ui.out.print(f"MCP key: [bold]{cfg.mcp_key_hint}[/bold]")
    ui.note("Use `memcl key rotate` to replace it.")
    return 0


async def _cmd_key_rotate(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """Rotate the MCP key (requires current key via config/env/flag)."""
    key_res = await client.rotate_mcp_key()
    api_key = key_res.api_key
    write_config_file(
        base_url=settings.base_url_value,
        api_key=api_key,
        timeout=settings.timeout_value,
    )
    if _json_mode(args):
        _emit({"api_key": api_key})
        return 0
    ui.out.print(
        f"[bold green]New MCP key:[/bold green] [bold]{api_key}[/bold]\n"
        f"[dim]Saved to {config_path()}[/dim]"
    )
    _print_connect_command(ui, settings.base_url_value, api_key)
    ui.out.print(
        "[yellow]Warning:[/yellow] agents that already added memory-cl must "
        "re-add it with the new key."
    )
    return 0


async def _cmd_config_set_openai_key(
    client: AsyncMemoryClient, args: argparse.Namespace, ui: UI,
    settings: CliSettings,
) -> int:
    """POST /config/openai-key — set the OpenAI API key server-side."""
    key: str = args.openai_key
    await client.set_openai_key(key)
    masked = _mask_key(key)
    if _json_mode(args):
        _emit({"ok": True, "hint": masked})
        return 0
    ui.out.print(f"[green]✓[/green] OpenAI key set: [bold]{masked}[/bold]")
    ui.note("Embedding mode is unchanged — use `memcl config set embedding-mode openai` to enable.")
    return 0


# ---------------------------------------------------------------------------
# config (no client needed)
# ---------------------------------------------------------------------------
def _mask_key(value: str | None) -> str:
    if not value:
        return "(not set)"
    return "****" + value[-4:] if len(value) > 4 else "****"


def _cmd_config(args: argparse.Namespace, ui: UI, settings: CliSettings) -> int:
    if args.config_cmd == "show":
        payload = {
            "config_file": str(config_path()),
            "config_file_exists": config_path().is_file(),
            "base_url": settings.base_url_value,
            "base_url_source": settings.base_url.source,
            "api_key": _mask_key(settings.api_key_value),
            "api_key_source": settings.api_key.source,
            "timeout": settings.timeout_value,
            "timeout_source": settings.timeout.source,
        }
        if _json_mode(args):
            _emit(payload)
            return 0
        from rich.table import Table
        table = Table(header_style="bold")
        table.add_column("setting")
        table.add_column("value")
        table.add_column("source")
        table.add_row("base_url", settings.base_url_value, settings.base_url.source)
        table.add_row("api_key", _mask_key(settings.api_key_value), settings.api_key.source)
        table.add_row("timeout", f"{settings.timeout_value:g}s", settings.timeout.source)
        ui.out.print(table)
        ui.note(
            f"config file: {config_path()}"
            + ("" if config_path().is_file() else " (absent — memcl config init)")
        )
        return 0

    # config init — prompt on a TTY, otherwise write resolved values.
    base_url = settings.base_url_value
    api_key = settings.api_key_value
    timeout = settings.timeout_value
    if sys.stdin.isatty() and sys.stdout.isatty():
        from rich.prompt import Prompt
        base_url = Prompt.ask("Base URL", default=base_url, console=ui.err)
        entered = Prompt.ask(
            "API key (empty for none)", default=api_key or "", console=ui.err,
        )
        api_key = entered or None
        timeout = float(
            Prompt.ask("Timeout (seconds)", default=f"{timeout:g}", console=ui.err)
        )
    path = write_config_file(base_url=base_url, api_key=api_key, timeout=timeout)
    if _json_mode(args):
        _emit({"written": str(path)})
    else:
        ui.out.print(f"[green]✓[/green] wrote {path}")
        ui.note("precedence: flags > MEMCL_* env > config file > defaults")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def _repo_flag(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument(
        "-r", "--repo", "--repo-id", dest="repo", default=None,
        required=required, help="repository id (see `memcl repos`)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memcl",
        description=(
            "Memory-CL CLI — explore and manage ingested repositories. "
            "Start with `memcl doctor` if anything misbehaves."
        ),
    )
    p.add_argument(
        "--base-url", default=None,
        help="service base URL (env MEMCL_BASE_URL, or ~/.memcl/config.toml)",
    )
    p.add_argument(
        "--api-key", default=None,
        help="API key (env MEMCL_API_KEY, or ~/.memcl/config.toml)",
    )
    p.add_argument(
        "--timeout", type=float, default=None,
        help="request timeout in seconds (env MEMCL_TIMEOUT; default 30)",
    )
    p.add_argument(
        "--request-id", default=None,
        help="X-Request-ID for correlating CLI calls with server traces",
    )

    # Shared by every subcommand. SUPPRESS keeps a parent-level --json from
    # being clobbered by a child parser's default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="emit the raw API payload as canonical JSON (for scripts)",
    )

    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    p_ingest = sub.add_parser(
        "ingest", parents=[common],
        help="ingest a repository (repo-id and commit inferred)",
    )
    p_ingest.add_argument(
        "path", nargs="?", default=".",
        help="local repo directory or server path under /repos (default: .)",
    )
    p_ingest.add_argument(
        "--repo-id", default=None,
        help="override the inferred repo id (default: directory basename)",
    )
    p_ingest.add_argument(
        "--commit-sha", default=None,
        help="override the inferred commit (default: git rev-parse HEAD)",
    )
    p_ingest.add_argument(
        "--server-path", default=None,
        help="exact path inside the API container, e.g. /repos/acme",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_repos = sub.add_parser(
        "repos", parents=[common], help="list ingested repositories",
    )
    p_repos.set_defaults(func=_cmd_repos)

    p_fresh = sub.add_parser(
        "freshness", parents=[common],
        help="auto-reingest status + control (managed repos, watcher)",
    )
    fresh_sub = p_fresh.add_subparsers(dest="freshness_cmd", metavar="<subcommand>")
    pf_add = fresh_sub.add_parser(
        "add", parents=[common], help="add a managed git repo (clone + keep fresh)",
    )
    pf_add.add_argument("url", help="git remote URL (https or git@)")
    pf_add.add_argument("--branch", default=None, help="branch to track (default: repo default)")
    pf_add.add_argument("--id", default=None, help="override repo_id (default: derived from URL)")
    pf_add.set_defaults(func=_cmd_freshness)
    for name, helptext in (
        ("sync", "force a freshness check now"),
        ("pause", "pause auto-reingest for a repo"),
        ("resume", "resume auto-reingest for a repo"),
        ("remove", "deregister a repo (delete a managed clone)"),
    ):
        pf = fresh_sub.add_parser(name, parents=[common], help=helptext)
        pf.add_argument("repo_id", help="repo id")
        pf.set_defaults(func=_cmd_freshness)
    p_fresh.set_defaults(func=_cmd_freshness)

    p_token = sub.add_parser(
        "token", parents=[common], help="manage named, revocable API tokens",
    )
    token_sub = p_token.add_subparsers(dest="token_cmd", metavar="<subcommand>")
    pt_create = token_sub.add_parser("create", parents=[common], help="mint a named token")
    pt_create.add_argument("name", help="a label for the token (e.g. laptop, ci)")
    pt_create.set_defaults(func=_cmd_token)
    pt_revoke = token_sub.add_parser("revoke", parents=[common], help="revoke a token by id")
    pt_revoke.add_argument("id", help="token id (from `memcl token list`)")
    pt_revoke.set_defaults(func=_cmd_token)
    p_token.set_defaults(func=_cmd_token)

    p_search = sub.add_parser(
        "search", parents=[common],
        help="semantic code search (natural-language question)",
    )
    p_search.add_argument("question", help="plain-prose question")
    _repo_flag(p_search)
    p_search.add_argument(
        "-k", "--top-k", dest="top_k", type=int, default=8,
        help="max results (default 8)",
    )
    p_search.set_defaults(func=_cmd_search)

    p_read = sub.add_parser(
        "read", parents=[common],
        help="read one unit (qualified name, unit id, or file path)",
    )
    p_read.add_argument("reference", help="qualified_name, unit_id, or file path")
    _repo_flag(p_read)
    p_read.set_defaults(func=_cmd_read)

    p_explore = sub.add_parser(
        "explore", parents=[common],
        help="walk the code graph from one symbol",
    )
    p_explore.add_argument("qualified_name", help="symbol to explore from")
    _repo_flag(p_explore)
    p_explore.add_argument(
        "--direction", default="all",
        choices=["callers", "callees", "imports", "imported_by", "inherits", "all"],
        help="relationship to follow (default: all)",
    )
    p_explore.add_argument(
        "--depth", type=int, default=1, help="hops to traverse (default 1)",
    )
    p_explore.set_defaults(func=_cmd_explore)

    p_symbols = sub.add_parser(
        "symbols", parents=[common],
        help="find symbols by (partial) qualified name",
    )
    p_symbols.add_argument("query", help="case-insensitive substring")
    _repo_flag(p_symbols)
    p_symbols.add_argument(
        "--limit", type=int, default=20, help="max matches (default 20)",
    )
    p_symbols.set_defaults(func=_cmd_symbols)

    p_overview = sub.add_parser(
        "overview", parents=[common],
        help="structural overview of one repo",
    )
    p_overview.add_argument(
        "repo", nargs="?", default=None,
        help="repo id (optional when exactly one repo is ingested)",
    )
    p_overview.set_defaults(func=_cmd_overview)

    p_status = sub.add_parser(
        "status", parents=[common], help="server status, human-readable",
    )
    p_status.set_defaults(func=_cmd_status)

    p_doctor = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose config, connectivity, auth, embeddings, repos",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_reembed = sub.add_parser(
        "reembed", parents=[common],
        help="backfill real vectors for an ingested repo",
    )
    p_reembed.add_argument("repo", nargs="?", default=None, help="repo id")
    p_reembed.add_argument(  # v1 spelling
        "--repo-id", dest="repo_id", default=None, help=argparse.SUPPRESS,
    )
    p_reembed.set_defaults(func=_cmd_reembed)

    p_snap = sub.add_parser(
        "snapshot", parents=[common], help="build or replay system snapshots",
    )
    snap_sub = p_snap.add_subparsers(dest="snapshot_cmd")
    p_build = snap_sub.add_parser(
        "build", parents=[common], help="capture a snapshot",
    )
    p_build.add_argument("--tenant-id", required=True)
    p_build.add_argument("--state-version", default="v0")
    p_build.set_defaults(func=_cmd_snapshot_build)
    p_replay = snap_sub.add_parser(
        "replay", parents=[common], help="verify a payload against a snapshot",
    )
    p_replay.add_argument("snapshot_id")
    p_replay.add_argument("--payload", required=True, help="JSON payload")
    p_replay.add_argument("--expected", default=None, help="JSON expected output")
    p_replay.set_defaults(func=_cmd_snapshot_replay)
    # v1 spelling: `memcl snapshot --tenant-id X` (no sub-action).
    p_snap.add_argument("--tenant-id", default=None, help=argparse.SUPPRESS)
    p_snap.add_argument("--state-version", default="v0", help=argparse.SUPPRESS)
    p_snap.set_defaults(func=_cmd_snapshot_legacy)

    p_config = sub.add_parser(
        "config", parents=[common], help="manage ~/.memcl/config.toml",
    )
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser(
        "init", parents=[common], help="create the config file (prompts on a TTY)",
    )
    config_sub.add_parser(
        "show", parents=[common], help="effective config and where it came from",
    )
    p_config_set = config_sub.add_parser(
        "set", parents=[common], help="set a server-side config value",
    )
    config_set_sub = p_config_set.add_subparsers(
        dest="config_set_key", required=True,
    )
    p_set_openai = config_set_sub.add_parser(
        "openai-key", parents=[common], help="set the OpenAI API key on the server",
    )
    p_set_openai.add_argument("openai_key", help="the key (sk-…)")
    p_set_openai.set_defaults(func=_cmd_config_set_openai_key)

    p_setup = sub.add_parser(
        "setup", parents=[common],
        help="first-run wizard — generate key, set embeddings, print connect command",
    )
    p_setup.set_defaults(func=_cmd_setup)

    p_key = sub.add_parser(
        "key", parents=[common],
        help="manage the MCP access key (generate / show / rotate)",
    )
    key_sub = p_key.add_subparsers(dest="key_cmd", required=True)
    key_sub.add_parser(
        "generate", parents=[common], help="generate a new key (fresh install only)",
    ).set_defaults(func=_cmd_key_generate)
    key_sub.add_parser(
        "show", parents=[common], help="print the masked key hint",
    ).set_defaults(func=_cmd_key_show)
    key_sub.add_parser(
        "rotate", parents=[common], help="replace the current key (re-add required)",
    ).set_defaults(func=_cmd_key_rotate)

    # ---- deprecated v1 subcommands (hidden from --help) ----
    p_query = sub.add_parser("query", parents=[common])
    p_query.add_argument("question", metavar="text")
    p_query.add_argument(
        "-r", "--repo", "--repo-id", dest="repo", default=None,
    )
    p_query.add_argument("-k", "--top-k", dest="top_k", type=int, default=8)
    p_query.add_argument("--seed-unit-ids", nargs="*", default=[])
    p_query.add_argument("--unit-kinds", nargs="*", default=[])
    p_query.set_defaults(func=_cmd_query_legacy)

    p_graph = sub.add_parser("graph", parents=[common])
    p_graph.add_argument("qualified_name", metavar="node")
    p_graph.add_argument(
        "-r", "--repo", "--repo-id", dest="repo", default=None,
    )
    p_graph.add_argument("--depth", type=int, default=1)
    p_graph.set_defaults(func=_cmd_graph_legacy)

    p_replay_legacy = sub.add_parser("replay", parents=[common])
    p_replay_legacy.add_argument("snapshot_id")
    p_replay_legacy.add_argument("--payload", required=True)
    p_replay_legacy.add_argument("--expected", default=None)
    p_replay_legacy.set_defaults(func=_cmd_replay_legacy)

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> int:
    ui = UI()
    try:
        settings = resolve_settings(
            base_url_flag=args.base_url,
            api_key_flag=args.api_key,
            timeout_flag=args.timeout,
        )
    except ConfigError as exc:
        ui.error(str(exc))
        return 1

    if args.command == "config":
        # "config set *" sub-subcommands need the async client.
        if getattr(args, "config_cmd", None) == "set" and hasattr(args, "func"):
            pass  # fall through to async dispatch below
        else:
            try:
                return _cmd_config(args, ui, settings)
            except (OSError, ValueError) as exc:
                ui.error(str(exc))
                return 1

    timeout = settings.timeout_value
    if args.command == "ingest" and settings.timeout.source == "default":
        timeout = INGEST_DEFAULT_TIMEOUT

    json_mode = _json_mode(args)
    try:
        async with AsyncMemoryClient(
            base_url=settings.base_url_value,
            api_key=settings.api_key_value,
            timeout_seconds=timeout,
            request_id=args.request_id,
        ) as client:
            result: int = await args.func(client, args, ui, settings)
            return result
    except CliFailure as exc:
        if json_mode:
            _emit_error({"error": "cli", "message": str(exc)})
        else:
            ui.error(str(exc))
        return 1
    except MemoryClientError as exc:
        _render_http_error(ui, exc, json_mode)
        return 1
    except httpx.ConnectError:
        if json_mode:
            _emit_error({"error": "connect", "url": settings.base_url_value})
        else:
            ui.error(
                f"Can't reach {settings.base_url_value} — is the server up? "
                "Try: memcl doctor"
            )
        return 1
    except httpx.TimeoutException:
        if json_mode:
            _emit_error({"error": "timeout", "url": settings.base_url_value})
        else:
            ui.error(
                f"request timed out after {timeout:g}s — the server may still "
                "be working. Retry with --timeout <seconds> or check: "
                "memcl status"
            )
        return 1


def _render_http_error(ui: UI, exc: MemoryClientError, json_mode: bool) -> None:
    if json_mode:
        _emit_error({
            "error": "http",
            "status_code": exc.status_code,
            "url": exc.url,
            "body": exc.body,
        })
        return
    if exc.status_code == 401:
        ui.error(
            "API key rejected — set MEMCL_API_KEY or run memcl config init"
        )
        return
    if exc.status_code == 200:
        # Tool-level failure on an HTTP 200 envelope — show the tool's own error.
        detail: Any = exc.body
        ui.error(str(detail) if detail else "tool returned an error (no detail)")
        return
    detail = exc.body
    if isinstance(exc.body, dict) and "detail" in exc.body:
        detail = exc.body["detail"]
    ui.error(f"server returned HTTP {exc.status_code} for {exc.url}: {detail}")


__all__ = ["CliFailure", "build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
