"""Access-control surface — composes TenantManager + PolicyEngine.

A single `AccessControl.check(...)` call:
    1. resolves the request's tenant ↔ repo ownership
    2. evaluates the configured policy engine
    3. returns a structured `AccessDecision`
    4. emits an audit_event via the supplied logger (if any)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.governance.audit_logger import AuditAction, AuditActor, AuditLogger
from core.governance.policy_engine import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
)
from core.governance.tenant_manager import (
    CrossTenantAccessError,
    TenantManager,
    TenantNotFoundError,
)


@dataclass(frozen=True, slots=True)
class AccessRequest:
    actor: AuditActor
    role: str
    tenant_id: str
    repo_id: str
    action: str
    entity_id: str = ""
    entity_kind: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    matched_policy: str | None
    tenant_id: str
    trace: tuple[str, ...]


class AccessControl:
    """Stateless gate — pure function over tenants + policies + request."""

    def __init__(
        self,
        *,
        tenants: TenantManager,
        policies: PolicyEngine,
        audit: AuditLogger | None = None,
    ) -> None:
        self._tenants = tenants
        self._policies = policies
        self._audit = audit

    def check(self, request: AccessRequest) -> AccessDecision:
        # 1. Tenant ↔ repo ownership.
        try:
            self._tenants.assert_owns_repo(
                tenant_id=request.tenant_id, repo_id=request.repo_id,
            )
        except (CrossTenantAccessError, TenantNotFoundError) as exc:
            decision = AccessDecision(
                allowed=False,
                reason=str(exc),
                matched_policy="tenant_ownership",
                tenant_id=request.tenant_id,
                trace=(f"tenant_ownership=deny:{type(exc).__name__}",),
            )
            self._audit_decision(request, decision)
            return decision

        # 2. Policy evaluation.
        ctx: dict[str, Any] = {
            "actor": request.actor.value,
            "role": request.role,
            "tenant_id": request.tenant_id,
            "repo_id": request.repo_id,
            "action": request.action,
            "entity_id": request.entity_id,
            "entity_kind": request.entity_kind,
        }
        if request.metadata:
            ctx.update(request.metadata)
        result: PolicyDecision = self._policies.evaluate(ctx)

        decision = AccessDecision(
            allowed=result.effect == PolicyEffect.ALLOW,
            reason=result.reason,
            matched_policy=result.matched_policy,
            tenant_id=request.tenant_id,
            trace=("tenant_ownership=allow", *result.trace),
        )
        self._audit_decision(request, decision)
        return decision

    def _audit_decision(
        self, request: AccessRequest, decision: AccessDecision,
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            actor=request.actor,
            action=AuditAction.POLICY_DECIDE,
            entity_id=request.entity_id or request.repo_id,
            tenant_id=request.tenant_id,
            before_hash="",
            after_hash="",
            metadata={
                "request_action": request.action,
                "allowed": decision.allowed,
                "matched_policy": decision.matched_policy or "",
                "reason": decision.reason,
                "role": request.role,
            },
            level="info" if decision.allowed else "warning",
        )


__all__ = ["AccessControl", "AccessDecision", "AccessRequest"]
