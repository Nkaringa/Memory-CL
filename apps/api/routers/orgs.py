"""Org members + teams management endpoints (org-admin gated).

All endpoints operate on the org that the authenticated principal belongs to
(principal.org_id). Agents are considered org-admins of their org by design.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from apps.api.auth_deps import PrincipalDep, hash_session_token, new_session_token
from apps.api.dependencies import (
    InvitationRepoDep,
    MembershipRepoDep,
    RepoGrantRepoDep,
    RepoRegistryDep,
    TeamRepoDep,
    UserRepoDep,
)
from core.auth.principal import ROLE_ADMIN, ROLE_OWNER, Principal
from schemas.orgs import (
    AddTeamMemberRequest,
    CreateGrantRequest,
    CreateInvitationRequest,
    CreateTeamRequest,
    SetRoleRequest,
)
from storage.org_repo import DEFAULT_ORG_ID

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


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

_VALID_ACCESS = {"read", "write", "admin"}


@router.post("/invitations")
async def create_invitation(
    body: CreateInvitationRequest,
    principal: OrgAdminDep,
    invitation_repo: InvitationRepoDep,
) -> dict:
    """Create an org invitation. Returns the raw invite token once (not stored)."""
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(_VALID_ROLES)}")
    raw = new_session_token()
    inv_id = secrets.token_urlsafe(8)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await invitation_repo.create(
        id=inv_id,
        org_id=principal.org_id,
        email=body.email.strip().lower(),
        role=body.role,
        token_hash=hash_session_token(raw),
        invited_by=principal.user_id,
        expires_at=expires_at,
    )
    return {"id": inv_id, "invite_token": raw, "accept_path": "/accept-invite?token=" + raw}


@router.get("/invitations")
async def list_invitations(
    principal: OrgAdminDep,
    invitation_repo: InvitationRepoDep,
) -> dict:
    """List pending (and all) invitations for the org."""
    rows = await invitation_repo.list_for_org(principal.org_id)
    return {
        "invitations": [
            {
                "id": r.id,
                "email": r.email,
                "role": r.role,
                "status": r.status,
                "expires_at": r.expires_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.delete("/invitations/{inv_id}")
async def revoke_invitation(
    inv_id: str,
    principal: OrgAdminDep,
    invitation_repo: InvitationRepoDep,
) -> dict:
    """Revoke a pending invitation."""
    await invitation_repo.revoke(inv_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-repo grants
# ---------------------------------------------------------------------------


@router.post("/repos/{repo_id}/grants")
async def create_grant(
    repo_id: str,
    body: CreateGrantRequest,
    principal: OrgAdminDep,
    repo_grant_repo: RepoGrantRepoDep,
    repo_registry: RepoRegistryDep,
) -> dict:
    """Grant a team or user access to a specific repo."""
    if body.subject_type not in {"team", "user"}:
        raise HTTPException(status_code=422, detail="subject_type must be 'team' or 'user'")
    if body.access not in _VALID_ACCESS:
        raise HTTPException(status_code=422, detail=f"access must be one of {sorted(_VALID_ACCESS)}")

    # Verify the repo belongs to this org. Missing registry rows default to "default" org.
    reg = await repo_registry.get(repo_id)
    repo_org = reg.org_id if reg is not None else DEFAULT_ORG_ID
    if repo_org != principal.org_id:
        raise HTTPException(status_code=403, detail="repo not in your org")

    grant_id = secrets.token_urlsafe(8)
    row = await repo_grant_repo.grant(
        id=grant_id,
        org_id=principal.org_id,
        repo_id=repo_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        access=body.access,
    )
    return {"id": row.id, "repo_id": row.repo_id, "subject_type": row.subject_type, "subject_id": row.subject_id, "access": row.access}


@router.get("/repos/{repo_id}/grants")
async def list_grants(
    repo_id: str,
    principal: OrgAdminDep,
    repo_grant_repo: RepoGrantRepoDep,
) -> dict:
    """List grants for a repo."""
    rows = await repo_grant_repo.list_for_repo(repo_id=repo_id)
    return {
        "grants": [
            {"id": r.id, "subject_type": r.subject_type, "subject_id": r.subject_id, "access": r.access}
            for r in rows
        ]
    }


@router.delete("/grants/{grant_id}")
async def revoke_grant(
    grant_id: str,
    principal: OrgAdminDep,
    repo_grant_repo: RepoGrantRepoDep,
) -> dict:
    """Revoke a repo grant."""
    await repo_grant_repo.revoke(grant_id)
    return {"ok": True}
