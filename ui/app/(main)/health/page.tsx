"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel, Tile, Btn } from "@/components/shell/primitives";

export default function HealthPage() {
  const client = getMemoryClient();

  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => client.status(),
    refetchInterval: 30_000,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client.health(),
    refetchInterval: 15_000,
  });

  const s = status.data;
  const components = health.data?.components ?? [];
  const overall = health.data?.status ?? (status.isLoading ? "…" : "unknown");
  const bootStages = s?.boot_stages ?? [];
  const bootOk = bootStages.filter((b) => b.status === "ok").length;
  const embeddingsOn = s?.embeddings_enabled ?? false;
  const safeMode = s?.safe_mode?.enabled ?? false;

  const recheck = () => {
    status.refetch();
    health.refetch();
  };

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Health"
        subtitle="Live component health and boot diagnostics."
        actions={
          <Btn onClick={recheck}>
            <RefreshCw size={14} /> Re-check now
          </Btn>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="status"
          value={<span className={overall === "ok" ? "text-ok" : overall === "degraded" ? "text-warn" : ""}>{overall === "ok" ? "healthy" : overall}</span>}
          sub={`schema ${health.data?.schema_version ?? s?.schema_version ?? "—"}`}
        />
        <Tile
          label="embeddings"
          value={<span className={embeddingsOn ? "text-ok" : "text-muted"}>{embeddingsOn ? "on" : "off"}</span>}
          sub={embeddingsOn ? "vector channel live" : "disabled"}
        />
        <Tile label="MCP tools" value={s?.mcp_tool_count ?? "—"} sub={s?.environment ?? "—"} />
        <Tile
          label="safe mode"
          value={<span className={safeMode ? "text-warn" : ""}>{safeMode ? "on" : "off"}</span>}
          sub={safeMode ? s?.safe_mode?.reason || "engaged" : "normal operation"}
        />
      </div>

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
        {/* boot stages */}
        <Panel title={<span className="flex items-center gap-2">Boot stages {bootStages.length ? <span className="font-normal text-muted">{bootOk}/{bootStages.length}</span> : null}</span>}>
          <div className="px-3 py-1">
            {bootStages.length === 0 ? (
              <div className="px-1 py-5 text-[13px] text-muted">Loading boot diagnostics…</div>
            ) : (
              bootStages
                .slice()
                .sort((a, b) => a.order - b.order)
                .map((b) => (
                  <div
                    key={b.name}
                    className="flex items-center justify-between border-b border-border py-2 text-[13px] last:border-0"
                  >
                    <span className="font-mono text-[12.5px]">{b.name}</span>
                    <span
                      className={
                        b.status === "ok" ? "text-ok" : b.status === "degraded" ? "text-warn" : "text-bad"
                      }
                    >
                      {b.status === "ok" ? "✓" : b.status}
                    </span>
                  </div>
                ))
            )}
          </div>
        </Panel>

        {/* components */}
        <Panel title="Components">
          <div>
            {components.length === 0 ? (
              <div className="px-3 py-5 text-[13px] text-muted">
                {health.isLoading ? "Loading components…" : "No component health reported."}
              </div>
            ) : (
              components.map((c) => (
                <div key={c.name} className="flex items-center gap-2.5 px-3 py-2.5">
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      c.status === "ok"
                        ? "bg-ok shadow-[0_0_0_3px_rgba(14,159,110,0.13)]"
                        : c.status === "degraded"
                          ? "bg-warn"
                          : "bg-bad"
                    }`}
                  />
                  <span className="font-medium capitalize">{c.name}</span>
                  <span className="ml-auto font-mono text-[12px] text-muted">
                    {c.status}
                    {c.latency_ms != null ? ` · ${c.latency_ms.toFixed(0)}ms` : ""}
                  </span>
                </div>
              ))
            )}
          </div>
        </Panel>
      </div>

      {(status.isError || health.isError) && (
        <div className="mt-3 rounded-xl border border-[#f0c9c9] bg-[#fef2f2] px-4 py-2.5 text-[13px] text-bad">
          Failed to reach the health endpoints. The server may be down or restarting.
        </div>
      )}
    </div>
  );
}
