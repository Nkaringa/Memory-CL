"use client";

import { useState } from "react";
import { ArrowLeftRight, Check, X } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { JsonView } from "@/components/ui/json-view";
import { Tooltip } from "@/components/ui/tooltip";
import { cn, truncHash } from "@/lib/utils";
import type { SnapshotResponse } from "@/lib/types";

interface SnapshotDiffProps {
  left: SnapshotResponse | null;
  right: SnapshotResponse | null;
  className?: string;
}

const COMPONENT_KEYS = [
  "graph_state_hash",
  "embedding_index_hash",
  "retrieval_config_hash",
  "schema_version",
  "mcp_registry_hash",
  "state_version_token",
] as const;

const KEY_DESCRIPTIONS: Record<typeof COMPONENT_KEYS[number], string> = {
  graph_state_hash: "Sorted SHA-256 over all nodes + edges in the Phase-2 graph.",
  embedding_index_hash: "Per-vector SHA-256 fingerprints of the embedding index.",
  retrieval_config_hash: "Hash of FeatureWeights and threshold knobs that drive ranking.",
  schema_version: "Global wire-schema version from schemas.base.",
  mcp_registry_hash: "MCP tool names + request schemas (any registry change ⇒ different hash).",
  state_version_token: "Phase-8 monotonic version token. Advances on every state mutation.",
};

export function SnapshotDiff({ left, right, className }: SnapshotDiffProps) {
  const [swapped, setSwapped] = useState(false);
  const [a, b] = swapped ? [right, left] : [left, right];
  const [aLabel, bLabel] = swapped ? ["B", "A"] : ["A", "B"];

  const identical = a && b && a.snapshot_id === b.snapshot_id;
  const driftCount = a && b
    ? COMPONENT_KEYS.filter((k) => a.components[k] !== b.components[k]).length
    : 0;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ArrowLeftRight size={14} className="text-accent" />
          Snapshot diff · components
        </CardTitle>
        <div className="flex items-center gap-2">
          {a && b && (
            <Badge variant={identical ? "ok" : driftCount > 1 ? "bad" : "warn"}>
              {identical ? "identical" : `${driftCount} drift${driftCount === 1 ? "" : "s"}`}
            </Badge>
          )}
          {a && b && (
            <Tooltip content="Swap A ↔ B" side="left">
              <Button
                size="sm" variant="ghost"
                onClick={() => setSwapped((v) => !v)}
                className="h-7 w-7 p-0" aria-label="swap snapshots"
              >
                <ArrowLeftRight size={14} />
              </Button>
            </Tooltip>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">

        <div className="grid grid-cols-2 gap-3">
          <SnapshotCard label={aLabel} snap={a} />
          <SnapshotCard label={bLabel} snap={b} />
        </div>

        {a && b && (
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs font-mono">
              <thead className="bg-bg/40 muted text-left">
                <tr className="border-b border-border">
                  <th className="font-normal px-3 py-2">component</th>
                  <th className="font-normal px-3 py-2">{aLabel}</th>
                  <th className="font-normal px-3 py-2">{bLabel}</th>
                  <th className="font-normal px-3 py-2 w-16">Δ</th>
                </tr>
              </thead>
              <tbody>
                {COMPONENT_KEYS.map((key) => {
                  const av = String(a.components[key]);
                  const bv = String(b.components[key]);
                  const same = av === bv;
                  return (
                    <tr
                      key={key}
                      className={cn(
                        "border-t border-border/60",
                        // Highlight changed rows so the eye lands on them
                        // before reading the row contents.
                        !same && "bg-warn/5",
                      )}
                    >
                      <td className="px-3 py-2 align-top">
                        <Tooltip content={KEY_DESCRIPTIONS[key]} side="right">
                          <span
                            className={cn(
                              "cursor-help underline decoration-dotted decoration-border underline-offset-2",
                              same ? "text-fg" : "text-warn",
                            )}
                          >
                            {key}
                          </span>
                        </Tooltip>
                      </td>
                      <td className={cn(
                        "px-3 py-2 align-top",
                        same ? "muted" : "text-fg",
                      )}>
                        {truncHash(av, 14)}
                      </td>
                      <td className={cn(
                        "px-3 py-2 align-top",
                        same ? "muted" : "text-fg",
                      )}>
                        {truncHash(bv, 14)}
                      </td>
                      <td className="px-3 py-2 align-top">
                        {same ? (
                          <span className="inline-flex items-center gap-1 text-ok text-[11px]">
                            <Check size={10} /> match
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-warn text-[11px]">
                            <X size={10} /> drift
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {a && b && !identical && (
          <p className="text-xs muted leading-relaxed">
            Drift is not a bug — it just means the system advanced legitimately
            between the two captures. Replay against {bLabel} to confirm
            determinism still holds for the operations you care about.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SnapshotCard({
  label, snap,
}: {
  label: string;
  snap: SnapshotResponse | null;
}) {
  if (!snap) {
    return (
      <div className="rounded-md border border-dashed border-border bg-bg/30 p-3 text-xs muted">
        {label}: build a snapshot to inspect
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border bg-bg/30 p-3 space-y-1">
      <div className="text-[10px] font-mono muted uppercase tracking-wider">
        snapshot {label}
      </div>
      <div className="font-mono text-xs break-all">
        {truncHash(snap.snapshot_id, 16)}
      </div>
      <div className="text-[10px] muted">
        {snap.tenant_id} · {snap.captured_at}
      </div>
      <details className="pt-2">
        <summary className="text-[10px] muted cursor-pointer hover:text-fg">
          components
        </summary>
        <JsonView value={snap.components} maxHeight="20rem" />
      </details>
    </div>
  );
}
