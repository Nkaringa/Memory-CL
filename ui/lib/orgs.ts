/* Typed helpers for the RBAC /orgs/* and /auth/accept-invite endpoints.
 *
 * All calls go through /api/* (same-origin, Next.js rewrite → backend).
 * Mutations throw on non-2xx so callers can surface errors with useMutation.
 */

import type { Invitation, MeResponse, OrgMember, RepoGrant, Team, TeamMember } from "@/lib/types";

const BASE = "/api";

async function orgFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...opts,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      ...(opts.headers ?? {}),
    },
  });
}

async function throwIfNotOk(resp: Response, fallback: string): Promise<void> {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail ?? fallback;
    throw new Error(String(detail));
  }
}

// ---- Members ---------------------------------------------------------------

export async function listMembers(): Promise<OrgMember[]> {
  const resp = await orgFetch("/orgs/members");
  await throwIfNotOk(resp, "Could not load members");
  const body = (await resp.json()) as { members: OrgMember[] };
  return body.members ?? [];
}

export async function setMemberRole(userId: string, role: string): Promise<void> {
  const resp = await orgFetch(`/orgs/members/${encodeURIComponent(userId)}/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
  await throwIfNotOk(resp, "Could not update member role");
}

export async function removeMember(userId: string): Promise<void> {
  const resp = await orgFetch(`/orgs/members/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
  await throwIfNotOk(resp, "Could not remove member");
}

// ---- Teams -----------------------------------------------------------------

export async function listTeams(): Promise<Team[]> {
  const resp = await orgFetch("/orgs/teams");
  await throwIfNotOk(resp, "Could not load teams");
  const body = (await resp.json()) as { teams: Team[] };
  return body.teams ?? [];
}

export async function createTeam(name: string, slug: string): Promise<Team> {
  const resp = await orgFetch("/orgs/teams", {
    method: "POST",
    body: JSON.stringify({ name, slug }),
  });
  await throwIfNotOk(resp, "Could not create team");
  return (await resp.json()) as Team;
}

export async function deleteTeam(teamId: string): Promise<void> {
  const resp = await orgFetch(`/orgs/teams/${encodeURIComponent(teamId)}`, {
    method: "DELETE",
  });
  await throwIfNotOk(resp, "Could not delete team");
}

export async function listTeamMembers(teamId: string): Promise<TeamMember[]> {
  const resp = await orgFetch(`/orgs/teams/${encodeURIComponent(teamId)}/members`);
  await throwIfNotOk(resp, "Could not load team members");
  const body = (await resp.json()) as { members: TeamMember[] };
  return body.members ?? [];
}

export async function addTeamMember(teamId: string, userId: string): Promise<void> {
  const resp = await orgFetch(`/orgs/teams/${encodeURIComponent(teamId)}/members`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
  await throwIfNotOk(resp, "Could not add team member");
}

export async function removeTeamMember(teamId: string, userId: string): Promise<void> {
  const resp = await orgFetch(
    `/orgs/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
  await throwIfNotOk(resp, "Could not remove team member");
}

// ---- Invitations -----------------------------------------------------------

export interface InviteResult {
  id: string;
  invite_token: string;
  accept_path: string;
}

export async function createInvitation(email: string, role: string): Promise<InviteResult> {
  const resp = await orgFetch("/orgs/invitations", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
  await throwIfNotOk(resp, "Could not create invitation");
  return (await resp.json()) as InviteResult;
}

export async function listInvitations(): Promise<Invitation[]> {
  const resp = await orgFetch("/orgs/invitations");
  await throwIfNotOk(resp, "Could not load invitations");
  const body = (await resp.json()) as { invitations: Invitation[] };
  return body.invitations ?? [];
}

export async function revokeInvitation(id: string): Promise<void> {
  const resp = await orgFetch(`/orgs/invitations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  await throwIfNotOk(resp, "Could not revoke invitation");
}

// ---- Grants ----------------------------------------------------------------

export async function listGrants(repoId: string): Promise<RepoGrant[]> {
  const resp = await orgFetch(`/orgs/repos/${encodeURIComponent(repoId)}/grants`);
  await throwIfNotOk(resp, "Could not load grants");
  const body = (await resp.json()) as { grants: RepoGrant[] };
  return body.grants ?? [];
}

export interface CreateGrantBody {
  subject_type: "team" | "user";
  subject_id: string;
  access: "read" | "write" | "admin";
}

export async function createGrant(repoId: string, body: CreateGrantBody): Promise<RepoGrant> {
  const resp = await orgFetch(`/orgs/repos/${encodeURIComponent(repoId)}/grants`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  await throwIfNotOk(resp, "Could not create grant");
  return (await resp.json()) as RepoGrant;
}

export async function deleteGrant(grantId: string): Promise<void> {
  const resp = await orgFetch(`/orgs/grants/${encodeURIComponent(grantId)}`, {
    method: "DELETE",
  });
  await throwIfNotOk(resp, "Could not delete grant");
}

// ---- Accept invite ---------------------------------------------------------

export interface AcceptInviteBody {
  token: string;
  email?: string;
  password?: string;
  display_name?: string;
}

export async function acceptInvite(body: AcceptInviteBody): Promise<MeResponse> {
  const resp = await orgFetch("/auth/accept-invite", {
    method: "POST",
    body: JSON.stringify(body),
  });
  await throwIfNotOk(resp, "Could not accept invitation");
  return (await resp.json()) as MeResponse;
}
