"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ChevronDown, ChevronRight } from "lucide-react";
import { Panel, Btn } from "@/components/shell/primitives";
import { copyToClipboard } from "@/lib/utils";
import { fetchMe } from "@/lib/auth";
import {
  listMembers,
  setMemberRole,
  removeMember,
  listTeams,
  createTeam,
  deleteTeam,
  listTeamMembers,
  addTeamMember,
  removeTeamMember,
  listInvitations,
  createInvitation,
  revokeInvitation,
  type InviteResult,
} from "@/lib/orgs";
import type { OrgMember, Team, Invitation } from "@/lib/types";

const ROLE_OPTIONS = ["owner", "admin", "member", "viewer"];

// ---- helpers ---------------------------------------------------------------

function isAdmin(roles: string[]): boolean {
  return roles.includes("owner") || roles.includes("admin");
}

function relativeExpiry(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = (then - Date.now()) / 1000;
  if (secs < 0) return "expired";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

// ---- Members section -------------------------------------------------------

function MembersSection({ members, loading }: { members: OrgMember[]; loading: boolean }) {
  const qc = useQueryClient();

  const roleChange = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      setMemberRole(userId, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-members"] }),
  });

  const remove = useMutation({
    mutationFn: (userId: string) => removeMember(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-members"] }),
  });

  const [roleError, setRoleError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  return (
    <div className="mb-6">
      <div className="mb-2 text-[13px] font-semibold">Members</div>
      {loading ? (
        <div className="text-[12.5px] text-muted">Loading…</div>
      ) : members.length === 0 ? (
        <div className="text-[12.5px] text-muted2">No members found.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                <th className="py-2 text-left font-semibold">email</th>
                <th className="py-2 text-left font-semibold">display name</th>
                <th className="py-2 text-left font-semibold">role</th>
                <th className="py-2 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id} className="border-t border-border">
                  <td className="py-2.5 font-mono text-[12.5px] text-muted">{m.email}</td>
                  <td className="py-2.5">{m.display_name}</td>
                  <td className="py-2.5">
                    <select
                      value={m.role}
                      onChange={(e) => {
                        setRoleError(null);
                        roleChange.mutate(
                          { userId: m.user_id, role: e.target.value },
                          { onError: (err) => setRoleError(err instanceof Error ? err.message : "Could not update role") },
                        );
                      }}
                      className="rounded-lg border border-border bg-bg px-2.5 py-1 text-[12.5px] outline-none focus:border-accent"
                    >
                      {ROLE_OPTIONS.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => {
                        setRemoveError(null);
                        remove.mutate(m.user_id, {
                          onError: (err) =>
                            setRemoveError(err instanceof Error ? err.message : "Could not remove member"),
                        });
                      }}
                      className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-bad hover:text-bad"
                    >
                      remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {roleError && (
        <div className="mt-2 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3 py-2 text-[12.5px] text-bad">
          {roleError}
        </div>
      )}
      {removeError && (
        <div className="mt-2 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3 py-2 text-[12.5px] text-bad">
          {removeError}
        </div>
      )}
    </div>
  );
}

// ---- Team row (expandable) -------------------------------------------------

function TeamRow({ team, orgMembers }: { team: Team; orgMembers: OrgMember[] }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [addUserId, setAddUserId] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  const teamMembers = useQuery({
    queryKey: ["team-members", team.team_id],
    queryFn: () => listTeamMembers(team.team_id),
    enabled: expanded,
  });

  const addMember = useMutation({
    mutationFn: (userId: string) => addTeamMember(team.team_id, userId),
    onSuccess: () => {
      setAddUserId("");
      setAddError(null);
      qc.invalidateQueries({ queryKey: ["team-members", team.team_id] });
    },
    onError: (err) =>
      setAddError(err instanceof Error ? err.message : "Could not add member"),
  });

  const removeTM = useMutation({
    mutationFn: (userId: string) => removeTeamMember(team.team_id, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["team-members", team.team_id] }),
  });

  const delTeam = useMutation({
    mutationFn: () => deleteTeam(team.team_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-teams"] }),
  });

  return (
    <div className="border-t border-border py-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-center gap-1.5 text-[13px] font-medium text-fg hover:text-accentInk"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {team.name}
          <span className="font-mono text-[11.5px] text-muted2">/{team.slug}</span>
        </button>
        <button
          type="button"
          onClick={() => delTeam.mutate()}
          className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-bad hover:text-bad"
        >
          delete
        </button>
      </div>

      {expanded && (
        <div className="ml-5 mt-2">
          {teamMembers.isLoading ? (
            <div className="text-[12.5px] text-muted">Loading…</div>
          ) : (teamMembers.data ?? []).length === 0 ? (
            <div className="text-[12.5px] text-muted2">No members in this team.</div>
          ) : (
            <div className="mb-2 space-y-1">
              {(teamMembers.data ?? []).map((tm) => (
                <div key={tm.user_id} className="flex items-center justify-between text-[12.5px]">
                  <span>
                    {tm.display_name}{" "}
                    <span className="font-mono text-muted">&lt;{tm.email}&gt;</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => removeTM.mutate(tm.user_id)}
                    className="rounded-md border border-border2 bg-bg px-2 py-0.5 text-[11.5px] text-muted2 hover:border-bad hover:text-bad"
                  >
                    remove
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* add member */}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              value={addUserId}
              onChange={(e) => setAddUserId(e.target.value)}
              className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-[12.5px] outline-none focus:border-accent"
            >
              <option value="">select member…</option>
              {orgMembers.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.display_name} ({m.email})
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!addUserId || addMember.isPending}
              onClick={() => {
                if (addUserId) addMember.mutate(addUserId);
              }}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accentInk disabled:opacity-50"
            >
              {addMember.isPending ? "Adding…" : "Add"}
            </button>
          </div>
          {addError && (
            <div className="mt-1.5 text-[12px] text-bad">{addError}</div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Teams section ---------------------------------------------------------

function TeamsSection({ orgMembers }: { orgMembers: OrgMember[] }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const teams = useQuery({ queryKey: ["org-teams"], queryFn: listTeams });

  const create = useMutation({
    mutationFn: () => createTeam(name.trim(), slug.trim()),
    onSuccess: () => {
      setName("");
      setSlug("");
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["org-teams"] });
    },
    onError: (err) =>
      setFormError(err instanceof Error ? err.message : "Could not create team"),
  });

  const list: Team[] = teams.data ?? [];

  return (
    <div className="mb-6">
      <div className="mb-2 text-[13px] font-semibold">Teams</div>
      {teams.isError && (
        <div className="mb-2 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3 py-2 text-[12.5px] text-bad">
          Could not load teams.
        </div>
      )}
      {teams.isLoading ? (
        <div className="text-[12.5px] text-muted">Loading…</div>
      ) : list.length === 0 ? (
        <div className="text-[12.5px] text-muted2">No teams yet.</div>
      ) : (
        <div className="mb-3">
          {list.map((t) => (
            <TeamRow key={t.team_id} team={t} orgMembers={orgMembers} />
          ))}
        </div>
      )}

      {/* create team form */}
      <div className="mt-3 border-t border-border pt-3">
        <div className="text-[12.5px] font-medium text-muted2">Create team</div>
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Team name"
            className="rounded-lg border border-border bg-bg px-3 py-1.5 text-[13px] outline-none focus:border-accent"
          />
          <input
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="slug"
            className="rounded-lg border border-border bg-bg px-3 py-1.5 font-mono text-[13px] outline-none focus:border-accent"
          />
          <button
            type="button"
            disabled={!name.trim() || !slug.trim() || create.isPending}
            onClick={() => { setFormError(null); create.mutate(); }}
            className="rounded-lg bg-accent px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-accentInk disabled:opacity-50"
          >
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
        {formError && (
          <div className="mt-2 text-[12px] text-bad">{formError}</div>
        )}
      </div>
    </div>
  );
}

// ---- Accept-link display (one-time reveal) ---------------------------------

function InviteLink({ result, onDismiss }: { result: InviteResult; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const link = `${origin}${result.accept_path}`;

  return (
    <div className="mb-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-3">
      <div className="mb-1.5 text-[12.5px] font-semibold text-[#8a5a00]">
        Invitation created — share this link. It won&apos;t be shown again.
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-[#1d1d1b] px-3 py-2 font-mono text-[12px] text-[#e6e6e6]">
        {link}
      </pre>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={async () => {
            if (await copyToClipboard(link)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border2 bg-bg px-2.5 py-1.5 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "copied" : "copy link"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-lg border border-border2 bg-bg px-2.5 py-1.5 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg"
        >
          done
        </button>
      </div>
    </div>
  );
}

// ---- Invitations section ---------------------------------------------------

function InvitationsSection() {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [formError, setFormError] = useState<string | null>(null);
  const [lastInvite, setLastInvite] = useState<InviteResult | null>(null);

  const invitations = useQuery({ queryKey: ["org-invitations"], queryFn: listInvitations });

  const create = useMutation({
    mutationFn: () => createInvitation(email.trim(), role),
    onSuccess: (result) => {
      setEmail("");
      setRole("member");
      setFormError(null);
      setLastInvite(result);
      qc.invalidateQueries({ queryKey: ["org-invitations"] });
    },
    onError: (err) =>
      setFormError(err instanceof Error ? err.message : "Could not create invitation"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeInvitation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-invitations"] }),
  });

  const list: Invitation[] = invitations.data ?? [];
  const pending = list.filter((i) => i.status === "pending");

  return (
    <div>
      <div className="mb-2 text-[13px] font-semibold">Invitations</div>

      {/* send invite form */}
      <div className="mb-3">
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="mb-1 block text-[11.5px] font-medium text-muted2">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="colleague@example.com"
              className="rounded-lg border border-border bg-bg px-3 py-1.5 text-[13px] outline-none focus:border-accent"
            />
          </div>
          <div>
            <label className="mb-1 block text-[11.5px] font-medium text-muted2">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <button
            type="button"
            disabled={!email.trim() || create.isPending}
            onClick={() => { setFormError(null); create.mutate(); }}
            className="rounded-lg bg-accent px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-accentInk disabled:opacity-50"
          >
            {create.isPending ? "Sending…" : "Send invite"}
          </button>
        </div>
        {formError && (
          <div className="mt-2 text-[12px] text-bad">{formError}</div>
        )}
      </div>

      {/* one-time reveal */}
      {lastInvite && (
        <InviteLink result={lastInvite} onDismiss={() => setLastInvite(null)} />
      )}

      {/* pending invitations list */}
      {invitations.isError && (
        <div className="mb-2 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3 py-2 text-[12.5px] text-bad">
          Could not load invitations.
        </div>
      )}
      {pending.length === 0 ? (
        <div className="text-[12.5px] text-muted2">No pending invitations.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                <th className="py-2 text-left font-semibold">email</th>
                <th className="py-2 text-left font-semibold">role</th>
                <th className="py-2 text-left font-semibold">status</th>
                <th className="py-2 text-left font-semibold">expires</th>
                <th className="py-2 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              {pending.map((inv) => (
                <tr key={inv.id} className="border-t border-border">
                  <td className="py-2 font-mono text-[12.5px] text-muted">{inv.email}</td>
                  <td className="py-2">{inv.role}</td>
                  <td className="py-2">
                    <span className="rounded-md bg-accentSoft px-2 py-0.5 text-[11.5px] font-medium text-accentInk">
                      {inv.status}
                    </span>
                  </td>
                  <td className="py-2 tabular-nums text-muted">{relativeExpiry(inv.expires_at)}</td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      onClick={() => revoke.mutate(inv.id)}
                      className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-bad hover:text-bad"
                    >
                      revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Main panel ------------------------------------------------------------

export function OrganizationPanel() {
  const me = useQuery({ queryKey: ["me"], queryFn: fetchMe });

  const roles = me.data?.user?.roles ?? [];
  if (me.isLoading) return null;
  if (!isAdmin(roles)) {
    return (
      <Panel title="Organization" className="mb-3.5">
        <div className="px-4 py-4 text-[12.5px] text-muted">
          You need owner or admin privileges to manage the organization.
        </div>
      </Panel>
    );
  }

  return <OrganizationPanelAdmin />;
}

function OrganizationPanelAdmin() {
  const members = useQuery({ queryKey: ["org-members"], queryFn: listMembers });
  const orgMembers: OrgMember[] = members.data ?? [];

  return (
    <Panel title="Organization" className="mb-3.5">
      <div className="px-4 py-4">
        <div className="mb-4 text-[12.5px] text-muted">
          Manage org members, teams, and invitations. Only owners and admins can access this section.
        </div>

        {members.isError && (
          <div className="mb-3 rounded-lg border border-[#f0c9c9] bg-[#fef2f2] px-3.5 py-2.5 text-[12.5px] text-bad">
            Could not load members.
          </div>
        )}

        <MembersSection members={orgMembers} loading={members.isLoading} />
        <TeamsSection orgMembers={orgMembers} />
        <InvitationsSection />
      </div>
    </Panel>
  );
}
