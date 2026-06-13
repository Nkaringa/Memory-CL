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
    # Simulate a TTY so the interactive prompt branch is taken.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Return a key on the Prompt.ask call.
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **kw: "sk-live-openai-key")
    _patch_cli_config_transport(
        monkeypatch, httpx.ASGITransport(app=config_api)
    )
    from apps.cli.main import main as cli_main
    rc = cli_main(["setup"])
    assert rc == 0
    assert config_api.state.captured.get("openai_key") == {"api_key": "sk-live-openai-key"}
    assert config_api.state.captured.get("embedding_mode") == {"mode": "openai"}


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


# ---------------------------------------------------------------------------
# Parser coverage
# ---------------------------------------------------------------------------
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
