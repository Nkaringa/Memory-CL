"""Snapshot + replay HTTP surface — thin Phase-9 exposure of Phase-8."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep
from core.observability import get_tracer
from core.reproducibility import ReplayEngine, SystemSnapshotBuilder
from schemas.base import SCHEMA_VERSION

router = APIRouter(prefix="/snapshot", tags=["snapshot"])
_TRACER = get_tracer(__name__)


class BuildSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=128)
    state_version_token: str = Field(default="v0")


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    tenant_id: str
    captured_at: str
    components: dict[str, str]


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(min_length=1)
    expected_output: Any | None = None
    payload: Any | None = None


class ReplayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    matches: bool
    expected_hash: str
    actual_hash: str
    notes: str = ""


@router.post("/build", response_model=SnapshotResponse)
async def build_snapshot(
    req: BuildSnapshotRequest, state: AppStateDep,
) -> SnapshotResponse:
    """Build a snapshot of the current process-local view.

    The snapshot ID is content-derived; passing the same inputs at
    the same state version always yields the same id.
    """
    with _TRACER.start_as_current_span("snapshot.build") as span:
        span.set_attribute("memcl.tenant_id", req.tenant_id)
        span.set_attribute("memcl.state_version_token", req.state_version_token)
        builder = SystemSnapshotBuilder()
        # Phase-9 builds a "boot snapshot" — Phase-8 already provides the
        # full builder for callers wanting deeper projection. Here we emit
        # a deterministic snapshot anchored on the live MCP registry +
        # the schema version + tenant + state token.
        registry = getattr(state, "mcp_registry", None)
        tools = registry.names() if registry is not None else []
        schemas = (
            {t.name: t.request_schema.__name__ for t in registry.all()}
            if registry is not None else {}
        )
        snapshot = builder.build(
            tenant_id=req.tenant_id,
            nodes=[], edges=[],
            embeddings={},
            retrieval_config={
                "schema_version": float(int(SCHEMA_VERSION) or 0),
            },
            mcp_tool_names=tools,
            mcp_request_schemas=schemas,
            state_version_token=req.state_version_token,
            captured_at=datetime.now(UTC),
        )
        span.set_attribute("memcl.snapshot_id", snapshot.snapshot_id)
        span.set_attribute("memcl.mcp_tool_count", len(tools))
        return SnapshotResponse(
            snapshot_id=snapshot.snapshot_id,
            tenant_id=snapshot.tenant_id,
            captured_at=snapshot.captured_at.isoformat(),
            components=snapshot.components.to_payload(),
        )


@router.post("/replay", response_model=ReplayResponse)
async def replay(req: ReplayRequest) -> ReplayResponse:
    """Verify that an `expected_output` is byte-equal to a `payload`.

    Phase-9 exposes a thin replay surface — agents pass the previously
    observed output and what they want to verify. `ReplayEngine`
    hashes both and reports parity.
    """
    if req.payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload is required",
        )

    with _TRACER.start_as_current_span("snapshot.replay") as span:
        span.set_attribute("memcl.snapshot_id", req.snapshot_id)
        span.set_attribute(
            "memcl.has_expected_output", req.expected_output is not None,
        )

        async def _op():
            return req.payload

        from core.reproducibility.system_snapshot import (
            SnapshotComponents,
            SystemSnapshot,
        )
        placeholder = SystemSnapshot(
            snapshot_id=req.snapshot_id,
            tenant_id="",
            captured_at=datetime.now(UTC),
            components=SnapshotComponents(
                graph_state_hash="", embedding_index_hash="",
                retrieval_config_hash="", schema_version=SCHEMA_VERSION,
                mcp_registry_hash="", state_version_token="",
            ),
        )
        result = await ReplayEngine().replay(
            placeholder, _op, expected_output=req.expected_output,
        )
        span.set_attribute("memcl.replay_matches", result.matches)
        return ReplayResponse(
            snapshot_id=result.snapshot_id,
            matches=result.matches,
            expected_hash=result.expected_hash,
            actual_hash=result.actual_hash,
            notes=result.notes,
        )
