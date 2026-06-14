"""Org members + teams management endpoints (org-admin gated).

All endpoints operate on the org that the authenticated principal belongs to
(principal.org_id). Agents are considered org-admins of their org by design.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from apps.api.auth_deps import PrincipalDep
from apps.api.dependencies import MembershipRepoDep, TeamRepoDep, UserRepoDep
from core.auth.principal import ROLE_ADMIN, ROLE_OWNER, Principal
from schemas.orgs import AddTeamMemberRequest, CreateTeamRequest, SetRoleRequest

router = APIRouter(prefix="/orgs", tags=["orgs"])

_VALID_ROLES = {"owner", "admin", "member", "viewer"}


def require_org_admin(principal: PrincipalDep) -> Principal:
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    if principal.kind == "agent" or principal.has_role(ROLE_OWNER) or principal.has_role(ROLE_ADMIN):
        return principal
    raise HTTPException(status_code=403, detail="org admin required")


OrgAdminDep = Annotated[Principal, Depends(require_org_admin)]


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/members")
async def list_members(
    principal: OrgAdminDep,
    membership_repo: MembershipRepoDep,
    user_repo: UserRepoDep,
) -> dict:
    """List all members of the principal's org, hydrated with user email + display_name."""
    rows = await membership_repo.list_members(org_id=principal.org_id)
    result = []
    for m in rows:
        user = await user_repo.get_user(m.user_id)
        result.append({
            "user_id": m.user_id,
            "email": user.email if user else "",
            "display_name": user.display_name if user else "",
            "role": m.role,
        })
    return {"members": result}


@router.post("/members/{user_id}/role")
async def set_member_role(
    user_id: str,
    body: SetRoleRequest,
    principal: OrgAdminDep,
    membership_repo: MembershipRepoDep,
) -> dict:
    """Change a member's role. Guards against demoting the last owner."""
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(_VALID_ROLES)}")

    target = await membership_repo.get_membership(user_id=user_id, org_id=principal.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user has no membership in this org")

    # Last-owner guard: prevent demoting the sole owner
    if target.role == ROLE_OWNER and body.role != ROLE_OWNER:
        all_members = await membership_repo.list_members(org_id=principal.org_id)
        owner_count = sum(1 for m in all_members if m.role == ROLE_OWNER)
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="cannot demote the last owner")

    await membership_repo.set_role(user_id=user_id, org_id=principal.org_id, role=body.role)
    return {"ok": True}


@router.delete("/members/{user_id}")
async def remove_member(
    user_id: str,
    principal: OrgAdminDep,
    membership_repo: MembershipRepoDep,
) -> dict:
    """Remove a member from the org. Guards against removing the last owner."""
    target = await membership_repo.get_membership(user_id=user_id, org_id=principal.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user has no membership in this org")

    # Last-owner guard
    if target.role == ROLE_OWNER:
        all_members = await membership_repo.list_members(org_id=principal.org_id)
        owner_count = sum(1 for m in all_members if m.role == ROLE_OWNER)
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="cannot remove the last owner")

    await membership_repo.remove_member(user_id=user_id, org_id=principal.org_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@router.get("/teams")
async def list_teams(
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
) -> dict:
    """List teams in the principal's org."""
    rows = await team_repo.list_teams(org_id=principal.org_id)
    return {"teams": [{"team_id": t.team_id, "name": t.name, "slug": t.slug} for t in rows]}


@router.post("/teams")
async def create_team(
    body: CreateTeamRequest,
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
) -> dict:
    """Create a new team in the principal's org. 409 on duplicate slug."""
    team_id = secrets.token_urlsafe(8)
    try:
        row = await team_repo.create_team(
            team_id=team_id,
            org_id=principal.org_id,
            name=body.name,
            slug=body.slug,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="a team with that slug already exists in this org")
    return {"team_id": row.team_id, "name": row.name, "slug": row.slug}


@router.delete("/teams/{team_id}")
async def delete_team(
    team_id: str,
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
) -> dict:
    """Delete a team. 404 if it doesn't exist or belongs to another org."""
    team = await team_repo.get_team(team_id)
    if team is None or team.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="team not found")
    await team_repo.delete_team(team_id)
    return {"ok": True}


@router.post("/teams/{team_id}/members")
async def add_team_member(
    team_id: str,
    body: AddTeamMemberRequest,
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
    membership_repo: MembershipRepoDep,
) -> dict:
    """Add a user to a team. The user must be an org member."""
    team = await team_repo.get_team(team_id)
    if team is None or team.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="team not found")

    # Verify the user is an org member
    membership = await membership_repo.get_membership(user_id=body.user_id, org_id=principal.org_id)
    if membership is None:
        raise HTTPException(status_code=400, detail="user is not an org member")

    await team_repo.add_team_member(team_id=team_id, user_id=body.user_id)
    return {"ok": True}


@router.delete("/teams/{team_id}/members/{user_id}")
async def remove_team_member(
    team_id: str,
    user_id: str,
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
) -> dict:
    """Remove a user from a team."""
    team = await team_repo.get_team(team_id)
    if team is None or team.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="team not found")

    await team_repo.remove_team_member(team_id=team_id, user_id=user_id)
    return {"ok": True}


@router.get("/teams/{team_id}/members")
async def list_team_members(
    team_id: str,
    principal: OrgAdminDep,
    team_repo: TeamRepoDep,
    user_repo: UserRepoDep,
) -> dict:
    """List members of a team, hydrated with user email + display_name."""
    team = await team_repo.get_team(team_id)
    if team is None or team.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="team not found")

    user_ids = await team_repo.list_team_member_ids(team_id)
    result = []
    for uid in user_ids:
        user = await user_repo.get_user(uid)
        result.append({
            "user_id": uid,
            "email": user.email if user else "",
            "display_name": user.display_name if user else "",
        })
    return {"members": result}
