"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Filter } from "lucide-react";
import { useMemo, useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, LiveBadge } from "@/components/shell/primitives";
import type { AuditEntryView } from "@/lib/types";

/** Extract the display fields from a heterogeneous audit payload — same
 *  idiom the Command Center FeedRow uses, surfaced at full size here. */
function describe(entry: AuditEntryView) {
  const p = entry.payload as Record<string, unknown>;
  const meta = (p.metadata as Record<string, unknown>) ?? p;
  const tool =
    (meta.tool as string) ?? (p.action as string) ?? (p.event as string) ?? "event";
  const statusVal = ((meta.status as string) ?? (p.status as string) ?? "ok").toLowerCase();
  const actor = (p.actor as string) ?? (meta.user_scope as string) ?? "agent";
  const latency = meta.latency_ms as number | undefined;
  // Best-effort args string from common payload shapes.
  const argSrc =
    (meta.params as Record<string, unknown>) ??
    (p.params as Record<string, unknown>) ??
    (p.args as Record<string, unknown>) ??
    {};
  const arg =
    (argSrc.question as string) ??
    (argSrc.qualified_name as string) ??
    (argSrc.reference as string) ??
    (argSrc.query as string) ??
    (argSrc.repo_id as string) ??
    (p.entity_id as string) ??
    "";
  const ts = (p.timestamp as string) ?? (meta.timestamp as string) ?? null;
  return { tool, statusVal, actor, latency, arg, ts };
}

function relTime(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 5) return "now";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

export default function ActivityPage() {
  const client = getMemoryClient();
  const [toolFilter, setToolFilter] = useState<string>("all");

  const audit = useQuery({
    queryKey: ["audit-tail-50"],
    queryFn: () => client.auditTail(50),
    refetchInterval: 2_000,
  });

  const verify = useMutation({ mutationFn: () => client.auditVerify() });

  const entries = useMemo(
    () => (audit.data?.entries ?? []).slice().reverse(),
    [audit.data],
  );
  const chainLen = audit.data?.chain_length ?? 0;

  const tools = useMemo(() => {
    const set = new Set<string>();
    for (const e of entries) set.add(describe(e).tool);
    return ["all", ...Array.from(set).sort()];
  }, [entries]);

  const shown = entries.filter((e) => toolFilter === "all" || describe(e).tool === toolFilter);

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Live Activity"
        subtitle="Every MCP tool call, as a hash-chained audit entry — polled from GET /audit/tail."
      />

      {/* control bar */}
      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-accentSoft px-2 py-1 text-[10px] font-bold tracking-wide text-accentInk">
          <LiveBadge /> auto-refresh 2s
        </span>
        <span className="rounded-full bg-accentSoft px-2.5 py-1 text-[12px] font-semibold text-accentInk">
          {chainLen} events
          {verify.data ? (verify.data.intact ? " · chain intact" : " · BROKEN") : ""}
        </span>
        <button
          onClick={() => verify.mutate()}
          disabled={verify.isPending}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border2 bg-bg px-2.5 py-1.5 text-[12px] font-medium text-muted2 hover:border-muted hover:text-fg disabled:opacity-50"
        >
          <CheckCircle2 size={13} /> {verify.isPending ? "verifying…" : "verify chain"}
        </button>
        <div className="inline-flex items-center gap-1.5 rounded-lg border border-border2 bg-bg px-2 py-1 text-[12px] text-muted2">
          <Filter size={13} />
          <select
            value={toolFilter}
            onChange={(e) => setToolFilter(e.target.value)}
            className="appearance-none bg-transparent font-mono text-[12px] text-fg outline-none"
          >
            {tools.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>

      {verify.data && !verify.data.intact && (
        <div className="mb-3 rounded-xl border border-[#f0c9c9] bg-[#fef2f2] px-4 py-2.5 text-[13px] text-bad">
          Chain integrity check failed{verify.data.broken_at_seq != null ? ` at seq #${verify.data.broken_at_seq}` : ""}.
        </div>
      )}

      <Panel bodyClass="p-1.5">
        {audit.isLoading ? (
          <div className="px-3 py-10 text-center text-[13px] text-muted">Loading audit tail…</div>
        ) : shown.length === 0 ? (
          <div className="px-3 py-10 text-center text-[13px] text-muted">
            {toolFilter === "all"
              ? "No tool calls yet — the feed lights up as agents query Memory-CL."
              : `No ${toolFilter} calls in the current tail.`}
          </div>
        ) : (
          <div className="space-y-px">
            {shown.map((e) => (
              <Row key={e.seq} entry={e} />
            ))}
          </div>
        )}
      </Panel>

      <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3.5 py-2.5 text-[12px] text-[#8a5a00]">
        Driven by <span className="font-mono">GET /audit/tail</span> polling — real today, no backend
        change. Each row is a hash-chain entry; the chain is in-memory and resets on server restart.
      </div>
    </div>
  );
}

function Row({ entry }: { entry: AuditEntryView }) {
  const { tool, statusVal, actor, latency, arg, ts } = describe(entry);
  const dotClass =
    statusVal === "failed" || statusVal === "error"
      ? "bg-bad"
      : statusVal === "running"
        ? "bg-warn animate-blink-fast"
        : "bg-ok";
  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-panel">
      <span className={`h-2 w-2 flex-none rounded-full ${dotClass}`} />
      <span className="font-mono text-[12.5px] font-semibold text-accentInk">{tool}</span>
      {arg && (
        <span className="max-w-[280px] truncate font-mono text-[12px] text-muted2">{arg}</span>
      )}
      <div className="ml-auto flex items-center gap-3.5 text-[11.5px] tabular-nums text-muted">
        <span>{actor}</span>
        <span className="font-mono">#{entry.seq}</span>
        {latency != null ? <span className="font-mono">{latency.toFixed(0)}ms</span> : null}
        {ts ? <span className="w-9 text-right">{relTime(ts)}</span> : null}
      </div>
    </div>
  );
}
