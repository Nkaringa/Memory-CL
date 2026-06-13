"""All human-facing rendering for memcl — one place, all rich.

Renderers take a typed SDK result and return the process exit code so
command handlers stay one-liners. rich degrades automatically on
NO_COLOR / non-TTY output, so nothing here branches on terminal-ness
except the syntax highlighter (pager-friendly raw bytes when piped).
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table
from rich.tree import Tree

from sdk.types import (
    ExploreResult,
    FindSymbolResult,
    IngestResult,
    ReadUnitResult,
    ReembedResult,
    RepoOverviewResult,
    ReposResult,
    SearchCodeResult,
    StatusResult,
)

_OK = "[green]✓[/green]"
_WARN = "[yellow]⚠[/yellow]"
_FAIL = "[red]✗[/red]"


class UI:
    """Two consoles: stdout for results, stderr for notices/spinners."""

    def __init__(self) -> None:
        self.out = Console()
        self.err = Console(stderr=True)

    def error(self, message: str) -> None:
        self.err.print(f"[bold red]error:[/bold red] {escape(message)}")

    def note(self, message: str) -> None:
        self.err.print(f"[dim]{escape(message)}[/dim]")

    def hint(self, message: str | None) -> None:
        if message:
            self.err.print(f"[dim]hint: {escape(message)}[/dim]")


def _score_markup(score: float) -> str:
    color = "green" if score >= 0.7 else ("yellow" if score >= 0.4 else "red")
    return f"[bold {color}]{score:.2f}[/bold {color}]"


def _location(file_path: str | None, lines: str | None) -> str:
    if not file_path:
        return ""
    return f"{file_path}:{lines}" if lines else file_path


def _render_unknown_repo(
    ui: UI, error: str | None, valid_repo_ids: list[str], hint: str | None,
) -> int:
    ui.error(error or "unknown repository")
    if valid_repo_ids:
        ui.note("ingested repos: " + ", ".join(valid_repo_ids))
    else:
        ui.hint(hint or "nothing is ingested yet — run: memcl ingest <path>")
    return 1


# ---------------------------------------------------------------------------
# repos
# ---------------------------------------------------------------------------
def render_repos(ui: UI, res: ReposResult) -> int:
    if not res.repos:
        ui.out.print("No repositories ingested yet.")
        ui.hint("run: memcl ingest <path>")
        return 0
    table = Table(title=None, header_style="bold")
    table.add_column("repo")
    table.add_column("units", justify="right")
    table.add_column("files", justify="right")
    table.add_column("languages")
    for repo in res.repos:
        table.add_row(
            repo.repo_id,
            str(repo.units),
            str(repo.files),
            ", ".join(repo.languages),
        )
    ui.out.print(table)
    return 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def render_search(ui: UI, res: SearchCodeResult) -> int:
    if res.error:
        return _render_unknown_repo(ui, res.error, res.valid_repo_ids, res.hint)
    if not res.results:
        ui.out.print("No results.")
        ui.hint(res.hint)
        return 0
    for idx, hit in enumerate(res.results, start=1):
        qname = escape(hit.qualified_name or "?")
        kind = f" [dim]({escape(hit.kind)})[/dim]" if hit.kind else ""
        ui.out.print(
            f"{idx:>2}. {_score_markup(hit.score)}  [bold cyan]{qname}[/bold cyan]{kind}"
        )
        loc = _location(hit.file_path, hit.lines)
        meta = " · ".join(
            part
            for part in (loc, ", ".join(hit.channels), hit.repo_id or "")
            if part
        )
        if meta:
            ui.out.print(f"    [dim]{escape(meta)}[/dim]")
        for line in hit.snippet.splitlines()[:3]:
            ui.out.print(f"    [dim]│[/dim] {escape(line)}")
        ui.out.print()
    footer = f"{len(res.results)} of {res.total_matches} matches"
    if res.truncated:
        footer += " (truncated — lower -k or pass -r <repo>)"
    if res.failed_repos:
        footer += f" · failed repos: {', '.join(res.failed_repos)}"
    ui.out.print(f"[dim]{escape(footer)}[/dim]")
    return 0


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
def render_read(ui: UI, res: ReadUnitResult) -> int:
    if res.error:
        return _render_unknown_repo(ui, res.error, res.valid_repo_ids, res.hint)
    if not res.found:
        ui.error(f"nothing matched {res.reference!r}")
        for s in res.suggestions:
            ui.err.print(
                f"  [cyan]{escape(s.get('qualified_name', '?'))}[/cyan]"
                f" [dim]({escape(s.get('kind', '?'))}"
                + (f" · {escape(s['repo_id'])}" if s.get("repo_id") else "")
                + ")[/dim]"
            )
        ui.hint(res.hint)
        return 1

    loc = _location(res.file_path, res.lines)
    header = " · ".join(
        part
        for part in (loc, res.kind or "", res.language or "", f"repo {res.repo_id}")
        if part
    )
    ui.out.print(f"[dim]# {escape(header)}[/dim]")
    if res.signature:
        ui.out.print(f"[dim]# {escape(res.signature)}[/dim]")
    if res.parent_chain:
        chain = " ← ".join(
            p.get("qualified_name", "?") for p in res.parent_chain
        )
        ui.out.print(f"[dim]# within: {escape(chain)}[/dim]")
    if ui.out.is_terminal:
        ui.out.print(
            Syntax(
                res.content,
                res.language or "text",
                line_numbers=False,
                word_wrap=False,
                background_color="default",
            )
        )
    else:
        # Pager/pipe friendly: bytes exactly as stored, no wrapping.
        sys.stdout.write(res.content)
        if not res.content.endswith("\n"):
            sys.stdout.write("\n")
    if res.truncated:
        ui.note("content truncated by the server's token cap")
    return 0


# ---------------------------------------------------------------------------
# explore
# ---------------------------------------------------------------------------
def render_explore(ui: UI, res: ExploreResult) -> int:
    if res.error:
        return _render_unknown_repo(ui, res.error, res.valid_repo_ids, res.hint)
    if not res.found:
        ui.error(f"unknown symbol {res.qualified_name!r}")
        for s in res.suggestions:
            ui.err.print(f"  [cyan]{escape(s.get('qualified_name', '?'))}[/cyan]")
        ui.hint(res.hint)
        return 1

    seed = res.seed or {}
    seed_loc = _location(seed.get("file_path"), seed.get("lines"))
    ui.out.print(
        f"[bold cyan]{escape(str(seed.get('qualified_name', '?')))}[/bold cyan]"
        f" [dim]({escape(str(seed.get('kind', '?')))} · {escape(seed_loc)})[/dim]"
    )
    if seed.get("signature"):
        ui.out.print(f"[dim]{escape(str(seed['signature']))}[/dim]")
    if not res.neighbors:
        ui.out.print(
            f"No {res.direction} relationships within depth {res.depth}."
        )
        ui.hint(res.hint)
        return 0

    table = Table(header_style="bold")
    table.add_column("relation")
    table.add_column("d", justify="right")
    table.add_column("kind")
    table.add_column("qualified name", overflow="fold")
    table.add_column("location", overflow="fold")
    table.add_column("signature", overflow="fold")
    for n in res.neighbors:
        table.add_row(
            n.relation,
            str(n.distance),
            n.kind or "",
            n.qualified_name or n.node_id or "?",
            _location(n.file_path, n.lines),
            n.signature or "",
        )
    ui.out.print(table)
    if res.truncated:
        ui.note("neighbor list truncated by the server")
    if res.warning:
        ui.note(res.warning)
    return 0


# ---------------------------------------------------------------------------
# symbols
# ---------------------------------------------------------------------------
def render_symbols(ui: UI, res: FindSymbolResult) -> int:
    if res.error:
        return _render_unknown_repo(ui, res.error, res.valid_repo_ids, res.hint)
    if not res.matches:
        ui.out.print("No symbols matched.")
        ui.hint(res.hint)
        return 0
    table = Table(header_style="bold")
    table.add_column("qualified name", overflow="fold")
    table.add_column("kind")
    table.add_column("location", overflow="fold")
    table.add_column("repo")
    for m in res.matches:
        table.add_row(
            m.qualified_name,
            m.kind or "",
            _location(m.file_path, m.lines),
            m.repo_id or "",
        )
    ui.out.print(table)
    if res.truncated:
        ui.note("match list truncated — raise --limit or narrow the query")
    return 0


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
def _bar(count: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = max(1, round(width * count / total)) if count else 0
    return "█" * filled


def render_overview(ui: UI, res: RepoOverviewResult) -> int:
    if res.error or not res.found:
        return _render_unknown_repo(ui, res.error, res.valid_repo_ids, res.hint)

    ui.out.print(
        f"[bold]{escape(res.repo_id or '?')}[/bold] — "
        f"{res.units} units · {res.files} files"
    )
    if res.languages:
        ui.out.print()
        ui.out.print("[bold]languages[/bold]")
        total = sum(res.languages.values())
        width = max(len(lang) for lang in res.languages)
        for lang, count in sorted(
            res.languages.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            pct = (100 * count) // total if total else 0
            ui.out.print(
                f"  {lang:<{width}}  [cyan]{_bar(count, total)}[/cyan]"
                f" {count} ({pct}%)"
            )
    if res.unit_kinds:
        kinds = " · ".join(
            f"{k}: {v}"
            for k, v in sorted(res.unit_kinds.items(), key=lambda kv: -kv[1])
        )
        ui.out.print(f"[dim]{escape(kinds)}[/dim]")

    if res.module_tree:
        ui.out.print()
        tree = Tree("[bold]modules[/bold]")
        for entry in res.module_tree:
            name = str(entry.get("name", "?"))
            units = entry.get("units", 0)
            branch = tree.add(f"[cyan]{escape(name)}[/cyan] [dim]({units} units)[/dim]")
            for module in list(entry.get("modules", []))[:8]:
                branch.add(f"[dim]{escape(str(module))}[/dim]")
        ui.out.print(tree)

    if res.most_connected:
        ui.out.print()
        table = Table(title="most connected", header_style="bold", title_justify="left")
        table.add_column("qualified name", overflow="fold")
        table.add_column("kind")
        table.add_column("links", justify="right")
        for item in res.most_connected[:8]:
            table.add_row(
                str(item.get("qualified_name", "?")),
                str(item.get("kind", "")),
                str(item.get("connections", "")),
            )
        ui.out.print(table)
    if res.doc_files:
        shown = ", ".join(res.doc_files[:6])
        more = f" (+{len(res.doc_files) - 6} more)" if len(res.doc_files) > 6 else ""
        ui.out.print(f"[dim]docs: {escape(shown + more)}[/dim]")
    if res.note:
        ui.note(res.note)
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
_STAGE_ICON = {"ok": _OK, "degraded": _WARN, "failed": _FAIL}


def render_status(ui: UI, res: StatusResult) -> int:
    ui.out.print(
        f"[bold]{escape(res.service)}[/bold] @ {escape(res.environment)}"
        f" [dim](schema v{escape(res.schema_version)})[/dim]"
    )
    boot_icon = _OK if res.boot_overall_ok else _FAIL
    ui.out.print(f"{boot_icon} boot {'ok' if res.boot_overall_ok else 'FAILED'}")
    for stage in res.boot_stages:
        icon = _STAGE_ICON.get(str(stage.get("status", "")), _WARN)
        line = f"   {icon} {escape(str(stage.get('name', '?')))}"
        error = str(stage.get("error", "") or "")
        if error:
            line += f" [red]{escape(error)}[/red]"
        ui.out.print(line)

    safe = res.safe_mode or {}
    if safe.get("enabled"):
        ui.out.print(f"{_FAIL} safe mode ON — {escape(str(safe.get('reason', '')))}")
    else:
        ui.out.print(f"{_OK} safe mode off")
    emb_icon = _OK if res.embeddings_enabled else _WARN
    emb_text = "on" if res.embeddings_enabled else "off (semantic search degraded)"
    ui.out.print(f"{emb_icon} embeddings {emb_text}")
    ui.out.print(f"{_OK} {res.mcp_tool_count} MCP tools registered")
    return 0


# ---------------------------------------------------------------------------
# ingest / reembed
# ---------------------------------------------------------------------------
def render_ingest(ui: UI, res: IngestResult, elapsed: str) -> int:
    metrics = res.metrics or {}
    units = metrics.get("units_emitted")
    files = metrics.get("files_walked")
    parts = []
    if units is not None:
        parts.append(f"units: {int(units)}")
    if files is not None:
        parts.append(f"files: {int(files)}")
    parts.append(f"collection: {res.units_collection}")
    ui.out.print(
        f"{_OK} Ingested [bold]{escape(res.repo_id)}[/bold]"
        f" @ {escape(res.commit_sha[:12])} in {elapsed}"
    )
    ui.out.print("   " + " · ".join(parts))
    if res.failed_files:
        shown = ", ".join(res.failed_files[:5])
        more = (
            f" (+{len(res.failed_files) - 5} more)"
            if len(res.failed_files) > 5
            else ""
        )
        ui.out.print(f"{_WARN} failed files: {escape(shown + more)}")
    ui.hint(f"try: memcl search \"what does this repo do\" -r {res.repo_id}")
    return 0


def render_reembed(ui: UI, res: ReembedResult) -> int:
    icon = _OK if res.failed_batches == 0 else _WARN
    ui.out.print(
        f"{icon} Re-embedded [bold]{escape(res.repo_id)}[/bold] — "
        f"{res.units_embedded}/{res.units_total} units"
        + (f" · {res.failed_batches} failed batches" if res.failed_batches else "")
    )
    return 0 if res.failed_batches == 0 else 1


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def render_check(
    ui: UI, ok: bool, label: str, detail: str = "", fix: str = "",
) -> None:
    icon = _OK if ok else _FAIL
    line = f"{icon} {escape(label)}"
    if detail:
        line += f" [dim]— {escape(detail)}[/dim]"
    ui.out.print(line)
    if not ok and fix:
        ui.out.print(f"   [yellow]fix:[/yellow] {escape(fix)}")


__all__ = [
    "UI",
    "render_check",
    "render_explore",
    "render_ingest",
    "render_overview",
    "render_read",
    "render_reembed",
    "render_repos",
    "render_search",
    "render_status",
    "render_symbols",
]
