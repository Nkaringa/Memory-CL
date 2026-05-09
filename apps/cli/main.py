"""Memory-CL CLI — `memcl` console script.

Subcommands map 1:1 to SDK methods. Output is canonical JSON
(sorted keys, compact separators) so two identical runs produce
byte-identical stdout — required by the spec's deterministic-output
rule.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from sdk import AsyncMemoryClient, MemoryClientError


def _emit(payload: Any) -> None:
    """Stable JSON to stdout."""
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


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------
async def _cmd_ingest(client: AsyncMemoryClient, args: argparse.Namespace) -> int:
    res = await client.ingest_repository(
        repo_id=args.repo_id, repo_path=args.repo, commit_sha=args.commit_sha,
    )
    _emit(res.model_dump(mode="json"))
    return 0


async def _cmd_query(client: AsyncMemoryClient, args: argparse.Namespace) -> int:
    res = await client.retrieve(
        text=args.text, repo_id=args.repo_id, top_k=args.top_k,
        seed_unit_ids=list(args.seed_unit_ids or []),
        unit_kinds=list(args.unit_kinds or []),
    )
    _emit(res.model_dump(mode="json"))
    return 0


async def _cmd_graph(client: AsyncMemoryClient, args: argparse.Namespace) -> int:
    res = await client.query_graph(
        node=args.node, repo_id=args.repo_id, depth=args.depth,
    )
    _emit(res.model_dump(mode="json"))
    return 0


async def _cmd_snapshot(client: AsyncMemoryClient, args: argparse.Namespace) -> int:
    res = await client.get_snapshot(
        tenant_id=args.tenant_id, state_version_token=args.state_version,
    )
    _emit(res.model_dump(mode="json"))
    return 0


async def _cmd_replay(client: AsyncMemoryClient, args: argparse.Namespace) -> int:
    payload: Any = json.loads(args.payload) if args.payload else None
    expected: Any = json.loads(args.expected) if args.expected else None
    res = await client.replay_snapshot(
        snapshot_id=args.snapshot_id, payload=payload, expected_output=expected,
    )
    _emit(res.model_dump(mode="json"))
    return 0


async def _cmd_status(client: AsyncMemoryClient, _args: argparse.Namespace) -> int:
    res = await client.get_status()
    _emit(res.model_dump(mode="json"))
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memcl",
        description="Memory-CL CLI — thin wrapper over the AsyncMemoryClient SDK",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("MEMCL_BASE_URL", "http://localhost:8000"),
        help="Memory-CL service base URL (env: MEMCL_BASE_URL)",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("MEMCL_API_KEY"),
        help="MCP API key (env: MEMCL_API_KEY)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("MEMCL_TIMEOUT", "30")),
        help="Request timeout in seconds",
    )
    p.add_argument(
        "--request-id",
        default=os.environ.get("MEMCL_REQUEST_ID"),
        help=(
            "X-Request-ID to send with every API call (env: MEMCL_REQUEST_ID). "
            "Useful for correlating CLI invocations with API logs / traces. "
            "Defaults to a fresh uuid4 per request."
        ),
    )

    sub = p.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a repository")
    p_ingest.add_argument("repo", help="Absolute path to the repo")
    p_ingest.add_argument("--repo-id", required=True)
    p_ingest.add_argument("--commit-sha", default="manual")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_query = sub.add_parser("query", help="Run hybrid retrieval")
    p_query.add_argument("text", help="Query text")
    p_query.add_argument("--repo-id", required=True)
    p_query.add_argument("--top-k", type=int, default=10)
    p_query.add_argument("--seed-unit-ids", nargs="*", default=[])
    p_query.add_argument("--unit-kinds", nargs="*", default=[])
    p_query.set_defaults(func=_cmd_query)

    p_graph = sub.add_parser("graph", help="Bounded graph traversal")
    p_graph.add_argument("node", help="qualified_name or unit_id")
    p_graph.add_argument("--repo-id", required=True)
    p_graph.add_argument("--depth", type=int, default=1)
    p_graph.set_defaults(func=_cmd_graph)

    p_snap = sub.add_parser("snapshot", help="Build a system snapshot")
    p_snap.add_argument("--tenant-id", required=True)
    p_snap.add_argument("--state-version", default="v0")
    p_snap.set_defaults(func=_cmd_snapshot)

    p_replay = sub.add_parser("replay", help="Verify a payload against a snapshot")
    p_replay.add_argument("snapshot_id", help="Snapshot id")
    p_replay.add_argument("--payload", required=True,
                          help="JSON payload to replay")
    p_replay.add_argument("--expected", default=None,
                          help="JSON expected_output for byte-equality check")
    p_replay.set_defaults(func=_cmd_replay)

    p_status = sub.add_parser("status", help="Print system status JSON")
    p_status.set_defaults(func=_cmd_status)

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> int:
    async with AsyncMemoryClient(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout_seconds=args.timeout,
        request_id=args.request_id,
    ) as client:
        try:
            return await args.func(client, args)
        except MemoryClientError as exc:
            _emit_error({
                "error": "http",
                "status_code": exc.status_code,
                "url": exc.url,
                "body": exc.body,
            })
            return 1


__all__ = ["build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
