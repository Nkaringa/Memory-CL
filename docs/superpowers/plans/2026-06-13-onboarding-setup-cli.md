# Onboarding Setup CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 typed SDK methods for the config endpoints, then build `memcl setup`, `memcl key generate|show|rotate`, and `memcl config set openai-key` as rich CLI commands that write the MCP key to `~/.memcl/config.toml`.

**Architecture:** SDK methods are thin wrappers over the existing `_get_json`/`_post_json` plumbing (same pattern as `get_status`/`ingest_repository`). CLI commands live in `apps/cli/main.py` (same dispatch idiom: async handler taking `client, args, ui, settings → int`). The generated MCP key is persisted to the config file via the existing `write_config_file` in `apps/cli/config.py` (no new config persistence code needed — the function already writes `api_key`). Tests mirror `tests/test_phase9_sdk_cli.py` using the fake ASGI app pattern.

**Tech Stack:** Python 3.12, httpx (ASGI transport for tests), Pydantic v2, rich (Prompt / Console), argparse subcommands, pytest-asyncio.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `sdk/types.py` | Modify | Add `AppConfigView` and `KeyResult` Pydantic models |
| `sdk/client.py` | Modify | Add 6 typed methods: `get_config`, `generate_mcp_key`, `rotate_mcp_key`, `set_openai_key`, `set_embedding_mode`, `complete_onboarding` |
| `sdk/__init__.py` | Modify | Export `AppConfigView` and `KeyResult` |
| `apps/cli/main.py` | Modify | Add `_cmd_setup`, `_cmd_key_generate`, `_cmd_key_show`, `_cmd_key_rotate`, `_cmd_config_set_openai_key`; wire `setup`, `key`, and extended `config set` subcommands into `build_parser` and `_dispatch` |
| `tests/test_onboarding_setup_cli.py` | Create | Tests for new SDK methods + CLI commands (fake ASGI app, happy path + already-configured + key-saved-to-config) |

---

## Task 1: Add SDK result types

**Files:**
- Modify: `sdk/types.py`
- Modify: `sdk/__init__.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_onboarding_setup_cli.py` (create the file):

```python
"""Tests for Phase-10 onboarding/setup SDK methods and CLI commands."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sdk import AppConfigView, AsyncMemoryClient, KeyResult, MemoryClientError


def test_app_config_view_is_importable() -> None:
    v = AppConfigView(
        configured=False,
        onboarding_completed=False,
        embedding_mode="openai",
        embeddings_enabled=False,
        has_openai_key=False,
        mcp_key_hint=None,
    )
    assert v.configured is False
    assert v.embedding_mode == "openai"


def test_key_result_is_importable() -> None:
    k = KeyResult(api_key="abc123")
    assert k.api_key == "abc123"
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py::test_app_config_view_is_importable tests/test_onboarding_setup_cli.py::test_key_result_is_importable -v
```

Expected: FAIL — `ImportError: cannot import name 'AppConfigView'`

- [ ] **Step 3: Add types to `sdk/types.py`**

Append to end of `sdk/types.py` (before nothing — it's the final section):

```python
class AppConfigView(_SdkBase):
    """GET /config response — onboarding state, never contains raw keys."""

    configured: bool
    onboarding_completed: bool
    embedding_mode: str
    embeddings_enabled: bool
    has_openai_key: bool
    mcp_key_hint: str | None = None


class KeyResult(_SdkBase):
    """POST /config/mcp-key/generate or rotate — the one-time key reveal."""

    api_key: str
```

- [ ] **Step 4: Export from `sdk/__init__.py`**

Add `AppConfigView` and `KeyResult` to the imports block and `__all__` list in `sdk/__init__.py`:

```python
# In the from sdk.types import (...) block, add:
    AppConfigView,
    KeyResult,

# In __all__, add:
    "AppConfigView",
    "KeyResult",
```

- [ ] **Step 5: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py::test_app_config_view_is_importable tests/test_onboarding_setup_cli.py::test_key_result_is_importable -v
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add sdk/types.py sdk/__init__.py tests/test_onboarding_setup_cli.py
git commit -m "feat(sdk): add AppConfigView and KeyResult types for config endpoints"
```

---

## Task 2: Add SDK methods to `AsyncMemoryClient`

**Files:**
- Modify: `sdk/client.py`

- [ ] **Step 1: Write the failing SDK method tests**

Append to `tests/test_onboarding_setup_cli.py`:

```python
# ---------------------------------------------------------------------------
# Fake config API for SDK + CLI tests
# ---------------------------------------------------------------------------
_CONFIG_UNCONFIGURED: dict[str, Any] = {
    "configured": False,
    "onboarding_completed": False,
    "embedding_mode": "openai",
    "embeddings_enabled": False,
    "has_openai_key": False,
    "mcp_key_hint": None,
}

_CONFIG_CONFIGURED: dict[str, Any] = {
    "configured": True,
    "onboarding_completed": True,
    "embedding_mode": "openai",
    "embeddings_enabled": True,
    "has_openai_key": True,
    "mcp_key_hint": "••••abcd",
}

_GENERATED_KEY = "test-secret-key-1234abcd"


def _build_config_api(*, already_configured: bool = False) -> FastAPI:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield

    app = FastAPI(lifespan=_ls)
    app.state.config = dict(
        _CONFIG_CONFIGURED if already_configured else _CONFIG_UNCONFIGURED
    )
    app.state.captured = {}

    @app.get("/config")
    async def get_config():
        return app.state.config

    @app.post("/config/mcp-key/generate")
    async def generate():
        app.state.captured["generate"] = True
        app.state.config = dict(_CONFIG_CONFIGURED)
        return {"api_key": _GENERATED_KEY}

    @app.post("/config/mcp-key/rotate")
    async def rotate():
        app.state.captured["rotate"] = True
        return {"api_key": "rotated-key-9999wxyz"}

    @app.post("/config/openai-key")
    async def set_openai(body: dict[str, Any]):
        app.state.captured["openai_key"] = body
        return {"ok": True}

    @app.post("/config/embedding-mode")
    async def set_mode(body: dict[str, Any]):
        app.state.captured["embedding_mode"] = body
        return {"ok": True}

    @app.post("/config/complete-onboarding")
    async def complete():
        app.state.captured["complete"] = True
        return {"ok": True}

    return app


@pytest.fixture
def config_api() -> FastAPI:
    return _build_config_api(already_configured=False)


@pytest.fixture
def config_api_configured() -> FastAPI:
    return _build_config_api(already_configured=True)


@pytest.fixture
def config_transport(config_api: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=config_api)


@pytest.fixture
def config_transport_configured(
    config_api_configured: FastAPI,
) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=config_api_configured)


# ---------------------------------------------------------------------------
# SDK method tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sdk_get_config_unconfigured(
    config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        res = await c.get_config()
    assert isinstance(res, AppConfigView)
    assert res.configured is False
    assert res.mcp_key_hint is None


@pytest.mark.asyncio
async def test_sdk_get_config_configured(
    config_transport_configured: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(
        base_url="http://t", transport=config_transport_configured
    ) as c:
        res = await c.get_config()
    assert res.configured is True
    assert res.mcp_key_hint == "••••abcd"


@pytest.mark.asyncio
async def test_sdk_generate_mcp_key(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        res = await c.generate_mcp_key()
    assert isinstance(res, KeyResult)
    assert res.api_key == _GENERATED_KEY
    assert config_api.state.captured.get("generate") is True


@pytest.mark.asyncio
async def test_sdk_rotate_mcp_key(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        res = await c.rotate_mcp_key()
    assert isinstance(res, KeyResult)
    assert res.api_key == "rotated-key-9999wxyz"
    assert config_api.state.captured.get("rotate") is True


@pytest.mark.asyncio
async def test_sdk_set_openai_key(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        await c.set_openai_key("sk-live-test-key")
    assert config_api.state.captured["openai_key"] == {"api_key": "sk-live-test-key"}


@pytest.mark.asyncio
async def test_sdk_set_openai_key_clear_with_none(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        await c.set_openai_key(None)
    assert config_api.state.captured["openai_key"] == {"api_key": None}


@pytest.mark.asyncio
async def test_sdk_set_embedding_mode(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        await c.set_embedding_mode("openai")
    assert config_api.state.captured["embedding_mode"] == {"mode": "openai"}


@pytest.mark.asyncio
async def test_sdk_complete_onboarding(
    config_api: FastAPI, config_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=config_transport) as c:
        await c.complete_onboarding()
    assert config_api.state.captured.get("complete") is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "sdk_get_config or sdk_generate or sdk_rotate or sdk_set_openai or sdk_set_embedding or sdk_complete" -v
```

Expected: FAIL — `AttributeError: 'AsyncMemoryClient' object has no attribute 'get_config'`

- [ ] **Step 3: Add the 6 methods to `sdk/client.py`**

Add a new `# ----- config / onboarding -----` section after the `# ----- status / audit -----` section, before the `# ----- internal HTTP plumbing -----` section. The exact insertion point is after the `verify_audit_chain` method (line 289 in the current file) and before the `# ----- internal HTTP plumbing -----` comment:

```python
    # ----- config / onboarding -----
    async def get_config(self) -> AppConfigView:
        """GET /config — onboarding state (unauthenticated, never raw keys)."""
        return AppConfigView.model_validate(await self._get_json("/config"))

    async def generate_mcp_key(self) -> KeyResult:
        """POST /config/mcp-key/generate — generate + store the MCP key once."""
        return KeyResult.model_validate(
            await self._post_json("/config/mcp-key/generate", {})
        )

    async def rotate_mcp_key(self) -> KeyResult:
        """POST /config/mcp-key/rotate — replace the current MCP key."""
        return KeyResult.model_validate(
            await self._post_json("/config/mcp-key/rotate", {})
        )

    async def set_openai_key(self, key: str | None) -> None:
        """POST /config/openai-key — set or clear the OpenAI API key."""
        await self._post_json("/config/openai-key", {"api_key": key})

    async def set_embedding_mode(self, mode: str) -> None:
        """POST /config/embedding-mode — 'openai' | 'local'."""
        await self._post_json("/config/embedding-mode", {"mode": mode})

    async def complete_onboarding(self) -> None:
        """POST /config/complete-onboarding — mark wizard finished."""
        await self._post_json("/config/complete-onboarding", {})
```

Also update the import at the top of `sdk/client.py` — add `AppConfigView` and `KeyResult` to the import from `sdk.types`:

```python
from sdk.types import (
    AppConfigView,
    ExploreResult,
    FindSymbolResult,
    IngestResult,
    KeyResult,
    McpToolResult,
    QueryGraphResult,
    ReadUnitResult,
    ReembedResult,
    ReplayResult,
    RepoOverviewResult,
    ReposResult,
    RetrieveResult,
    SearchCodeResult,
    SnapshotResult,
    StatusResult,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "sdk_get_config or sdk_generate or sdk_rotate or sdk_set_openai or sdk_set_embedding or sdk_complete" -v
```

Expected: 8 passed

- [ ] **Step 5: Full regression check**

```
.venv/bin/pytest tests/test_phase9_sdk_cli.py tests/test_onboarding_setup_cli.py -q
```

Expected: 62 + 10 = 72 passed (2 type tests + 8 SDK tests so far)

- [ ] **Step 6: Commit**

```bash
git add sdk/client.py tests/test_onboarding_setup_cli.py
git commit -m "feat(sdk): add 6 typed methods for config/onboarding endpoints"
```

---

## Task 3: `memcl setup` command

**Files:**
- Modify: `apps/cli/main.py`

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_onboarding_setup_cli.py`:

```python
# ---------------------------------------------------------------------------
# CLI test helpers (mirror test_phase9_sdk_cli.py pattern)
# ---------------------------------------------------------------------------
def _patch_cli_config_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.AsyncBaseTransport,
) -> None:
    """Inject the transport into the CLI's SDK client."""
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)


# ---------------------------------------------------------------------------
# memcl setup — happy path (unconfigured)
# ---------------------------------------------------------------------------
def test_cli_setup_unconfigured_generates_key_and_saves_to_config(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup: generates key, saves it to config.toml, prints connect command."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    # Suppress interactive OpenAI prompt (empty = skip).
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **kw: "")
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["setup"])
    assert rc == 0
    out = capsys.readouterr().out
    # Key was printed.
    assert _GENERATED_KEY in out
    # Connect command was printed.
    assert "claude mcp add" in out
    assert "--transport sse" in out
    assert "X-API-Key" in out
    # Key was saved to config file.
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert _GENERATED_KEY in text
    # complete-onboarding was called.
    assert config_api.state.captured.get("complete") is True


# ---------------------------------------------------------------------------
# memcl setup — already configured
# ---------------------------------------------------------------------------
def test_cli_setup_already_configured_prints_hint(
    config_api_configured: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup: already configured → print masked hint + re-print connect command."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api_configured)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["setup"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Already set up" in out
    assert "••••abcd" in out
    assert "memcl key rotate" in out
    # No new key generated.
    assert config_api_configured.state.captured.get("generate") is None


# ---------------------------------------------------------------------------
# memcl setup — with OpenAI key entered
# ---------------------------------------------------------------------------
def test_cli_setup_with_openai_key_sets_key_and_mode(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup: if user enters OpenAI key, set_openai_key + set_embedding_mode called."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    # Return a key on the first Prompt.ask call.
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **kw: "sk-live-openai-key")
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["setup"])
    assert rc == 0
    assert config_api.state.captured.get("openai_key") == {"api_key": "sk-live-openai-key"}
    assert config_api.state.captured.get("embedding_mode") == {"mode": "openai"}
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "cli_setup" -v
```

Expected: FAIL — `error: argument <command>: invalid choice: 'setup'`

- [ ] **Step 3: Implement `_cmd_setup` in `apps/cli/main.py`**

Add after the `_cmd_graph_legacy` function (around line 577), before the `# config (no client needed)` section:

```python
# ---------------------------------------------------------------------------
# setup / key / config-set commands
# ---------------------------------------------------------------------------
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


def _print_connect_command(ui: UI, base_url: str, api_key: str) -> None:
    """Print the pre-filled claude mcp add command."""
    cmd = (
        f'claude mcp add --transport sse --scope user memory-cl '
        f'{base_url}/mcp/sse --header "X-API-Key: {api_key}"'
    )
    ui.out.print(f"\n[bold]Run this to connect Claude:[/bold]\n  {cmd}\n")
```

- [ ] **Step 4: Add `setup` to `build_parser` in `apps/cli/main.py`**

In `build_parser()`, after the `p_config` block and before the `# ---- deprecated v1 subcommands ----` comment, add:

```python
    p_setup = sub.add_parser(
        "setup", parents=[common],
        help="first-run wizard — generate key, set embeddings, print connect command",
    )
    p_setup.set_defaults(func=_cmd_setup)
```

Also update `test_cli_parser_exposes_all_subcommands` in `tests/test_onboarding_setup_cli.py` (add a parser check test):

```python
def test_cli_parser_has_setup_key_and_config_set() -> None:
    from apps.cli.main import build_parser
    parser = build_parser()
    sub = next(
        a for a in parser._actions  # type: ignore[attr-defined]
        if isinstance(a, type(parser._subparsers._group_actions[0]))  # type: ignore[attr-defined]
    )
    expected_new = {"setup", "key"}
    assert expected_new.issubset(set(sub.choices.keys()))
```

- [ ] **Step 5: Run tests to verify setup tests pass**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "cli_setup" -v
```

Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add apps/cli/main.py tests/test_onboarding_setup_cli.py
git commit -m "feat(cli): add memcl setup interactive first-run wizard"
```

---

## Task 4: `memcl key generate|show|rotate`

**Files:**
- Modify: `apps/cli/main.py`

- [ ] **Step 1: Write the failing CLI key command tests**

Append to `tests/test_onboarding_setup_cli.py`:

```python
# ---------------------------------------------------------------------------
# memcl key generate
# ---------------------------------------------------------------------------
def test_cli_key_generate_saves_to_config_and_prints(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """key generate: saves key to config, prints it."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["key", "generate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert _GENERATED_KEY in out
    assert cfg.is_file()
    assert _GENERATED_KEY in cfg.read_text(encoding="utf-8")


def test_cli_key_generate_4xx_tells_user_to_rotate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """key generate when already configured → 401; friendly error mentions rotate."""
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.get("/config")
    async def cfg():
        return _CONFIG_CONFIGURED

    @app.post("/config/mcp-key/generate")
    async def gen():
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="already configured")

    cfg_path = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg_path))
    _patch_cli_config_transport(monkeypatch, httpx.ASGITransport(app=app))
    from apps.cli.main import main as cli_main
    rc = cli_main(["key", "generate"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "memcl key rotate" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# memcl key show
# ---------------------------------------------------------------------------
def test_cli_key_show_prints_masked_hint(
    config_api_configured: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """key show: prints masked hint from GET /config, never the raw key."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api_configured)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["key", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "••••abcd" in out
    assert _GENERATED_KEY not in out


# ---------------------------------------------------------------------------
# memcl key rotate
# ---------------------------------------------------------------------------
def test_cli_key_rotate_saves_new_key_to_config(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """key rotate: saves new key to config, prints connect command + warning."""
    cfg = tmp_path / "config.toml"
    # Seed config with an existing key so rotate has auth.
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('base_url = "http://localhost:8000"\napi_key = "old-key"\n')
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["key", "rotate"])
    assert rc == 0
    out = capsys.readouterr().out
    # New key printed.
    assert "rotated-key-9999wxyz" in out
    # Connect command printed.
    assert "claude mcp add" in out
    # Warning about agents.
    assert "re-add" in out or "agents" in out
    # New key saved to config file.
    assert "rotated-key-9999wxyz" in cfg.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "cli_key" -v
```

Expected: FAIL — `error: argument <command>: invalid choice: 'key'`

- [ ] **Step 3: Implement key command handlers in `apps/cli/main.py`**

Add after `_cmd_setup` / `_print_connect_command` (still in the setup/key/config-set section):

```python
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
```

- [ ] **Step 4: Add `key` subcommand to `build_parser`**

In `build_parser()`, after the `p_setup` block, add:

```python
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
```

- [ ] **Step 5: Wire `key` dispatch in `_dispatch`**

The key sub-commands set `func` directly on the sub-parser, so they already go through `args.func`. Confirm `_dispatch` already handles this pattern (it does — all handlers use `args.func`). No change needed.

- [ ] **Step 6: Run tests to verify key tests pass**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "cli_key" -v
```

Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add apps/cli/main.py tests/test_onboarding_setup_cli.py
git commit -m "feat(cli): add memcl key generate|show|rotate commands"
```

---

## Task 5: `memcl config set openai-key`

**Files:**
- Modify: `apps/cli/main.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_onboarding_setup_cli.py`:

```python
# ---------------------------------------------------------------------------
# memcl config set openai-key
# ---------------------------------------------------------------------------
def test_cli_config_set_openai_key_calls_api_and_prints_masked(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """config set openai-key: calls POST /config/openai-key, prints masked key."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["config", "set", "openai-key", "sk-live-mykey123"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sk-" in out or "****" in out or "openai" in out.lower()
    assert "sk-live-mykey123" not in out   # must be masked
    assert config_api.state.captured.get("openai_key") == {"api_key": "sk-live-mykey123"}


def test_cli_config_set_openai_key_json_mode(
    config_api: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """config set openai-key --json: emits JSON confirmation."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["config", "set", "openai-key", "sk-live-mykey456", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["hint"] == "****y456"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "config_set_openai" -v
```

Expected: FAIL — `error: argument config_cmd: invalid choice: 'set'`

- [ ] **Step 3: Implement `_cmd_config_set_openai_key` in `apps/cli/main.py`**

This command requires the SDK client, so it can't live in the synchronous `_cmd_config`. Add a new async handler in the setup/key/config-set section:

```python
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
```

- [ ] **Step 4: Add `config set` to `build_parser`**

In `build_parser()`, in the `p_config` / `config_sub` block, after `config_sub.add_parser("show", ...)`, add:

```python
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
```

- [ ] **Step 5: Wire `config set` dispatch in `_dispatch`**

The `_dispatch` function currently handles `config` synchronously. The new `config set` subcommand needs the async client. Update `_dispatch` to detect the `set` sub-subcommand and route it through the async path.

Find the block in `_dispatch`:
```python
    if args.command == "config":
        try:
            return _cmd_config(args, ui, settings)
        except (OSError, ValueError) as exc:
            ui.error(str(exc))
            return 1
```

Replace it with:
```python
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
```

This lets `config set openai-key` fall through to the standard `args.func(client, args, ui, settings)` dispatch path at the bottom of `_dispatch` which already handles async handlers properly.

- [ ] **Step 6: Run tests to verify config set tests pass**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -k "config_set_openai" -v
```

Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add apps/cli/main.py tests/test_onboarding_setup_cli.py
git commit -m "feat(cli): add memcl config set openai-key command"
```

---

## Task 6: Parser subcommand coverage test + full green gates

**Files:**
- Modify: `tests/test_onboarding_setup_cli.py`

- [ ] **Step 1: Add parser coverage test**

Append to `tests/test_onboarding_setup_cli.py`:

```python
def test_cli_parser_has_setup_key_config_set() -> None:
    """All new top-level subcommands are registered."""
    from apps.cli.main import build_parser
    parser = build_parser()
    sub = next(
        a for a in parser._actions  # type: ignore[attr-defined]
        if isinstance(a, type(parser._subparsers._group_actions[0]))  # type: ignore[attr-defined]
    )
    assert "setup" in sub.choices
    assert "key" in sub.choices
```

- [ ] **Step 2: Run entire new test file**

```
.venv/bin/pytest tests/test_onboarding_setup_cli.py -v
```

Expected: all tests pass (2 type + 8 SDK + 3 setup + 5 key + 2 config-set + 1 parser = 21 tests)

- [ ] **Step 3: Run full regression suite**

```
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: no regressions — existing 62 tests still green, new tests pass.

- [ ] **Step 4: Ruff lint**

```
.venv/bin/ruff check apps/cli sdk
```

Expected: no output (clean).

- [ ] **Step 5: Commit**

```bash
git add tests/test_onboarding_setup_cli.py
git commit -m "test(cli): add parser coverage test; all onboarding-setup tests green"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task that covers it |
|-----------------|---------------------|
| SDK `get_config()` | Task 2 |
| SDK `generate_mcp_key()` | Task 2 |
| SDK `rotate_mcp_key()` | Task 2 |
| SDK `set_openai_key(key)` | Task 2 |
| SDK `set_embedding_mode(mode)` | Task 2 |
| SDK `complete_onboarding()` | Task 2 |
| `AppConfigView` type | Task 1 |
| `KeyResult` type | Task 1 |
| `memcl setup` — already configured path | Task 3 |
| `memcl setup` — generate + save key | Task 3 |
| `memcl setup` — OpenAI key prompt | Task 3 |
| `memcl setup` — print connect command | Task 3 |
| `memcl setup` — call complete-onboarding | Task 3 |
| `memcl key generate` | Task 4 |
| `memcl key show` | Task 4 |
| `memcl key rotate` — save to config + print | Task 4 |
| `memcl key generate` — 4xx → friendly error | Task 4 |
| `memcl config set openai-key` | Task 5 |
| `--json` passthrough on all commands | Tasks 3/4/5 |
| Friendly errors, no tracebacks | Tasks 3/4/5 |
| Gates: pytest green | Task 6 |
| Gates: ruff clean | Task 6 |

### Placeholder scan

None found — all steps contain exact code.

### Type consistency

- `AppConfigView` defined in Task 1, used in Task 2 (`get_config` return type) and Task 3/4 CLI handlers — consistent.
- `KeyResult` defined in Task 1, used in Task 2 (`generate_mcp_key`/`rotate_mcp_key`) and Tasks 3/4 — consistent.
- `write_config_file(base_url=..., api_key=..., timeout=...)` signature matches `apps/cli/config.py:53` — consistent.
- `_mask_key` already defined in `apps/cli/main.py:582` — reused in Task 5 without redefinition — consistent.
- `_print_connect_command(ui, base_url, api_key)` defined in Task 3, used in Tasks 3/4 — consistent.
- `_patch_cli_config_transport` defined once in the test file, reused by all CLI tests — consistent.
