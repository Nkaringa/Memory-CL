"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { getMemoryClient } from "@/lib/api";
import type { FreshnessRepo } from "@/lib/types";
import { PageHeader, Panel, Btn } from "@/components/shell/primitives";

interface OverviewData {
  found?: boolean;
  repo_id?: string;
  units?: number;
  files?: number;
  languages?: Record<string, number>;
  module_tree?: { name: string; units: number; modules: string[] }[];
  most_connected?: { qualified_name: string; kind: string; connections: number }[];
  note?: string;
}

export default function RepositoriesPage() {
  const client = getMemoryClient();
  const repos = useQuery({
    queryKey: ["repos"],
    queryFn: () => client.listRepos(),
    refetchInterval: 60_000,
  });

  const [selected, setSelected] = useState<string>("");
  const [showAdd, setShowAdd] = useState(false);

  const overview = useMutation({
    mutationFn: async (repoId: string) => {
      const resp = await client.runTool("repo_overview", { repo_id: repoId });
      return resp.data as unknown as OverviewData;
    },
  });

  const openOverview = (repoId: string) => {
    setSelected(repoId);
    overview.mutate(repoId);
  };

  const freshness = useQuery({
    queryKey: ["freshness"],
    queryFn: () => client.getFreshness(),
    refetchInterval: 30_000,
  });

  const list = repos.data?.repos ?? [];
  const totalUnits = list.reduce((a, r) => a + r.units, 0);
  const ov = overview.data;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Repositories"
        subtitle={`${list.length} repositories · ${totalUnits.toLocaleString()} units`}
        actions={<Btn primary onClick={() => setShowAdd((v) => !v)}>+ Add repository</Btn>}
      />

      {showAdd && <AddRepoPanel />}

      <Panel bodyClass="p-0">
        {repos.isLoading ? (
          <div className="px-4 py-8 text-center text-[13px] text-muted">Loading repositories…</div>
        ) : list.length === 0 ? (
          <div className="px-4 py-8 text-center text-[13px] text-muted">No repositories ingested.</div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                <th className="px-4 py-2.5 text-left font-semibold">repository</th>
                <th className="px-4 py-2.5 text-left font-semibold">units</th>
                <th className="px-4 py-2.5 text-left font-semibold">files</th>
                <th className="px-4 py-2.5 text-left font-semibold">languages</th>
                <th className="px-4 py-2.5 text-left font-semibold">actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.repo_id} className="border-t border-border">
                  <td className="px-4 py-2.5 font-semibold">{r.repo_id}</td>
                  <td className="px-4 py-2.5 tabular-nums">{r.units.toLocaleString()}</td>
                  <td className="px-4 py-2.5 tabular-nums">{r.files.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-muted">
                    {r.languages.length > 3
                      ? `${r.languages.length} languages`
                      : r.languages.join(", ") || "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex gap-1.5">
                      <SmBtn onClick={() => openOverview(r.repo_id)}>overview</SmBtn>
                      <SmBtn title="Re-embed calls /ingest/reembed — planned operator action">re-embed</SmBtn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* freshness */}
      <FreshnessPanel
        data={freshness.data?.repos ?? []}
        enabled={freshness.data?.freshness_enabled ?? true}
        loading={freshness.isLoading}
      />

      {/* overview drill-in */}
      {(overview.isPending || ov) && (
        <Panel className="mt-3.5" title={<span>Overview {selected ? <span className="font-mono font-normal text-muted">· {selected}</span> : null}</span>}>
          <div className="px-4 py-4">
            {overview.isPending ? (
              <div className="py-2 text-[13px] text-muted">Loading overview…</div>
            ) : !ov?.found ? (
              <div className="py-2 text-[13px] text-muted">{ov?.note ?? "No overview available."}</div>
            ) : (
              <>
                <div className="mb-3 text-[12.5px] text-muted">
                  {ov.units?.toLocaleString()} units · {ov.files?.toLocaleString()} files
                </div>

                {/* language bars */}
                {ov.languages && Object.keys(ov.languages).length > 0 && (
                  <div className="mb-4">
                    {Object.entries(ov.languages)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 6)
                      .map(([lang, count]) => {
                        const pct = ov.units ? (count / ov.units) * 100 : 0;
                        return (
                          <div key={lang} className="my-1.5 flex items-center gap-3 text-[12.5px]">
                            <span className="w-24 text-muted2">{lang}</span>
                            <span className="h-2 flex-1 overflow-hidden rounded bg-panel2">
                              <i className="block h-full rounded bg-accent" style={{ width: `${pct}%` }} />
                            </span>
                            <span className="w-24 text-right font-mono tabular-nums text-muted">
                              {count} · {pct.toFixed(0)}%
                            </span>
                          </div>
                        );
                      })}
                  </div>
                )}

                {/* module tree */}
                {ov.module_tree && ov.module_tree.length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1.5 text-[11.5px] uppercase tracking-wide text-muted">Top modules</div>
                    <div className="flex flex-wrap gap-1.5">
                      {ov.module_tree.slice(0, 12).map((m) => (
                        <span
                          key={m.name}
                          className="rounded-md bg-accentSoft px-2 py-1 font-mono text-[11.5px] text-accentInk"
                        >
                          {m.name} <span className="opacity-70">{m.units}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* most connected */}
                {ov.most_connected && ov.most_connected.length > 0 && (
                  <div className="text-[12.5px] text-muted">
                    most connected:{" "}
                    {ov.most_connected.slice(0, 3).map((c, i) => (
                      <span key={c.qualified_name}>
                        {i > 0 ? " · " : ""}
                        <span className="font-mono text-fg">{c.qualified_name}</span> ({c.connections})
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </Panel>
      )}
    </div>
  );
}

function AddRepoPanel() {
  const [mode, setMode] = useState<"managed" | "local">("managed");
  return (
    <Panel className="mb-3.5" title="Add repository">
      <div className="px-4 py-4">
        <div className="mb-3 inline-flex rounded-lg border border-border2 p-0.5 text-[12.5px]">
          {(["managed", "local"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1 font-medium transition-colors ${
                mode === m ? "bg-accentSoft text-accentInk" : "text-muted2 hover:text-fg"
              }`}
            >
              {m === "managed" ? "Git URL (auto-fresh)" : "Server path (local)"}
            </button>
          ))}
        </div>
        {mode === "managed" ? <AddManagedForm /> : <AddLocalForm />}
      </div>
    </Panel>
  );
}

function AddManagedForm() {
  const client = getMemoryClient();
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");

  const add = useMutation({
    mutationFn: () =>
      client.addManagedRepo({
        remote_url: url.trim(),
        branch: branch.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["freshness"] });
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });

  return (
    <>
      <div className="mb-2 text-[12.5px] text-muted">
        Memory-CL clones the repo into its own workspace, ingests it, and keeps it fresh by polling
        the branch. No server access needed — just a git URL.
      </div>
      <label className="mb-1 block text-[11.5px] text-muted2">git remote URL</label>
      <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/you/project.git"
        className="mb-2 w-full rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
      />
      <label className="mb-1 block text-[11.5px] text-muted2">branch (optional)</label>
      <input
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
        placeholder="main"
        className="mb-3 w-full max-w-[240px] rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
      />
      <div>
        <button
          onClick={() => add.mutate()}
          disabled={add.isPending || !url.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white hover:bg-accentInk disabled:opacity-50"
        >
          {add.isPending ? "Cloning + ingesting…" : "Add & keep fresh"}
        </button>
      </div>
      {add.isError && (
        <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2 text-[12px] text-[#8a5a00]">
          Could not add the repo — check the URL is reachable (private repos need a GITHUB_TOKEN).
          ({add.error.message})
        </div>
      )}
      {add.data && (
        <div className="mt-3 rounded-lg border border-border bg-panel px-3 py-2 text-[12px]">
          Added <span className="font-mono text-accentInk">{add.data.repo_id}</span> @{" "}
          <span className="font-mono">{(add.data.commit_sha ?? "").slice(0, 12)}</span> — now watched.
        </div>
      )}
    </>
  );
}

function AddLocalForm() {
  const client = getMemoryClient();
  const qc = useQueryClient();
  const [path, setPath] = useState("");
  const repoId = path.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() || "";

  const ingest = useMutation({
    mutationFn: () =>
      client.ingest({ repo_id: repoId, repo_path: path, commit_sha: "manual" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["freshness"] });
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });

  return (
    <>
      <div className="mb-2 text-[12.5px] text-muted">
        Ingestion reads a path on the <b>server host</b> (same model as the CLI). Once ingested, the
        local watcher keeps it fresh if the files change on the server.
      </div>
      <label className="mb-1 block text-[11.5px] text-muted2">server path</label>
      <input
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/repos/my-project"
        className="mb-2 w-full rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
      />
      {repoId && (
        <div className="mb-3 text-[12px] text-muted">
          repo id → <span className="font-mono text-accentInk">{repoId}</span>
        </div>
      )}
      <button
        onClick={() => ingest.mutate()}
        disabled={ingest.isPending || !path.trim()}
        className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white hover:bg-accentInk disabled:opacity-50"
      >
        {ingest.isPending ? "Ingesting…" : "Ingest"}
      </button>
      {ingest.isError && (
        <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2 text-[12px] text-[#8a5a00]">
          Ingest failed — confirm the path exists on the server host. ({ingest.error.message})
        </div>
      )}
      {ingest.data && (
        <div className="mt-3 rounded-lg border border-border bg-panel px-3 py-2 text-[12px]">
          Ingested <span className="font-mono text-accentInk">{ingest.data.repo_id}</span> @{" "}
          <span className="font-mono">{ingest.data.commit_sha}</span>
          {ingest.data.failed_files.length > 0
            ? ` · ${ingest.data.failed_files.length} files failed`
            : " · no failures"}
        </div>
      )}
    </>
  );
}

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function FreshnessPanel({
  data,
  enabled,
  loading,
}: {
  data: FreshnessRepo[];
  enabled: boolean;
  loading: boolean;
}) {
  const client = getMemoryClient();
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["freshness"] });

  const toggle = useMutation({
    mutationFn: (v: { id: string; on: boolean }) => client.setFreshnessWatch(v.id, v.on),
    onSuccess: invalidate,
  });
  const sync = useMutation({
    mutationFn: (id: string) => client.syncFreshness(id),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => client.removeFreshness(id),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });

  return (
    <Panel
      className="mt-3.5"
      bodyClass="p-0"
      title={
        <span>
          Freshness{" "}
          <span className="text-[12px] font-normal text-muted">
            · {enabled ? "auto-reingest on" : "disabled"}
          </span>
        </span>
      }
    >
      {loading ? (
        <div className="px-4 py-6 text-center text-[13px] text-muted">Loading…</div>
      ) : data.length === 0 ? (
        <div className="px-4 py-6 text-center text-[13px] text-muted">
          No repos registered for freshness yet. Add a Git URL above to keep one fresh automatically.
        </div>
      ) : (
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-[11.5px] uppercase tracking-wide text-muted">
              <th className="px-4 py-2.5 text-left font-semibold">repository</th>
              <th className="px-4 py-2.5 text-left font-semibold">source</th>
              <th className="px-4 py-2.5 text-left font-semibold">branch</th>
              <th className="px-4 py-2.5 text-left font-semibold">synced</th>
              <th className="px-4 py-2.5 text-left font-semibold">state</th>
              <th className="px-4 py-2.5 text-left font-semibold">actions</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.repo_id} className="border-t border-border align-middle">
                <td className="px-4 py-2.5 font-semibold">{r.repo_id}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-bold ${
                      r.source_type === "managed"
                        ? "bg-accentSoft text-accentInk"
                        : "bg-panel2 text-muted2"
                    }`}
                  >
                    {r.source_type === "managed" ? "GIT" : "LOCAL"}
                  </span>
                </td>
                <td className="px-4 py-2.5 font-mono text-[12px] text-muted">{r.branch ?? "—"}</td>
                <td className="px-4 py-2.5 text-muted">{relativeTime(r.last_synced_at)}</td>
                <td className="px-4 py-2.5">
                  {r.last_error ? (
                    <span className="text-bad" title={r.last_error}>
                      error
                    </span>
                  ) : r.watch_enabled ? (
                    <span className="text-ok">watching</span>
                  ) : (
                    <span className="text-muted2">paused</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex gap-1.5">
                    <SmBtn onClick={() => sync.mutate(r.repo_id)}>sync</SmBtn>
                    <SmBtn onClick={() => toggle.mutate({ id: r.repo_id, on: !r.watch_enabled })}>
                      {r.watch_enabled ? "pause" : "resume"}
                    </SmBtn>
                    <SmBtn onClick={() => remove.mutate(r.repo_id)}>remove</SmBtn>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function SmBtn({
  children,
  onClick,
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="rounded-md border border-border2 bg-bg px-2.5 py-1 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg"
    >
      {children}
    </button>
  );
}
