from __future__ import annotations

import pytest

from core.governance import (
    AccessControl,
    AccessRequest,
    AuditActor,
    CrossTenantAccessError,
    Policy,
    PolicyEffect,
    PolicyEngine,
    Tenant,
    TenantManager,
    TenantNotFoundError,
    deny_external_retrieval,
    enforce_retention,
    limit_ingestion_size,
    restrict_mcp_tool_by_role,
)


# ---- TenantManager --------------------------------------------------------
def test_tenant_manager_assigns_repos_to_owners() -> None:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme", name="ACME"))
    tm.assign_repo(tenant_id="acme", repo_id="repo-1")
    assert tm.tenant_for_repo("repo-1") == "acme"
    assert tm.list_repos("acme") == ("repo-1",)


def test_tenant_manager_blocks_cross_tenant_repo_steal() -> None:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme", name="A"))
    tm.register_tenant(Tenant(tenant_id="enemy", name="E"))
    tm.assign_repo(tenant_id="acme", repo_id="repo-1")
    with pytest.raises(CrossTenantAccessError):
        tm.assign_repo(tenant_id="enemy", repo_id="repo-1")


def test_tenant_manager_assert_owns_repo() -> None:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme", name="A"))
    tm.assign_repo(tenant_id="acme", repo_id="repo-1")
    # OK
    tm.assert_owns_repo(tenant_id="acme", repo_id="repo-1")
    # Wrong tenant
    with pytest.raises(CrossTenantAccessError):
        tm.assert_owns_repo(tenant_id="other", repo_id="repo-1")
    # Unknown repo
    with pytest.raises(TenantNotFoundError):
        tm.assert_owns_repo(tenant_id="acme", repo_id="ghost")


def test_tenant_manager_rejects_duplicate_registration() -> None:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme", name="A"))
    with pytest.raises(ValueError):
        tm.register_tenant(Tenant(tenant_id="acme", name="B"))


# ---- PolicyEngine ---------------------------------------------------------
def test_policy_engine_default_allow_when_no_policies() -> None:
    decision = PolicyEngine().evaluate({})
    assert decision.effect == PolicyEffect.ALLOW
    assert decision.matched_policy is None


def test_policy_engine_returns_first_matching_effect() -> None:
    def deny_all(_ctx):
        return PolicyEffect.DENY

    def neutral(_ctx):
        return PolicyEffect.NEUTRAL

    eng = PolicyEngine([
        Policy(name="neutral", priority=1, predicate=neutral),
        Policy(name="deny_all", priority=2, predicate=deny_all),
    ])
    decision = eng.evaluate({})
    assert decision.effect == PolicyEffect.DENY
    assert decision.matched_policy == "deny_all"
    assert "neutral=neutral" in decision.trace


def test_policy_engine_evaluates_in_priority_order() -> None:
    """Lower priority number runs first; lower-priority deny wins
    over higher-priority allow."""
    def deny(_ctx):
        return PolicyEffect.DENY

    def allow(_ctx):
        return PolicyEffect.ALLOW

    eng = PolicyEngine([
        Policy(name="hi_allow", priority=10, predicate=allow),
        Policy(name="lo_deny", priority=1, predicate=deny),
    ])
    decision = eng.evaluate({})
    assert decision.matched_policy == "lo_deny"


def test_policy_engine_rejects_duplicate_name() -> None:
    eng = PolicyEngine([Policy(name="x", priority=1,
                               predicate=lambda c: PolicyEffect.NEUTRAL)])
    with pytest.raises(ValueError):
        eng.add(Policy(name="x", priority=2,
                       predicate=lambda c: PolicyEffect.NEUTRAL))


def test_policy_deny_external_retrieval() -> None:
    eng = PolicyEngine([deny_external_retrieval()])
    deny = eng.evaluate({"action": "retrieve", "entity_kind": "External"})
    allow = eng.evaluate({"action": "retrieve", "entity_kind": "Function"})
    skip = eng.evaluate({"action": "ingest", "entity_kind": "External"})
    assert deny.effect == PolicyEffect.DENY
    assert allow.effect == PolicyEffect.ALLOW
    assert skip.effect == PolicyEffect.ALLOW  # NEUTRAL → default allow


def test_policy_restrict_mcp_tool_by_role() -> None:
    eng = PolicyEngine([restrict_mcp_tool_by_role(allowed={
        "agent": {"get_context", "query_graph"},
        "admin": {"*"},  # treated as a literal tool name unless wildcard set
        "*": {"get_module_summary"},
    })])
    # Wildcard tool — anyone allowed.
    assert eng.evaluate({
        "action": "mcp_tool", "role": "noone", "tool": "get_module_summary",
    }).effect == PolicyEffect.ALLOW
    # Role-permitted tool.
    assert eng.evaluate({
        "action": "mcp_tool", "role": "agent", "tool": "get_context",
    }).effect == PolicyEffect.ALLOW
    # Role-denied tool.
    assert eng.evaluate({
        "action": "mcp_tool", "role": "agent", "tool": "ingest_repository",
    }).effect == PolicyEffect.DENY
    # Unknown role.
    assert eng.evaluate({
        "action": "mcp_tool", "role": "phantom", "tool": "get_context",
    }).effect == PolicyEffect.DENY


def test_policy_limit_ingestion_size() -> None:
    eng = PolicyEngine([limit_ingestion_size(max_bytes=1024)])
    assert eng.evaluate({"action": "ingest", "payload_bytes": 100}).effect == PolicyEffect.ALLOW
    assert eng.evaluate({"action": "ingest", "payload_bytes": 2048}).effect == PolicyEffect.DENY


def test_policy_enforce_retention() -> None:
    eng = PolicyEngine([enforce_retention(max_age_days=30)])
    assert eng.evaluate({"entity_age_days": 5}).effect == PolicyEffect.ALLOW
    assert eng.evaluate({"entity_age_days": 60}).effect == PolicyEffect.DENY


# ---- AccessControl --------------------------------------------------------
def _setup() -> tuple[TenantManager, PolicyEngine]:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme", name="A"))
    tm.assign_repo(tenant_id="acme", repo_id="repo-1")
    eng = PolicyEngine([deny_external_retrieval()])
    return tm, eng


def test_access_control_denies_cross_tenant_access() -> None:
    tm, eng = _setup()
    ac = AccessControl(tenants=tm, policies=eng)
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="other-tenant", repo_id="repo-1",
        action="retrieve", entity_id="u1", entity_kind="Function",
    ))
    assert not decision.allowed
    assert decision.matched_policy == "tenant_ownership"


def test_access_control_allows_in_tenant_action() -> None:
    tm, eng = _setup()
    ac = AccessControl(tenants=tm, policies=eng)
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="acme", repo_id="repo-1",
        action="retrieve", entity_id="u1", entity_kind="Function",
    ))
    assert decision.allowed


def test_access_control_denies_external_retrieval() -> None:
    tm, eng = _setup()
    ac = AccessControl(tenants=tm, policies=eng)
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="acme", repo_id="repo-1",
        action="retrieve", entity_id="numpy", entity_kind="External",
    ))
    assert not decision.allowed
    assert decision.matched_policy == "deny_external_retrieval"
