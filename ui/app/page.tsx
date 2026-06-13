"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, Tile, Btn, LiveBadge } from "@/components/shell/primitives";
import type { AuditEntryView } from "@/lib/types";

export default function CommandCenter() {
  const [dismissed, setDismissed] = useState(false);
  const client = getMemoryClient();

  const config = useQuery({ queryKey: ["config"], queryFn: () => client.getConfig() });
  const status = useQuery({ queryKey: ["status"], queryFn: () => client.status(), refetchInterval: 30_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => client.health(), refetchInterval: 15_000 });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => client.listRepos(), refetchInterval: 60_000 });
  const audit = useQuery({ queryKey: ["audit-tail"], queryFn: () => client.auditTail(50), refetchInterval: 2_000 });

  const s = status.data;
  const repoList = repos.data?.repos ?? [];
  const totalUnits = repoList.reduce((a, r) => a + r.units, 0);
  const entries = (audit.data?.entries ?? []).slice().reverse();
  const chainLen = audit.data?.chain_length ?? 0;
  const components = health.data?.components ?? [];
  const embeddingsOn = s?.embeddings_enabled ?? false;
  const cfg = config.data;
  const needsSetup = cfg ? !cfg.onboarding_completed && !cfg.configured : false;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Command Center"
        actions={<Btn primary href="/repositories">+ Add repo</Btn>}
      />

      {needsSetup && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-border2 bg-accentSoft px-4 py-3.5">
          <Sparkles size={18} className="flex-none text-accentInk" />
          <div>
            <div className="text-[14px] font-semibold text-accentInk">Welcome — finish setup</div>
            <div className="text-[12.5px] text-muted2">
              Generate your access key and connect an agent to get started.
            </div>
          </div>
          <Btn primary href="/setup" className="ml-auto">
            Start setup →
          </Btn>
        </div>
      )}

      {!dismissed && (
        <div className="mb-4 flex items-center gap-2.5 rounded-xl border border-[#f3e2c0] bg-warnSoft px-3.5 py-2.5 text-[13px] text-[#8a5a00]">
          <AlertTriangle size={15} />
          <span>
            Audit chain is in-memory — it resets when the server restarts.{" "}
            <span className="opacity-80">Persistent log is a planned upgrade.</span>
          </span>
          <button className="ml-auto text-[12px] font-semibold" onClick={() => setDismissed(true)}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* live metric tiles */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Units indexed"
          value={totalUnits.toLocaleString()}
          sub={`${repoList.length} repositories`}
          spark={[40, 55, 48, 70, 62, 90]}
        />
        <Tile
          label="Tool calls (chain)"
          value={chainLen}
          sub={`${entries.length} recent`}
          spark={[30, 50, 40, 65, 55, 85]}
        />
        <Tile
          label="MCP tools"
          value={s?.mcp_tool_count ?? "—"}
          sub={s ? `${s.environment} · schema ${s.schema_version}` : "—"}
          spark={[88, 90, 86, 92, 89, 91]}
        />
        <Tile
          label="Vector channel"
          value={embeddingsOn ? "100" : "off"}
          unit={embeddingsOn ? "%" : undefined}
          sub={embeddingsOn ? "embeddings healthy" : "embeddings disabled"}
          spark={embeddingsOn ? [88, 90, 86, 92, 89, 91] : [10, 10, 10, 10, 10, 10]}
        />
      </div>

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-3.5">
          {/* live activity */}
          <Panel
            title="Live activity"
            live
            action={<Btn href="/activity" className="!border-0 !px-0 !text-[12px] !text-muted">open monitor →</Btn>}
            bodyClass="p-1.5"
          >
            {entries.length === 0 ? (
              <div className="px-3 py-8 text-center text-[13px] text-muted">
                No tool calls yet — the feed lights up as agents query Memory-CL.
              </div>
            ) : (
              <div className="space-y-px">
                {entries.slice(0, 8).map((e) => (
                  <FeedRow key={e.seq} entry={e} />
                ))}
              </div>
            )}
          </Panel>

          {/* repositories quick list */}
          <Panel title="Repositories">
            <div className="px-2.5 py-1">
              {repoList.length === 0 ? (
                <div className="px-1 py-5 text-center text-[13px] text-muted">No repositories ingested.</div>
              ) : (
                repoList.map((r) => (
                  <div
                    key={r.repo_id}
                    className="flex items-center justify-between border-b border-border py-2 text-[13px] last:border-0"
                  >
                    <span>
                      <b className="font-semibold">{r.repo_id}</b>{" "}
                      <span className="text-muted">· {r.languages.slice(0, 3).join(", ")}</span>
                    </span>
                    <b className="font-semibold tabular-nums">{r.units.toLocaleString()}</b>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>

        <div className="space-y-3.5">
          {/* system pulse */}
          <Panel title="System pulse">
            <div>
              {components.length === 0 ? (
                <div className="px-3 py-4 text-[13px] text-muted">Loading components…</div>
              ) : (
                components.map((c) => (
                  <div key={c.name} className="flex items-center gap-2.5 px-3 py-2.5">
                    <span
                      className={`h-2.5 w-2.5 rounded-full ${
                        c.status === "ok" ? "bg-ok shadow-[0_0_0_3px_rgba(14,159,110,0.13)]" : "bg-warn"
                      }`}
                    />
                    <span className="font-medium capitalize">{c.name}</span>
                    <span className="ml-auto font-mono text-[12px] text-muted">
                      {c.status === "ok" ? "ok" : c.status}
                      {c.latency_ms != null ? ` · ${c.latency_ms.toFixed(0)}ms` : ""}
                    </span>
                  </div>
                ))
              )}
              <div className="flex items-center gap-2.5 border-t border-border px-3 py-2.5">
                <span className="h-2.5 w-2.5 rounded-full bg-ok shadow-[0_0_0_3px_rgba(14,159,110,0.13)]" />
                <span className="font-medium">Audit chain</span>
                <span className="ml-auto font-mono text-[12px] text-muted">{chainLen} · intact</span>
              </div>
            </div>
          </Panel>

          {/* quick actions */}
          <Panel title="Quick actions">
            <div className="flex flex-wrap gap-2 p-2.5">
              <Btn href="/ask">✦ Ask</Btn>
              <Btn href="/repositories">＋ Add repo</Btn>
              <Btn href="/agents">⚇ Connect agent</Btn>
              <Btn href="/snapshots">⧉ Snapshot</Btn>
              <Btn href="/activity">≣ Activity</Btn>
            </div>
          </Panel>

          {/* boot health */}
          <Panel title={<span className="flex items-center gap-2">Boot {s ? <span className="text-muted font-normal">{s.boot_stages.filter((b) => b.status === "ok").length}/{s.boot_stages.length} ok</span> : null}</span>}>
            <div className="px-3 py-1.5">
              {s ? (
                <div className="flex flex-wrap gap-1.5">
                  {s.boot_stages.map((b) => (
                    <span
                      key={b.name}
                      className={`rounded-md px-2 py-1 text-[11px] font-medium ${
                        b.status === "ok"
                          ? "bg-accentSoft text-accentInk"
                          : "bg-warnSoft text-warn"
                      }`}
                    >
                      {b.name}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="py-3 text-[13px] text-muted">Loading…</div>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function FeedRow({ entry }: { entry: AuditEntryView }) {
  const p = entry.payload as Record<string, unknown>;
  const meta = (p.metadata as Record<string, unknown>) ?? p;
  const tool = (meta.tool as string) ?? (p.action as string) ?? (p.event as string) ?? "event";
  const statusVal = ((meta.status as string) ?? (p.status as string) ?? "ok").toLowerCase();
  const actor = (p.actor as string) ?? (meta.user_scope as string) ?? "agent";
  const latency = meta.latency_ms as number | undefined;
  const dotClass =
    statusVal === "failed" || statusVal === "error"
      ? "bg-bad"
      : statusVal === "running"
        ? "bg-warn animate-blink-fast"
        : "bg-ok";

  return (
    <div className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-panel">
      <span className={`h-2 w-2 flex-none rounded-full ${dotClass}`} />
      <span className="font-mono text-[12.5px] font-semibold text-accentInk">{tool}</span>
      <div className="ml-auto flex items-center gap-3 text-[11.5px] tabular-nums text-muted">
        <span>{actor}</span>
        <span className="font-mono">#{entry.seq}</span>
        {latency != null ? <span className="font-mono">{latency.toFixed(0)}ms</span> : null}
      </div>
    </div>
  );
}
