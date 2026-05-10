"""Audit-log inspection HTTP surface."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from apps.api.dependencies import AppStateDep

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    prev_hash: str
    hash: str
    payload: dict[str, Any]


class AuditTailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_length: int
    entries: list[AuditEntryView]


class AuditVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_length: int
    intact: bool
    error: str = ""
    broken_at_seq: int | None = None


def _resolve_audit_logger(request: Request, state: AppStateDep):
    """Audit logger is attached during lifespan to ``app.state``, NOT to
    the ``AppState`` dataclass. Read from the right place — the previous
    `getattr(state, "audit_logger", None)` always returned None in
    production and made every audit call 503.
    """
    return getattr(request.app.state, "audit_logger", None)


@router.get("/tail", response_model=AuditTailResponse)
async def tail(
    request: Request,
    state: AppStateDep,
    limit: int = Query(default=50, gt=0, le=1000),
) -> AuditTailResponse:
    """Return the most recent `limit` audit entries (deterministic order)."""
    logger = _resolve_audit_logger(request, state)
    if logger is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="audit logger not initialized",
        )
    all_entries = list(logger.store)
    tail_entries = all_entries[-limit:]
    return AuditTailResponse(
        chain_length=len(all_entries),
        entries=[
            AuditEntryView(
                seq=e.seq, prev_hash=e.prev_hash, hash=e.hash,
                payload=dict(e.payload),
            )
            for e in tail_entries
        ],
    )


@router.get("/verify", response_model=AuditVerifyResponse)
async def verify(
    request: Request, state: AppStateDep,
) -> AuditVerifyResponse:
    """Re-walk the audit chain and report whether it's intact."""
    from infra.audit.immutable_log_store import ChainBrokenError

    logger = _resolve_audit_logger(request, state)
    if logger is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="audit logger not initialized",
        )
    try:
        logger.verify()
        return AuditVerifyResponse(
            chain_length=len(logger.store), intact=True,
        )
    except ChainBrokenError as exc:
        return AuditVerifyResponse(
            chain_length=len(logger.store),
            intact=False,
            error=str(exc),
            broken_at_seq=exc.seq,
        )
