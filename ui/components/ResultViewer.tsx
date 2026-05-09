"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Activity, Search, Boxes, GitGraph, Database } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { JsonView } from "@/components/ui/json-view";
import { ExplainPanel } from "@/components/ExplainPanel";
import { fmtMs, fmtScore, truncHash } from "@/lib/utils";
import type { ContextEntry, RetrieveResponse } from "@/lib/types";

interface ResultViewerProps {
  result: RetrieveResponse;
}

export function ResultViewer({ result }: ResultViewerProps) {
  const [advanced, setAdvanced] = useState(false);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Result · packet</CardTitle>
          <div className="flex items-center gap-3">
            <Switch
              id="adv-toggle"
              checked={advanced}
              onCheckedChange={setAdvanced}
              label="advanced"
            />
            <Badge variant="muted">request_id {truncHash(result.query_id, 8)}</Badge>
            <Badge variant="muted">latency {fmtMs(result.latency_ms)}</Badge>
            <Badge variant="muted">confidence {fmtScore(result.packet.confidence)}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <PipelineTrace result={result} />
          <ChannelHits result={result} />

          <Tabs defaultValue="ranked">
            <TabsList>
              <TabsTrigger value="ranked">Ranked results</TabsTrigger>
              <TabsTrigger value="packet">Packet</TabsTrigger>
              {advanced && <TabsTrigger value="raw">Raw JSON</TabsTrigger>}
            </TabsList>

            <TabsContent value="ranked">
              <RankedList result={result} advanced={advanced} />
            </TabsContent>
            <TabsContent value="packet">
              <PacketSummary result={result} />
            </TabsContent>
            {advanced && (
              <TabsContent value="raw">
                <JsonView value={result} />
              </TabsContent>
            )}
          </Tabs>
        </CardContent>
        <CardFooter>
          <span>
            failed channels:{" "}
            {result.failed_channels.length === 0
              ? "none"
              : result.failed_channels.join(", ")}
          </span>
          <span className="font-mono">
            {result.ranked_count} of {result.final_candidates} candidates
          </span>
        </CardFooter>
      </Card>
    </div>
  );
}

function ChannelHits({ result }: { result: RetrieveResponse }) {
  const hits = [
    { Icon: GitGraph, label: "graph", value: result.graph_hits },
    { Icon: Search, label: "vector", value: result.vector_hits },
    { Icon: Database, label: "metadata", value: result.metadata_hits },
  ];
  return (
    <div className="grid grid-cols-3 gap-3">
      {hits.map(({ Icon, label, value }) => (
        <div
          key={label}
          className="rounded-md border border-border bg-bg/40 px-3 py-3 flex items-center gap-3"
        >
          <Icon size={16} className="text-accent" />
          <div className="flex-1">
            <div className="text-xs muted">{label}</div>
            <div className="text-lg font-semibold mono">{value}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function PipelineTrace({ result }: { result: RetrieveResponse }) {
  const stages = [
    { Icon: Boxes,   label: "ingestion",       hint: "Phase 2/3 already applied" },
    { Icon: Search,  label: "retrieval",       hint: `${result.final_candidates} candidates` },
    { Icon: Activity,label: "ranking",         hint: `${result.ranked_count} ranked` },
    { Icon: GitGraph,label: "context assembly",hint: `${result.packet.context.length} entries` },
  ];
  return (
    <div className="rounded-md border border-border bg-bg/30 px-3 py-2 flex items-center gap-2 overflow-x-auto">
      {stages.map(({ Icon, label, hint }, i) => (
        <div key={label} className="flex items-center gap-2 shrink-0">
          <div className="flex items-center gap-2 px-2 py-1 rounded bg-panel border border-border">
            <Icon size={14} className="text-accent" />
            <div className="leading-tight">
              <div className="text-xs">{label}</div>
              <div className="text-[10px] muted">{hint}</div>
            </div>
          </div>
          {i < stages.length - 1 && <ChevronRight size={14} className="text-muted" />}
        </div>
      ))}
    </div>
  );
}

function PacketSummary({ result }: { result: RetrieveResponse }) {
  const p = result.packet;
  const blocks: Array<{ label: string; value: string[] }> = [
    { label: "constraints", value: p.constraints },
    { label: "risks", value: p.risks },
    { label: "changes", value: p.changes },
  ];
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="text-xs muted">task</div>
        <div className="mono">{p.task || "—"}</div>
      </div>
      {blocks.map((b) => (
        <div key={b.label}>
          <div className="text-xs muted">{b.label}</div>
          <div className="font-mono text-xs">
            {b.value.length === 0 ? "—" : b.value.join(", ")}
          </div>
        </div>
      ))}
    </div>
  );
}

function RankedList({ result, advanced }: { result: RetrieveResponse; advanced: boolean }) {
  if (result.packet.context.length === 0) {
    return <div className="text-sm muted">no entries — try a different query.</div>;
  }
  return (
    <ul className="divide-y divide-border">
      {result.packet.context.map((entry) => (
        <RankedRow key={entry.id} entry={entry} result={result} advanced={advanced} />
      ))}
    </ul>
  );
}

function RankedRow({
  entry, result, advanced,
}: { entry: ContextEntry; result: RetrieveResponse; advanced: boolean }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="py-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left flex items-center gap-3"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <div className="flex-1 min-w-0">
          <div className="font-mono text-sm truncate">
            {(entry.data?.qualified_name as string) ?? entry.id}
          </div>
          <div className="text-xs muted truncate">
            {(entry.data?.file_path as string) ?? "—"}
          </div>
        </div>
        <Badge variant={entry.type === "constraint" || entry.type === "risk" ? "warn" : "muted"}>
          {entry.type}
        </Badge>
        <Badge variant="accent">{fmtScore(entry.score)}</Badge>
        <span className="text-[10px] muted font-mono">
          {((entry.data?.channels as string[] | undefined) ?? []).join(",")}
        </span>
      </button>

      {expanded && (
        <div className="mt-3 pl-6 grid grid-cols-1 lg:grid-cols-2 gap-3">
          <ExplainPanel result={result} entry={entry} />
          {advanced && <JsonView value={entry} maxHeight="40vh" />}
        </div>
      )}
    </li>
  );
}
