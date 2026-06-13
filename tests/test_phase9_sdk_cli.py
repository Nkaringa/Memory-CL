from __future__ import annotations

import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from apps.cli.config import resolve_settings
from apps.cli.main import build_parser, infer_commit_sha, infer_repo_id, resolve_server_path
from apps.cli.main import main as cli_main
from sdk import AsyncMemoryClient, MemoryClientError


# =========================================================================
# Isolation: a stray MEMCL_* env var or a real ~/.memcl/config.toml on the
# dev machine must never leak into these tests.
# =========================================================================
@pytest.fixture(autouse=True)
def _isolate_cli_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    for var in ("MEMCL_BASE_URL", "MEMCL_API_KEY", "MEMCL_TIMEOUT",
                "MEMCL_REQUEST_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEMCL_CONFIG", str(tmp_path / "absent-config.toml"))
    # Wide virtual terminal so rich tables don't truncate cell text.
    monkeypatch.setenv("COLUMNS", "300")


# =========================================================================
# Fake API the SDK + CLI talk to.
# =========================================================================
_SEARCH_DATA: dict[str, Any] = {
    "results": [
        {
            "repo_id": "acme",
            "qualified_name": "pkg.auth.login",
            "kind": "fn",
            "file_path": "pkg/auth.py",
            "lines": "10-42",
            "score": 0.91,
            "channels": ["vector", "graph"],
            "snippet": "def login(user):\n    check(user)\n    return ok\nextra",
            "snippet_truncated": False,
        },
    ],
    "total_matches": 1,
    "truncated": False,
}

_READ_DATA: dict[str, Any] = {
    "found": True,
    "unit_id": "u-1",
    "repo_id": "acme",
    "qualified_name": "pkg.auth.login",
    "kind": "fn",
    "file_path": "pkg/auth.py",
    "lines": "10-42",
    "language": "python",
    "signature": "def login(user)",
    "docstring": None,
    "imports": [],
    "calls": [],
    "bases": [],
    "content": "def login(user):\n    return ok\n",
    "truncated": False,
    "parent_chain": [{"qualified_name": "pkg.auth", "kind": "mod"}],
}

_EXPLORE_DATA: dict[str, Any] = {
    "found": True,
    "seed": {
        "qualified_name": "pkg.auth.login",
        "kind": "fn",
        "file_path": "pkg/auth.py",
        "lines": "10-42",
        "signature": "def login(user)",
    },
    "direction": "all",
    "depth": 1,
    "neighbors": [
        {
            "node_id": "u-nbr",
            "qualified_name": "pkg.auth.check",
            "kind": "fn",
            "file_path": "pkg/auth.py",
            "lines": "50-60",
            "signature": "def check(user)",
            "snippet": "def check(user):",
            "distance": 1,
            "relation": "CALLS ->",
        },
    ],
    "edges": [{"src_id": "u-1", "kind": "CALLS", "dst_id": "u-nbr"}],
    "truncated": False,
    "truncated_edges": False,
}

_SYMBOLS_DATA: dict[str, Any] = {
    "matches": [
        {
            "repo_id": "acme",
            "qualified_name": "pkg.auth.login",
            "kind": "fn",
            "file_path": "pkg/auth.py",
            "lines": "10-42",
            "unit_id": "u-1",
        },
    ],
    "truncated": False,
}

_OVERVIEW_DATA: dict[str, Any] = {
    "found": True,
    "repo_id": "acme",
    "units": 5,
    "files": 2,
    "languages": {"python": 5},
    "unit_kinds": {"fn": 3, "mod": 2},
    "module_tree": [{"name": "pkg", "units": 5, "modules": ["pkg.auth"]}],
    "largest_modules": [
        {"qualified_name": "pkg.auth", "units": 4, "file_path": "pkg/auth.py"},
    ],
    "most_connected": [
        {"qualified_name": "pkg.auth.login", "kind": "fn",
         "file_path": "pkg/auth.py", "connections": 3},
    ],
    "doc_files": ["README.md"],
}

_REPOS_PAYLOAD: dict[str, Any] = {
    "schema_version": "1",
    "repos": [
        {"repo_id": "acme", "units": 5, "files": 2, "languages": ["python"]},
    ],
}


def _envelope(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": tool, "request_id": "rid", "status": "success",
        "data": data, "latency_ms": 0.0, "schema_version": "1",
    }


def _build_fake_api() -> FastAPI:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)
    app.state.captured = {}

    @app.post("/ingest")
    async def ingest(body: dict[str, Any]):
        app.state.captured["ingest"] = body
        return {
            "repo_id": body["repo_id"],
            "commit_sha": body["commit_sha"],
            "units_collection": f"repo_{body['repo_id']}",
            "metrics": {"units_emitted": 1, "files_walked": 1},
            "failed_files": [],
        }

    @app.post("/ingest/reembed")
    async def reembed(body: dict[str, Any]) -> dict[str, Any]:
        app.state.captured["reembed"] = body
        return {
            "repo_id": body["repo_id"],
            "units_total": 5,
            "units_embedded": 5,
            "failed_batches": 0,
        }

    @app.post("/retrieve")
    async def retrieve(body: dict[str, Any]):
        return {
            "query_id": "qid",
            "repo_id": body["repo_id"],
            "packet": {"task": body["text"], "context": [], "risks": [],
                       "constraints": [], "changes": [], "confidence": 0.0,
                       "schema_version": "1"},
            "graph_hits": 0,
            "vector_hits": 0,
            "metadata_hits": 0,
            "final_candidates": 0,
            "ranked_count": 0,
            "failed_channels": [],
            "latency_ms": 0.0,
        }

    @app.get("/repos")
    async def repos():
        return _REPOS_PAYLOAD

    @app.post("/mcp/tools/{tool}")
    async def mcp(tool: str, body: dict[str, Any]):
        app.state.captured[f"mcp:{tool}"] = body
        if tool == "search_code":
            return _envelope(tool, _SEARCH_DATA)
        if tool == "read_unit":
            return _envelope(tool, _READ_DATA)
        if tool == "explore":
            return _envelope(tool, _EXPLORE_DATA)
        if tool == "find_symbol":
            return _envelope(tool, _SYMBOLS_DATA)
        if tool == "repo_overview":
            return _envelope(tool, _OVERVIEW_DATA)
        if tool == "list_repos":
            return _envelope(tool, {"repos": _REPOS_PAYLOAD["repos"]})
        if tool == "query_graph":
            node = body["node"]
            depth = body.get("depth", 1)
            return _envelope(tool, {
                "node": node,
                "found": True,
                "depth": depth,
                "neighbors": _EXPLORE_DATA["neighbors"],
                "edges": [
                    {"src_id": node, "kind": "CALLS", "dst_id": "u-nbr"}
                ],
                "seed": _EXPLORE_DATA["seed"],
                "direction": "all",
                "truncated": False,
                "truncated_edges": False,
                "deprecated": "use explore",
                # v1 compat key — what the SDK reads via candidates[].unit_id
                "candidates": [
                    {
                        "unit_id": "u-nbr",
                        "qualified_name": "pkg.m.helper",
                        "kind": "fn",
                        "file_path": "pkg/m.py",
                    }
                ],
            })
        return {
            "tool": tool, "request_id": "rid", "status": "failed",
            "error": "unknown_tool", "error_code": "unknown_tool",
            "data": {}, "latency_ms": 0.0, "schema_version": "1",
        }

    @app.post("/snapshot/build")
    async def snap_build(body: dict[str, Any]):
        return {
            "snapshot_id": "snap-abc",
            "tenant_id": body["tenant_id"],
            "captured_at": "2026-05-08T00:00:00+00:00",
            "components": {"graph_state_hash": "0", "embedding_index_hash": "0",
                           "retrieval_config_hash": "0", "schema_version": "1",
                           "mcp_registry_hash": "0", "state_version_token": "v0"},
        }

    @app.post("/snapshot/replay")
    async def snap_replay(body: dict[str, Any]):
        return {
            "snapshot_id": body["snapshot_id"],
            "matches": True,
            "expected_hash": "h1",
            "actual_hash": "h1",
            "notes": "",
        }

    @app.get("/status")
    async def status():
        return {
            "service": "memory-cl",
            "environment": "development",
            "safe_mode": {"enabled": False, "reason": "", "triggered_by": ""},
            "feature_flags": [],
            "boot_overall_ok": True,
            "boot_failed_stages": [],
            "boot_degraded_stages": [],
            "boot_stages": [
                {"name": "postgres", "order": 1, "status": "ok", "error": ""},
            ],
            "mcp_tool_count": 14,
            "schema_version": "1",
            "embeddings_enabled": True,
        }

    @app.get("/mcp/tools")
    async def list_tools():
        return {"tools": [
            {"name": "query_graph", "request_schema": "QueryGraphRequest"},
        ]}

    return app


@pytest.fixture
def fake_api() -> FastAPI:
    return _build_fake_api()


@pytest.fixture
def asgi_transport(fake_api: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=fake_api)


def _patch_cli_transport(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport,
) -> None:
    """Make the CLI's SDK client speak to the fake API (or a failing one)."""
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)


def _raising_transport(exc_factory: Any) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)
    return httpx.MockTransport(handler)


# =========================================================================
#                              SDK
# =========================================================================
@pytest.mark.asyncio
async def test_sdk_ingest_repository(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.ingest_repository(
            repo_id="acme", repo_path="/tmp", commit_sha="abc",
        )
    assert res.repo_id == "acme"
    assert res.units_collection == "repo_acme"


@pytest.mark.asyncio
async def test_sdk_reembed_repository(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.reembed_repository(repo_id="acme")
    assert res.repo_id == "acme"
    assert res.units_total == 5
    assert res.units_embedded == 5
    assert res.failed_batches == 0


@pytest.mark.asyncio
async def test_sdk_retrieve(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.retrieve(text="auth", repo_id="acme", top_k=3)
    assert res.repo_id == "acme"
    assert res.packet["task"] == "auth"


@pytest.mark.asyncio
async def test_sdk_query_graph_unwraps_mcp_tool_response(
    asgi_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.query_graph(node="pkg.m.f", repo_id="acme", depth=2)
    assert res.found
    assert res.depth == 2
    # v1 compat: SDK can read candidates[].unit_id.
    assert len(res.candidates) == 1
    assert res.candidates[0]["unit_id"] == "u-nbr"


@pytest.mark.asyncio
async def test_sdk_run_mcp_tool_returns_full_envelope(
    asgi_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.run_mcp_tool(
            tool="query_graph", payload={"node": "x", "repo_id": "acme"},
        )
    assert res.status == "success"
    assert res.data["found"] is True


@pytest.mark.asyncio
async def test_sdk_get_status(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.get_status()
    assert res.service == "memory-cl"
    assert res.boot_overall_ok is True
    assert res.mcp_tool_count == 14
    assert res.embeddings_enabled is True


@pytest.mark.asyncio
async def test_sdk_snapshot_and_replay_round_trip(
    asgi_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        snap = await c.get_snapshot(tenant_id="acme")
        rep = await c.replay_snapshot(
            snapshot_id=snap.snapshot_id, payload={"x": 1}, expected_output={"x": 1},
        )
    assert snap.snapshot_id == "snap-abc"
    assert rep.matches is True


@pytest.mark.asyncio
async def test_sdk_raises_on_non_2xx() -> None:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.post("/ingest")
    async def boom(_body: dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="bad input")

    async with AsyncMemoryClient(
        base_url="http://t", transport=httpx.ASGITransport(app=app),
    ) as c:
        with pytest.raises(MemoryClientError) as ei:
            await c.ingest_repository(
                repo_id="r", repo_path="/x", commit_sha="c",
            )
    assert ei.value.status_code == 400


# ----- v2 typed wrappers -----
@pytest.mark.asyncio
async def test_sdk_search_code(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.search_code(question="where is login?", repo_id="acme")
    assert res.total_matches == 1
    hit = res.results[0]
    assert hit.qualified_name == "pkg.auth.login"
    assert hit.score == 0.91
    assert hit.channels == ["vector", "graph"]


@pytest.mark.asyncio
async def test_sdk_read_unit(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.read_unit(reference="pkg.auth.login", repo_id="acme")
    assert res.found is True
    assert "def login" in res.content
    assert res.parent_chain[0]["qualified_name"] == "pkg.auth"


@pytest.mark.asyncio
async def test_sdk_explore(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.explore(qualified_name="pkg.auth.login", repo_id="acme")
    assert res.found is True
    assert res.neighbors[0].qualified_name == "pkg.auth.check"
    assert res.neighbors[0].relation == "CALLS ->"


@pytest.mark.asyncio
async def test_sdk_find_symbol(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.find_symbol(query="login", repo_id="acme")
    assert res.matches[0].qualified_name == "pkg.auth.login"
    assert res.matches[0].lines == "10-42"


@pytest.mark.asyncio
async def test_sdk_repo_overview(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.repo_overview(repo_id="acme")
    assert res.found is True
    assert res.languages == {"python": 5}
    assert res.module_tree[0]["name"] == "pkg"


@pytest.mark.asyncio
async def test_sdk_get_repos(asgi_transport: httpx.ASGITransport) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.get_repos()
    assert [r.repo_id for r in res.repos] == ["acme"]
    assert res.repos[0].units == 5


@pytest.mark.asyncio
async def test_sdk_v2_wrapper_raises_on_tool_failure(
    asgi_transport: httpx.ASGITransport,
) -> None:
    """Unknown tools come back status=failed → typed wrapper raises."""
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        with pytest.raises(MemoryClientError):
            await c._run_tool_data("not_a_tool", {})


# =========================================================================
#                              CLI — parser
# =========================================================================
def test_cli_parser_exposes_all_subcommands() -> None:
    parser = build_parser()
    sub = next(
        a for a in parser._actions  # type: ignore[attr-defined]
        if isinstance(a, type(parser._subparsers._group_actions[0]))  # type: ignore[attr-defined]
    )
    expected = {
        # v2 surface
        "ingest", "repos", "search", "read", "explore", "symbols",
        "overview", "status", "doctor", "reembed", "snapshot", "config",
        # kept v1 spellings
        "query", "graph", "replay",
    }
    assert expected.issubset(set(sub.choices.keys()))


# =========================================================================
#                              CLI — inference
# =========================================================================
def test_infer_repo_id_uses_directory_basename(tmp_path: Path) -> None:
    repo = tmp_path / "my-service"
    repo.mkdir()
    assert infer_repo_id(str(repo)) == "my-service"


def test_infer_commit_sha_from_git(tmp_path: Path) -> None:
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "x", "-q"],
        cwd=repo, check=True,
    )
    sha = infer_commit_sha(str(repo))
    assert len(sha) == 40 and sha != "manual"


def test_infer_commit_sha_falls_back_to_manual(tmp_path: Path) -> None:
    plain = tmp_path / "no-git"
    plain.mkdir()
    assert infer_commit_sha(str(plain)) == "manual"


def test_resolve_server_path() -> None:
    # already a server path → untouched, no mapping notice
    assert resolve_server_path("/repos/acme", None) == ("/repos/acme", False)
    # explicit override wins
    assert resolve_server_path("/Users/me/acme", "/repos/x") == ("/repos/x", False)
    # local path → mapped to /repos/<basename>, flagged
    mapped, was_mapped = resolve_server_path("/Users/me/acme", None)
    assert mapped == "/repos/acme" and was_mapped is True


def test_cli_ingest_infers_repo_id_and_commit(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "inferred-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "x", "-q"],
        cwd=repo, check=True,
    )
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["ingest", str(repo), "--json"])
    assert rc == 0
    body = fake_api.state.captured["ingest"]
    assert body["repo_id"] == "inferred-repo"
    assert len(body["commit_sha"]) == 40
    # local path mapped onto the server's /repos/<name> convention
    assert body["repo_path"] == "/repos/inferred-repo"
    payload = json.loads(capsys.readouterr().out)
    assert payload["repo_id"] == "inferred-repo"


def test_cli_ingest_explains_server_path_model(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "localonly"
    repo.mkdir()
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["ingest", str(repo)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "ITS OWN filesystem" in err
    assert "rsync -av" in err
    assert "/repos/localonly" in err


def test_cli_ingest_server_path_override(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main([
        "ingest", ".", "--repo-id", "acme",
        "--server-path", "/repos/elsewhere", "--json",
    ])
    assert rc == 0
    body = fake_api.state.captured["ingest"]
    assert body["repo_path"] == "/repos/elsewhere"
    assert body["repo_id"] == "acme"


def test_cli_ingest_old_spelling_still_works(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """v1: memcl ingest /repos/X --repo-id X --commit-sha Y."""
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main([
        "ingest", "/repos/acme", "--repo-id", "acme",
        "--commit-sha", "abc123", "--json",
    ])
    assert rc == 0
    body = fake_api.state.captured["ingest"]
    assert body == {
        "repo_id": "acme", "repo_path": "/repos/acme", "commit_sha": "abc123",
    }


def test_cli_ingest_timeout_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(
        monkeypatch,
        _raising_transport(
            lambda req: httpx.ReadTimeout("slow", request=req)
        ),
    )
    rc = cli_main(["ingest", "/repos/acme"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "still ingesting" in err
    assert "memcl repos" in err
    assert "Traceback" not in err


# =========================================================================
#                              CLI — v2 commands (happy paths)
# =========================================================================
def test_cli_repos_renders_table(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["repos"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "acme" in out and "python" in out and "5" in out


def test_cli_search_renders_ranked_results(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["search", "where is login?", "-r", "acme", "-k", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pkg.auth.login" in out
    assert "0.91" in out
    assert "pkg/auth.py:10-42" in out
    assert "def login(user):" in out          # snippet preview
    assert "extra" not in out                 # only first 3 snippet lines


def test_cli_search_json_passthrough(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["search", "where is login?", "-r", "acme", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == _SEARCH_DATA
    assert fake_api.state.captured["mcp:search_code"] == {
        "question": "where is login?", "repo_id": "acme", "top_k": 8,
    }


def test_cli_read_prints_header_and_content(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["read", "pkg.auth.login", "-r", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pkg/auth.py:10-42" in out
    assert "def login(user):" in out
    assert "return ok" in out


def test_cli_explore_renders_neighbors(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["explore", "pkg.auth.login", "-r", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pkg.auth.check" in out
    assert "CALLS ->" in out
    assert "def check(user)" in out


def test_cli_explore_infers_sole_repo(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Exactly one repo ingested → -r may be omitted."""
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["explore", "pkg.auth.login"])
    assert rc == 0
    assert fake_api.state.captured["mcp:explore"]["repo_id"] == "acme"


def test_cli_symbols_renders_table(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["symbols", "login", "-r", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pkg.auth.login" in out
    assert "pkg/auth.py:10-42" in out


def test_cli_overview_renders_languages_and_modules(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["overview", "acme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "5 units" in out
    assert "python" in out
    assert "pkg" in out
    assert "README.md" in out


def test_cli_status_humanized(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "memory-cl" in out
    assert "boot ok" in out
    assert "postgres" in out
    assert "14 MCP tools" in out


def test_cli_status_json_is_canonical(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--json keeps v1's byte-identical canonical JSON contract."""
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["status", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["service"] == "memory-cl"
    rc2 = cli_main(["status", "--json"])
    assert rc2 == 0
    assert captured.out == capsys.readouterr().out


def test_cli_doctor_all_green(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["--base-url", "http://t", "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    for check in ("config", "server", "auth", "embeddings", "repos"):
        assert check in out


def test_cli_doctor_unreachable_server(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(
        monkeypatch,
        _raising_transport(
            lambda req: httpx.ConnectError("refused", request=req)
        ),
    )
    rc = cli_main(["doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "can't reach" in out
    assert "fix:" in out


def test_cli_reembed_positional_repo(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["reembed", "acme"])
    assert rc == 0
    assert fake_api.state.captured["reembed"] == {"repo_id": "acme"}
    assert "5/5" in capsys.readouterr().out


def test_cli_reembed_old_flag_spelling_and_json(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["reembed", "--repo-id", "acme", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "failed_batches": 0, "repo_id": "acme", "units_embedded": 5,
        "units_total": 5,
    }


def test_cli_snapshot_build_and_replay(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["snapshot", "build", "--tenant-id", "acme"])
    assert rc == 0
    assert "snap-abc" in capsys.readouterr().out

    rc = cli_main([
        "snapshot", "replay", "snap-abc", "--payload", '{"x": 1}',
    ])
    assert rc == 0
    assert "matched" in capsys.readouterr().out


def test_cli_snapshot_legacy_spelling(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """v1: memcl snapshot --tenant-id X / memcl replay <id> --payload …"""
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["snapshot", "--tenant-id", "acme", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["snapshot_id"] == "snap-abc"

    rc = cli_main(["replay", "snap-abc", "--payload", '{"x": 1}', "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["matches"] is True
    assert "deprecated" in captured.err


# =========================================================================
#                              CLI — deprecated aliases
# =========================================================================
def test_cli_query_aliases_to_search_with_notice(
    asgi_transport: httpx.ASGITransport, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["query", "auth flow", "--repo-id", "acme", "--top-k", "3"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "memcl search" in captured.err
    assert "pkg.auth.login" in captured.out


def test_cli_graph_aliases_to_explore_with_notice(
    fake_api: FastAPI, asgi_transport: httpx.ASGITransport,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(monkeypatch, asgi_transport)
    rc = cli_main(["graph", "pkg.auth.login", "--repo-id", "acme", "--depth", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "memcl explore" in captured.err
    assert "pkg.auth.check" in captured.out
    assert fake_api.state.captured["mcp:explore"]["depth"] == 2


# =========================================================================
#                              CLI — errors (no tracebacks)
# =========================================================================
def test_cli_connection_refused_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cli_transport(
        monkeypatch,
        _raising_transport(
            lambda req: httpx.ConnectError("refused", request=req)
        ),
    )
    rc = cli_main(["--base-url", "http://127.0.0.1:9", "repos"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Can't reach http://127.0.0.1:9" in err
    assert "memcl doctor" in err
    assert "Traceback" not in err


def test_cli_401_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.post("/mcp/tools/{tool}")
    async def reject(tool: str, _body: dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="invalid API key")

    _patch_cli_transport(monkeypatch, httpx.ASGITransport(app=app))
    rc = cli_main(["search", "x", "-r", "acme"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "API key rejected" in err
    assert "MEMCL_API_KEY" in err
    assert "memcl config init" in err
    assert "Traceback" not in err


def test_cli_json_mode_emits_structured_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """--json keeps v1's machine-readable error contract on stderr."""
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    err_app = FastAPI(lifespan=_ls)

    @err_app.get("/status")
    async def boom():
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="oops")

    _patch_cli_transport(monkeypatch, httpx.ASGITransport(app=err_app))
    rc = cli_main(["status", "--json"])
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "http"
    assert err["status_code"] == 500


def test_cli_read_miss_renders_suggestions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.post("/mcp/tools/read_unit")
    async def miss(_body: dict):
        return _envelope("read_unit", {
            "found": False,
            "reference": "pkg.auth.loginn",
            "suggestions": [{"qualified_name": "pkg.auth.login", "kind": "fn"}],
            "hint": "No unit matched.",
        })

    _patch_cli_transport(monkeypatch, httpx.ASGITransport(app=app))
    rc = cli_main(["read", "pkg.auth.loginn", "-r", "acme"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pkg.auth.login" in err
    assert "Traceback" not in err


# =========================================================================
#                              CLI — config
# =========================================================================
def test_config_precedence_flags_env_file_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'base_url = "http://from-file:1"\n'
        'api_key = "file-key"\n'
        "timeout = 11\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))

    # file beats defaults
    s = resolve_settings()
    assert s.base_url_value == "http://from-file:1"
    assert s.base_url.source == "config"
    assert s.api_key_value == "file-key"
    assert s.timeout_value == 11.0

    # env beats file
    monkeypatch.setenv("MEMCL_BASE_URL", "http://from-env:1")
    s = resolve_settings()
    assert s.base_url_value == "http://from-env:1"
    assert s.base_url.source == "env"
    assert s.api_key_value == "file-key"  # untouched layer still wins

    # flag beats env
    s = resolve_settings(base_url_flag="http://from-flag:1")
    assert s.base_url_value == "http://from-flag:1"
    assert s.base_url.source == "flag"


def test_cli_config_init_writes_file_and_show_reads_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MEMCL_CONFIG", str(cfg))
    rc = cli_main([
        "--base-url", "http://homelab:8000", "--api-key", "secret-key-1234",
        "config", "init",
    ])
    assert rc == 0
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert 'base_url = "http://homelab:8000"' in text
    assert 'api_key = "secret-key-1234"' in text
    capsys.readouterr()

    rc = cli_main(["config", "show", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["base_url"] == "http://homelab:8000"
    assert payload["base_url_source"] == "config"
    # the key never appears in clear text in `show`
    assert payload["api_key"] == "****1234"


def test_cli_no_repos_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.get("/repos")
    async def empty():
        return {"schema_version": "1", "repos": []}

    _patch_cli_transport(monkeypatch, httpx.ASGITransport(app=app))
    rc = cli_main(["repos"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "No repositories ingested yet" in captured.out
    assert "memcl ingest" in captured.err  # the hint
