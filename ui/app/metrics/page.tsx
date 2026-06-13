"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, Tile } from "@/components/shell/primitives";
import type { AuditEntryView } from "@/lib/types";

function describe(entry: AuditEntryView) {
  const p = entry.payload as Record<string, unknown>;
  const meta = (p.metadata as Record<string, unknown>) ?? p;
  const tool =
    (meta.tool as string) ?? (p.action as string) ?? (p.event as string) ?? "event";
  const statusVal = ((meta.status as string) ?? (p.status as string) ?? "ok").toLowerCase();
  const latency = typeof meta.latency_ms === "number" ? (meta.latency_ms as number) : null;
  return { tool, statusVal, latency };
}

function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  const v = sorted[idx];
  return v ?? null;
}

export default function MetricsPage() {
  const client = getMemoryClient();
  const audit = useQuery({
    queryKey: ["audit-tail-metrics"],
    queryFn: () => client.auditTail(50),
    refetchInterval: 5_000,
  });

  const m = useMemo(() => {
    const entries = audit.data?.entries ?? [];
    const rows = entries.map(describe);
    const byTool = new Map<string, number>();
    const byStatus = new Map<string, number>();
    const latencies: number[] = [];
    for (const r of rows) {
      byTool.set(r.tool, (byTool.get(r.tool) ?? 0) + 1);
      byStatus.set(r.statusVal, (byStatus.get(r.statusVal) ?? 0) + 1);
      if (r.latency != null) latencies.push(r.latency);
    }
    latencies.sort((a, b) => a - b);
    const failed =
      (byStatus.get("failed") ?? 0) + (byStatus.get("error") ?? 0);
    return {
      total: rows.length,
      toolCounts: Array.from(byTool.entries()).sort((a, b) => b[1] - a[1]),
      failed,
      p50: percentile(latencies, 50),
      p95: percentile(latencies, 95),
      p99: percentile(latencies, 99),
      maxLat: latencies.length ? (latencies[latencies.length - 1] ?? null) : null,
      latencyCount: latencies.length,
    };
  }, [audit.data]);

  const chainLen = audit.data?.chain_length ?? 0;
  const maxToolCount = Math.max(1, ...m.toolCounts.map(([, c]) => c));

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Metrics"
        subtitle="Derived live from the audit tail. Richer trends need a planned /metrics endpoint."
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Tile
          label="Tool calls (chain)"
          value={chainLen}
          sub={`${m.total} in current tail · live from audit`}
        />
        <Tile
          label="Failed calls (tail)"
          value={m.failed}
          sub={m.total ? `${((m.failed / m.total) * 100).toFixed(0)}% of last ${m.total}` : "—"}
        />
        <Tile
          label="Distinct tools"
          value={m.toolCounts.length}
          sub="seen in current tail"
        />
      </div>

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
        {/* latency percentiles */}
        <Panel title="Latency (p50 / p95 / p99) — live from audit">
          <div className="px-4 py-3">
            {m.latencyCount === 0 ? (
              <div className="py-4 text-[13px] text-muted">
                No latency-bearing entries in the current tail yet.
              </div>
            ) : (
              <>
                <Bar label="p50" value={m.p50} max={m.maxLat} />
                <Bar label="p95" value={m.p95} max={m.maxLat} />
                <Bar label="p99" value={m.p99} max={m.maxLat} />
              </>
            )}
            <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2 text-[12px] text-[#8a5a00]">
              Percentiles over the last {m.latencyCount} entries with a recorded latency. Stable
              latency trends need a dedicated <span className="font-mono">/metrics</span> endpoint.
            </div>
          </div>
        </Panel>

        {/* channel hit-rate — backend gap */}
        <Panel title="Channel hit-rate">
          <div className="px-4 py-3">
            <div className="space-y-1 opacity-60">
              <Bar label="vector" value={null} max={1} />
              <Bar label="keyword" value={null} max={1} />
              <Bar label="graph" value={null} max={1} />
            </div>
            <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2 text-[12px] text-[#8a5a00]">
              How often each retrieval channel contributes a top result. Not exposed by the audit
              tail — needs a backend metrics endpoint to surface honestly.
            </div>
          </div>
        </Panel>
      </div>

      {/* call volume by tool */}
      <Panel className="mt-3.5" title="Call volume by tool — live from audit">
        <div className="px-4 py-4">
          {m.toolCounts.length === 0 ? (
            <div className="py-2 text-[13px] text-muted">No tool calls in the current tail.</div>
          ) : (
            <div className="space-y-2">
              {m.toolCounts.map(([tool, count]) => (
                <div key={tool} className="flex items-center gap-3 text-[12.5px]">
                  <span className="w-36 truncate font-mono text-muted2">{tool}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded bg-panel2">
                    <i
                      className="block h-full rounded bg-accent"
                      style={{ width: `${(count / maxToolCount) * 100}%` }}
                    />
                  </span>
                  <span className="w-8 text-right font-mono tabular-nums text-muted">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}

function Bar({ label, value, max }: { label: string; value: number | null; max: number | null }) {
  const pct = value != null && max ? Math.max(3, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-3 border-b border-border py-2.5 last:border-0 text-[13px]">
      <span className="w-12 text-muted2">{label}</span>
      <span className="h-2 flex-1 overflow-hidden rounded bg-panel2">
        <i className="block h-full rounded bg-accent" style={{ width: `${pct}%` }} />
      </span>
      <span className="w-16 text-right font-mono text-muted">
        {value != null ? `${value.toFixed(0)}ms` : "—"}
      </span>
    </div>
  );
}
