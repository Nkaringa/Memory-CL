"""Deterministic policy engine.

Policies are pure predicates: `(context: dict) -> PolicyEffect`.
Evaluation walks the policy list in priority order and returns the
first effect that isn't `NEUTRAL`. Default effect is `ALLOW` so
the engine fails-open in the absence of explicit policy.

Determinism is structural: no PRNG, no clock, sorted policy iteration
within a priority band.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEUTRAL = "neutral"  # rule didn't apply — try the next one


@dataclass(frozen=True, slots=True)
class Policy:
    """One deterministic rule."""

    name: str
    priority: int  # lower = evaluated first
    predicate: Callable[[dict[str, Any]], PolicyEffect]
    description: str = ""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: PolicyEffect
    matched_policy: str | None
    reason: str = ""
    trace: tuple[str, ...] = field(default_factory=tuple)


class PolicyEngine:
    """Stateless rules engine — same context → same decision, always.

    Built-in policy helpers below cover the spec's common cases:
        * deny external dependencies
        * restrict MCP tool by role
        * cap ingestion size per tenant
        * enforce retention windows
    Callers can also pass arbitrary `Policy` instances.
    """

    def __init__(self, policies: Sequence[Policy] | None = None) -> None:
        self._policies: list[Policy] = sorted(
            list(policies or []), key=lambda p: (p.priority, p.name),
        )

    @property
    def policies(self) -> tuple[Policy, ...]:
        return tuple(self._policies)

    def add(self, policy: Policy) -> None:
        if any(p.name == policy.name for p in self._policies):
            raise ValueError(f"policy '{policy.name}' already registered")
        self._policies.append(policy)
        self._policies.sort(key=lambda p: (p.priority, p.name))

    def evaluate(self, context: dict[str, Any]) -> PolicyDecision:
        trace: list[str] = []
        for policy in self._policies:
            effect = policy.predicate(context)
            trace.append(f"{policy.name}={effect.value}")
            if effect == PolicyEffect.NEUTRAL:
                continue
            return PolicyDecision(
                effect=effect,
                matched_policy=policy.name,
                reason=policy.description,
                trace=tuple(trace),
            )
        return PolicyDecision(
            effect=PolicyEffect.ALLOW,
            matched_policy=None,
            reason="no policy matched — default allow",
            trace=tuple(trace),
        )


# ---------------------------------------------------------------------------
# Built-in policy factories.
# ---------------------------------------------------------------------------
def deny_external_retrieval(*, priority: int = 10) -> Policy:
    """Reject any retrieval whose target is an EXTERNAL graph node."""
    def _pred(ctx: dict[str, Any]) -> PolicyEffect:
        if ctx.get("action") != "retrieve":
            return PolicyEffect.NEUTRAL
        kind = str(ctx.get("entity_kind", "")).lower()
        if kind == "external":
            return PolicyEffect.DENY
        return PolicyEffect.NEUTRAL
    return Policy(
        name="deny_external_retrieval",
        priority=priority,
        predicate=_pred,
        description="EXTERNAL nodes are not first-class agent context",
    )


def restrict_mcp_tool_by_role(
    *, allowed: dict[str, set[str]], priority: int = 20,
) -> Policy:
    """`allowed` maps role → set of permitted tool names.

    Roles missing from the map are denied; tools missing from the set
    for a present role are denied. Use the empty role `"*"` to grant
    universal access to a tool.
    """
    def _pred(ctx: dict[str, Any]) -> PolicyEffect:
        if ctx.get("action") != "mcp_tool":
            return PolicyEffect.NEUTRAL
        role = str(ctx.get("role", ""))
        tool = str(ctx.get("tool", ""))
        wildcard = allowed.get("*", set())
        if tool in wildcard:
            return PolicyEffect.ALLOW
        permitted = allowed.get(role)
        if permitted is None:
            return PolicyEffect.DENY
        return PolicyEffect.ALLOW if tool in permitted else PolicyEffect.DENY
    return Policy(
        name="restrict_mcp_tool_by_role",
        priority=priority,
        predicate=_pred,
        description="role-based MCP tool access control",
    )


def limit_ingestion_size(*, max_bytes: int, priority: int = 30) -> Policy:
    """Reject `ingest` actions whose `payload_bytes` exceeds `max_bytes`."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    def _pred(ctx: dict[str, Any]) -> PolicyEffect:
        if ctx.get("action") != "ingest":
            return PolicyEffect.NEUTRAL
        size = int(ctx.get("payload_bytes", 0))
        return PolicyEffect.DENY if size > max_bytes else PolicyEffect.NEUTRAL
    return Policy(
        name="limit_ingestion_size",
        priority=priority,
        predicate=_pred,
        description=f"max {max_bytes} bytes per ingest",
    )


def enforce_retention(*, max_age_days: int, priority: int = 40) -> Policy:
    """Deny actions on entities older than the retention window.

    Context must carry `entity_age_days`. Use this as a guardrail
    against accidental queries on archived data.
    """
    if max_age_days <= 0:
        raise ValueError("max_age_days must be > 0")

    def _pred(ctx: dict[str, Any]) -> PolicyEffect:
        age = ctx.get("entity_age_days")
        if age is None:
            return PolicyEffect.NEUTRAL
        return PolicyEffect.DENY if int(age) > max_age_days else PolicyEffect.NEUTRAL
    return Policy(
        name="enforce_retention",
        priority=priority,
        predicate=_pred,
        description=f"reject entities older than {max_age_days} days",
    )


__all__ = [
    "Policy",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEngine",
    "deny_external_retrieval",
    "enforce_retention",
    "limit_ingestion_size",
    "restrict_mcp_tool_by_role",
]
