from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from apps.cli.main import build_parser
from apps.cli.main import main as cli_main
from sdk import AsyncMemoryClient, MemoryClientError


# =========================================================================
# Fake API the SDK + CLI talk to.
# =========================================================================
def _build_fake_api() -> FastAPI:
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    app = FastAPI(lifespan=_ls)

    @app.post("/ingest")
    async def ingest(body: dict[str, Any]):
        return {
            "repo_id": body["repo_id"],
            "commit_sha": body["commit_sha"],
            "units_collection": f"repo_{body['repo_id']}",
            "metrics": {"units_emitted": 1, "files_walked": 1},
            "failed_files": [],
        }

    @app.post("/ingest/reembed")
    async def reembed(body: dict[str, Any]) -> dict[str, Any]:
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

    @app.post("/mcp/tools/{tool}")
    async def mcp(tool: str, body: dict[str, Any]):
        if tool == "query_graph":
            node = body["node"]
            depth = body.get("depth", 1)
            return {
                "tool": "query_graph",
                "request_id": "rid",
                "status": "success",
                "data": {
                    "node": node,
                    "found": True,
                    "depth": depth,
                    # v2-alias shape: neighbors + directed edges + seed.
                    "neighbors": [
                        {
                            "node_id": "u-nbr",
                            "qualified_name": "pkg.m.helper",
                            "kind": "fn",
                            "file_path": "pkg/m.py",
                            "lines": "1-5",
                            "signature": "def helper()",
                            "snippet": "def helper():",
                            "distance": 1,
                            "relation": "CALLS ->",
                        }
                    ],
                    "edges": [
                        {"src_id": node, "kind": "CALLS", "dst_id": "u-nbr"}
                    ],
                    "seed": {
                        "qualified_name": node,
                        "kind": "fn",
                        "file_path": "pkg/m.py",
                        "lines": "1-5",
                        "signature": "def f()",
                    },
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
                },
                "latency_ms": 0.0,
                "schema_version": "1",
            }
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
            "boot_stages": [],
            "mcp_tool_count": 7,
            "schema_version": "1",
        }

    @app.get("/mcp/tools")
    async def list_tools():
        return {"tools": [
            {"name": "query_graph", "request_schema": "QueryGraphRequest"},
        ]}

    return app


@pytest.fixture
def asgi_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=_build_fake_api())


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
    assert res.mcp_tool_count == 7


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


# =========================================================================
#                              CLI
# =========================================================================
def test_cli_parser_exposes_all_subcommands() -> None:
    parser = build_parser()
    # Argparse stores subparsers under the `command` dest.
    sub = next(
        a for a in parser._actions  # type: ignore[attr-defined]
        if isinstance(a, type(parser._subparsers._group_actions[0]))  # type: ignore[attr-defined]
    )
    expected = {"ingest", "reembed", "query", "graph", "snapshot", "replay", "status"}
    assert expected.issubset(set(sub.choices.keys()))


def test_cli_status_prints_canonical_json(
    asgi_transport: httpx.ASGITransport, capsys: pytest.CaptureFixture, monkeypatch,
) -> None:
    """End-to-end: CLI dispatches into the SDK against the fake API."""
    # Monkey-patch the SDK constructor so the CLI uses our ASGI transport.
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = asgi_transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)

    rc = cli_main(["status"])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["service"] == "memory-cl"
    # Same call again should produce byte-identical stdout (determinism).
    rc2 = cli_main(["status"])
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert captured.out == captured2.out


def test_cli_query_dispatches_through_sdk(
    asgi_transport: httpx.ASGITransport, capsys: pytest.CaptureFixture, monkeypatch,
) -> None:
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = asgi_transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)
    rc = cli_main(["query", "auth flow", "--repo-id", "acme", "--top-k", "3"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repo_id"] == "acme"


def test_cli_reembed_dispatches_through_sdk(
    asgi_transport: httpx.ASGITransport,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient  # type: ignore[attr-defined]

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = asgi_transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)
    rc = cli_main(["reembed", "--repo-id", "acme"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "failed_batches": 0, "repo_id": "acme", "units_embedded": 5,
        "units_total": 5,
    }


def test_cli_emits_structured_error_on_http_failure(
    capsys: pytest.CaptureFixture, monkeypatch,
) -> None:
    """When the SDK raises MemoryClientError, the CLI exits 1 with stderr JSON."""
    @asynccontextmanager
    async def _ls(_app: FastAPI):
        yield
    err_app = FastAPI(lifespan=_ls)

    @err_app.post("/ingest")
    async def boom(_body: dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="oops")

    transport = httpx.ASGITransport(app=err_app)
    from apps.cli import main as cli_module
    real_client = cli_module.AsyncMemoryClient

    class _Patched(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(cli_module, "AsyncMemoryClient", _Patched)
    rc = cli_main(["ingest", "/tmp", "--repo-id", "r"])
    assert rc == 1

    captured = capsys.readouterr()
    err = json.loads(captured.err)
    assert err["error"] == "http"
    assert err["status_code"] == 500
