"""Per-repo access enforcement helpers (Task 9, Phase-3 RBAC).

Non-breaking contract
---------------------
* auth NOT configured (dev/bootstrap)     → no-op (open)
* auth IS configured, principal NOT authenticated → no-op (open)
* auth IS configured AND principal IS authenticated → enforce RBAC

The two public async helpers (`assert_repo_access`, `filter_repos_for_principal`)
are thin glue between the routers and the pure `resolve_repo_access` / `accessible_repo_ids`
logic already in `core.auth.access`.

`load_access_for_principal` builds the {repo_id: level} access map for one
request by fetching:
  - all repo_ids this principal's org owns (from the repo_registry)
  - the principal's team memberships (to expand team grants)
  - all grants that apply to this user or their teams

Everything is scoped to `principal.org_id` so cross-org leakage is impossible.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from core.auth.access import accessible_repo_ids, resolve_repo_access
from core.auth.principal import Principal


# ---------------------------------------------------------------------------
# Auth-configured check (mirrors how config.py's _require_bootstrap_or_authed
# decides whether auth is "on"):
#   * runtime_config present  →  runtime.configured()  (Postgres-over-env)
#   * no runtime_config       →  settings.mcp_api_key  (env fallback)
# In both cases the caller also checks the token_cache (named tokens).
# ---------------------------------------------------------------------------

def _auth_configured(request: Request) -> bool:
    """Return True when the instance has auth enforced (key or named token set)."""
    from apps.mcp.token_auth import auth_is_configured

    token_cache = getattr(request.app.state, "token_cache", None)
    runtime = getattr(request.app.state, "runtime_config", None)
    if runtime is not None:
        expected_key: str | None = runtime.mcp_api_key()
    else:
        from core import get_settings
        s = get_settings()
        k = s.mcp_api_key
        expected_key = k.get_secret_value() if (k and k.get_secret_value().strip()) else None

    return auth_is_configured(expected_key, token_cache)


# ---------------------------------------------------------------------------
# Build the {repo_id: level} access map for a principal
# ---------------------------------------------------------------------------

async def load_access_for_principal(
    principal: Principal,
    app_state,  # AppState — untyped to avoid circular import
    request: Request | None = None,
) -> dict[str, str]:
    """Compute {repo_id: access_level} for the authenticated principal.

    Pulls from:
      - repo_registry.list_all() to get all repo_ids registered under the principal's org
      - team_repo.team_ids_for_user() to enumerate team memberships
      - repo_grant_repo.list_for_subjects() to collect grants for user + teams
    Then delegates to `resolve_repo_access` for the canonical RBAC logic.

    ``request`` is required in production so that ``repo_registry`` is read
    from ``request.app.state.repo_registry`` (where the lifespan attaches it)
    rather than from the ``AppState`` dataclass (which never holds it).  When
    ``request`` is absent (legacy unit-test callers) the function falls back to
    the old unfiltered ``units_repo.list_repos()`` path.
    """
    org_id = principal.org_id
    user_id = principal.user_id
    kind = principal.kind

    # The role the user holds in their org. For agents it's "agent".
    # For users, pick the first role in the tuple (there's exactly one per org).
    role = principal.roles[0] if principal.roles else "member"

    # All repo_ids owned by the principal's org — from the freshness registry.
    # The registry lives on request.app.state (attached by lifespan), NOT on the
    # AppState dataclass.  Reading it from the dataclass always yields None,
    # causing the fallback below to return every repo across all orgs (leak).
    repo_registry = None
    if request is not None:
        repo_registry = getattr(request.app.state, "repo_registry", None)
    if repo_registry is None:
        # Fall back to units_repo listing if registry isn't wired
        # (e.g. minimal test apps that don't run the full lifespan).
        summaries = await app_state.units_repo.list_repos()
        org_repo_ids: set[str] = {s.repo_id for s in summaries}
    else:
        all_rows = await repo_registry.list_all()
        org_repo_ids = {r.repo_id for r in all_rows if r.org_id == org_id}

    # Team memberships for this user in this org.
    team_ids: list[str] = []
    team_repo = getattr(app_state, "team_repo", None)
    if team_repo is not None and kind != "agent":
        team_ids = await team_repo.team_ids_for_user(user_id=user_id, org_id=org_id)

    # Grants for this user + their teams.
    grants: list[dict] = []
    repo_grant_repo = getattr(app_state, "repo_grant_repo", None)
    if repo_grant_repo is not None and kind != "agent":
        grant_rows = await repo_grant_repo.list_for_subjects(
            org_id=org_id, user_id=user_id, team_ids=team_ids
        )
        grants = [
            {"repo_id": g.repo_id, "access": g.access}
            for g in grant_rows
        ]

    return resolve_repo_access(
        kind=kind,
        role=role,
        org_repo_ids=org_repo_ids,
        user_id=user_id,
        team_ids=set(team_ids),
        grants=grants,
    )


# ---------------------------------------------------------------------------
# Public helpers consumed by the routers
# ---------------------------------------------------------------------------

async def assert_repo_access(
    request: Request,
    principal: Principal,
    repo_id: str,
    level: str,
    app_state,
) -> None:
    """Raise 403 when enforcement is active and the principal lacks `level` on `repo_id`.

    No-op (open) when:
      - auth is not configured  (dev / bootstrap mode)
      - principal is not authenticated  (anonymous caller)

    Special case — repo bootstrap / first ingest:
      When `level` is "write" and the repo is not yet registered in the org
      (brand-new repo, first ingest), privileged principals (agents and
      owner/admin humans) are allowed through so they can create the repo.
      Regular members must have an explicit write grant even on new repos.
    """
    if not _auth_configured(request):
        return
    if not principal.is_authenticated:
        return

    access = await load_access_for_principal(principal, app_state, request=request)
    permitted = accessible_repo_ids(access, need=level)
    if repo_id in permitted:
        return

    # Allow privileged principals to ingest/create a brand-new repo (one that
    # isn't in org_repo_ids yet, so resolve_repo_access couldn't include it).
    if level == "write":
        role = principal.roles[0] if principal.roles else ""
        if principal.kind == "agent" or role in ("owner", "admin"):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"access denied: {level!r} access required on repo {repo_id!r}",
    )


async def filter_repos_for_principal(
    request: Request,
    principal: Principal,
    repo_summaries: list,
    app_state,
) -> list:
    """Return only the repo summaries the principal may read.

    No-op (returns full list unchanged) when auth is not configured or the
    principal is not authenticated.
    """
    if not _auth_configured(request):
        return repo_summaries
    if not principal.is_authenticated:
        return repo_summaries

    access = await load_access_for_principal(principal, app_state, request=request)
    permitted = accessible_repo_ids(access, need="read")
    return [s for s in repo_summaries if s.repo_id in permitted]
