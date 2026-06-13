"use client";

import { useQuery } from "@tanstack/react-query";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel } from "@/components/shell/primitives";
import type { FeatureWeightsView } from "@/lib/types";

// Pinned Phase-4 defaults — used when the backend omits feature_weights.
const DEFAULT_WEIGHTS: FeatureWeightsView = {
  semantic: 0.35,
  graph: 0.25,
  recency: 0.2,
  importance: 0.15,
  feedback: 0.05,
};

const WEIGHT_ORDER: (keyof FeatureWeightsView)[] = [
  "semantic",
  "graph",
  "recency",
  "importance",
  "feedback",
];

export default function SettingsPage() {
  const client = getMemoryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: () => client.status() });

  const s = status.data;
  const weights = s?.feature_weights ?? DEFAULT_WEIGHTS;
  const fromBackend = s?.feature_weights != null;
  const embeddingsOn = s?.embeddings_enabled ?? false;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Settings"
        subtitle="Read-only view of the live engine configuration."
      />

      <Panel title="Ranking weights" className="mb-3.5">
        <div className="px-4 py-4">
          <div className="mb-3 text-[12.5px] text-muted">
            How retrieval channels combine into a result score —{" "}
            {fromBackend ? "served live by the engine." : "engine omitted them; showing pinned Phase-4 defaults."}
          </div>
          {WEIGHT_ORDER.map((k) => (
            <div key={k} className="my-1.5 flex items-center gap-3 text-[12.5px]">
              <span className="w-24 text-muted2">{k}</span>
              <span className="h-2 flex-1 overflow-hidden rounded bg-panel2">
                <i
                  className="block h-full rounded bg-accent"
                  style={{ width: `${Math.min(100, weights[k] * 100)}%` }}
                />
              </span>
              <span className="w-12 text-right font-mono tabular-nums text-muted">
                {weights[k].toFixed(2)}
              </span>
            </div>
          ))}
          <div className="mt-2 text-[11.5px] text-muted">
            sum ={" "}
            <span className="font-mono">
              {WEIGHT_ORDER.reduce((a, k) => a + weights[k], 0).toFixed(2)}
            </span>
          </div>
        </div>
      </Panel>

      <Panel title="Connection">
        <div className="px-4 py-2">
          <Row k="server">
            <span className="font-mono">same-origin /api proxy → backend</span>
          </Row>
          <Row k="API key">
            <span className="font-mono text-muted">injected server-side (MCP_API_KEY)</span>
          </Row>
          <Row k="embeddings">
            {embeddingsOn ? "enabled" : "disabled"}
            {s?.service ? ` · ${s.service}` : ""}
          </Row>
          <Row k="environment">{s?.environment ?? "—"}</Row>
          <Row k="schema version">
            <span className="font-mono">{s?.schema_version ?? "—"}</span>
          </Row>
          <Row k="theme">Light (minimal) · emerald accent</Row>
        </div>
      </Panel>

      {status.isError && (
        <div className="mt-3 rounded-xl border border-[#f0c9c9] bg-[#fef2f2] px-4 py-2.5 text-[13px] text-bad">
          Could not load status — showing defaults where possible.
        </div>
      )}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-[13px] last:border-0">
      <span className="text-muted2">{k}</span>
      <span>{children}</span>
    </div>
  );
}
