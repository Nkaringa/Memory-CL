"use client";

import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sparkles, Database, GitGraph, Search, Activity } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { fmtScore } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import type { ContextEntry, FeatureWeightsView, RetrieveResponse } from "@/lib/types";

/** Mandated Phase-4 weights — FALLBACK only. The live values are served
 *  by /status (`feature_weights`); these constants cover older backends
 *  that don't expose them yet. */
export const FEATURE_WEIGHTS: FeatureWeightsView = {
  semantic: 0.35,
  graph: 0.25,
  recency: 0.2,
  importance: 0.15,
  feedback: 0.05,
};

interface ExplainPanelProps {
  result: RetrieveResponse;
  entry: ContextEntry;
  className?: string;
}

const CHANNEL_META: Record<string, {
  Icon: typeof Search;
  label: string;
  hint: string;
}> = {
  vector:   { Icon: Search,   label: "vector",   hint: "semantic similarity over embedded units (requires embeddings enabled)" },
  graph:    { Icon: GitGraph, label: "graph",    hint: "BFS proximity from the seed node" },
  metadata: { Icon: Database, label: "metadata", hint: "ILIKE match on canonical Postgres" },
};

/** "Why this result?" — reader-first, not raw JSON.
 *
 *  Layout (top to bottom):
 *    1. Plain-language verdict — what + why in one sentence
 *    2. Identity row — qname / file / kind / band
 *    3. Retrieval source — which channels contributed
 *    4. Visual ranking-formula breakdown — bars per feature
 */
export function ExplainPanel({ result, entry, className }: ExplainPanelProps) {
  // Same key as the dashboard's status query → served from the shared
  // react-query cache; no extra round-trip in the common case.
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
  });
  const weights = status.data?.feature_weights ?? FEATURE_WEIGHTS;

  const channels = useMemo(
    () => ((entry.data?.channels as string[] | undefined) ?? []).filter(Boolean),
    [entry.data?.channels],
  );
  const qname = (entry.data?.qualified_name as string | undefined) ?? "—";
  const fp = (entry.data?.file_path as string | undefined) ?? "—";
  const kind = (entry.data?.kind as string | undefined) ?? "—";

  const features = useMemo(
    () => [
      {
        key: "semantic_similarity",
        label: "Semantic similarity",
        weight: weights.semantic,
        applies: channels.includes("vector"),
        hint: "Cosine of the query embedding against the unit's vector",
      },
      {
        key: "graph_proximity",
        label: "Graph proximity",
        weight: weights.graph,
        applies: channels.includes("graph"),
        hint: "1 − (depth / max_depth) — closer to seed scores higher",
      },
      {
        key: "recency",
        label: "Recency",
        weight: weights.recency,
        applies: channels.includes("metadata"),
        hint: "Exponential decay on the unit's updated_at",
      },
      {
        key: "importance",
        label: "Importance",
        weight: weights.importance,
        applies: true,
        hint: "Saturating sqrt of incoming-edge count",
      },
      {
        key: "user_feedback",
        label: "User feedback",
        weight: weights.feedback,
        applies: false,
        hint: "Reserved — Phase-6 collects, Phase-12+ wires it in",
      },
    ],
    [channels, weights],
  );

  const maxWeight = Math.max(...features.map((f) => f.weight), 1e-9);

  const summary = buildSummary({
    score: entry.score,
    confidence: result.packet.confidence,
    type: entry.type,
    kind,
    channels,
  });

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles size={14} className="text-accent" /> Why this result?
        </CardTitle>
        <Badge variant="accent">score {fmtScore(entry.score)}</Badge>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">

        <div className="rounded-md border border-border bg-bg/30 p-3 leading-relaxed text-fg/90">
          {summary}
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <Field label="qualified_name" value={qname} mono />
          <Field label="file_path" value={fp} mono />
          <Field label="kind" value={kind} mono />
          <Field label="entry type" value={entry.type} />
        </div>

        <div>
          <div className="text-xs muted mb-2">Retrieval source</div>
          <div className="flex flex-wrap gap-2">
            {channels.length === 0 && (
              <span className="text-xs muted">no channel attribution recorded</span>
            )}
            {channels.map((c) => {
              const meta = CHANNEL_META[c] ?? {
                Icon: Activity, label: c, hint: c,
              };
              return (
                <Tooltip key={c} content={meta.hint}>
                  <Badge variant="accent" className="cursor-help gap-1.5">
                    <meta.Icon size={10} /> {meta.label}
                  </Badge>
                </Tooltip>
              );
            })}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-xs muted">Ranking formula</div>
            <div className="text-[10px] muted font-mono">
              {weights.semantic.toFixed(2)}·sem + {weights.graph.toFixed(2)}·graph
              {" + "}{weights.recency.toFixed(2)}·rec + {weights.importance.toFixed(2)}·imp
              {" + "}{weights.feedback.toFixed(2)}·feedback
            </div>
          </div>
          <ul className="space-y-1.5">
            {features.map(({ key, ...rest }) => (
              <FeatureRow key={key} maxWeight={maxWeight} {...rest} />
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

function FeatureRow({
  label, weight, applies, hint, maxWeight,
}: {
  label: string;
  weight: number;
  applies: boolean;
  hint: string;
  maxWeight: number;
}) {
  // Bar width as a percentage of the largest weight (largest = 100%).
  const widthPct = (weight / maxWeight) * 100;
  return (
    <li className="grid grid-cols-[140px_1fr_56px_72px] items-center gap-3 text-xs">
      <Tooltip content={hint}>
        <span className="cursor-help text-fg/90 truncate">{label}</span>
      </Tooltip>
      <div className="h-1.5 rounded-full bg-border overflow-hidden">
        <div
          className={applies ? "bg-accent h-full" : "bg-muted/40 h-full"}
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <span className="font-mono text-muted text-right">{weight.toFixed(2)}</span>
      <span
        className={
          applies
            ? "font-mono text-accent text-right"
            : "font-mono muted text-right"
        }
      >
        {applies ? "applies" : "no signal"}
      </span>
    </li>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] muted mb-0.5 uppercase tracking-wider">{label}</div>
      <div className={mono ? "font-mono text-xs break-all" : "text-xs"}>{value}</div>
    </div>
  );
}

function buildSummary({
  score, confidence, type, kind, channels,
}: {
  score: number; confidence: number; type: string; kind: string; channels: string[];
}): ReactNode {
  const channelCount = channels.length;
  let channelLabel: ReactNode;
  if (channelCount === 0) {
    channelLabel = <span className="muted">no channels</span>;
  } else if (channelCount === 1) {
    channelLabel = (
      <span>
        <span className="text-accent font-mono">{channels[0]}</span> alone
      </span>
    );
  } else {
    channelLabel = (
      <span>
        {channelCount === 2 ? "both " : "all "}
        <span className="text-accent font-mono">{channels.join(" + ")}</span>
      </span>
    );
  }

  return (
    <p>
      This{" "}
      <span className="font-mono text-fg">{kind}</span> entry surfaced via{" "}
      {channelLabel}, scoring{" "}
      <span className="font-mono text-fg">{fmtScore(score)}</span>{" "}
      under the mandated Phase-4 ranking formula. It was placed in the{" "}
      <span className="font-mono text-fg">{type}</span> priority band by the
      context assembler. Packet-level confidence is{" "}
      <span className="font-mono text-fg">{fmtScore(confidence)}</span>.
    </p>
  );
}
