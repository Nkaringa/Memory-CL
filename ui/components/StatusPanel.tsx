"use client";

import {
  AlertTriangle, CircleDot, Database, Layers, Settings2,
  ShieldCheck, Workflow, Zap,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Section } from "@/components/ui/section";
import { Skeleton, SkeletonRow } from "@/components/ui/skeleton";
import { computePosture, PostureBadge } from "@/components/ui/posture-badge";
import type { FeatureFlagView, StatusResponse } from "@/lib/types";
import { statusVariant } from "@/lib/utils";

interface StatusPanelProps {
  status: StatusResponse | null;
  className?: string;
}

/** Bucket flags into the four status sections by name prefix. The
 *  taxonomy is a soft hint — if a flag doesn't match a bucket it lands
 *  in MCP/Other so it's still surfaced.
 */
function bucketFlags(flags: FeatureFlagView[]) {
  const memory: FeatureFlagView[] = [];
  const scaling: FeatureFlagView[] = [];
  const mcp: FeatureFlagView[] = [];
  const other: FeatureFlagView[] = [];
  for (const f of flags) {
    const n = f.name.toLowerCase();
    if (/(scal|cache|pool|warm|prefetch|throttle|degrade|safe_mode|safe-mode|backpressure)/.test(n)) {
      scaling.push(f);
    } else if (/(graph|embed|vector|index|store|memory|snapshot|audit|chain)/.test(n)) {
      memory.push(f);
    } else if (/(mcp|tool|registry)/.test(n)) {
      mcp.push(f);
    } else {
      other.push(f);
    }
  }
  return { memory, scaling, mcp, other };
}

export function StatusPanel({ status, className }: StatusPanelProps) {
  if (!status) {
    return (
      <div className={`grid grid-cols-1 md:grid-cols-2 gap-4 ${className ?? ""}`}>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <CardHeader>
              <CardTitle><Skeleton className="h-4 w-32" /></CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const posture = computePosture(status);
  const buckets = bucketFlags(status.feature_flags);
  const failed = status.boot_failed_stages.length;
  const degraded = status.boot_degraded_stages.length;

  return (
    <div className={`space-y-6 ${className ?? ""}`}>

      {/* Headline posture — always above the fold. */}
      <div className="rounded-lg border border-border bg-panel/30 px-5 py-4 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <PostureBadge posture={posture} size="md" />
          <div className="text-xs muted">
            <span className="font-mono">{status.service}</span> ·{" "}
            <span className="font-mono">{status.environment}</span> ·{" "}
            schema <span className="font-mono">{status.schema_version}</span>
          </div>
        </div>
        {(failed > 0 || degraded > 0) && (
          <div className="flex items-center gap-2 text-xs muted">
            {failed > 0 && (
              <Badge variant="bad">
                <AlertTriangle size={10} /> {failed} failed
              </Badge>
            )}
            {degraded > 0 && (
              <Badge variant="warn">{degraded} degraded</Badge>
            )}
          </div>
        )}
      </div>

      {/* Four-pillar grid: System Health · Memory State · MCP Tools · Scaling. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SystemHealthCard status={status} />
        <MemoryStateCard status={status} flags={buckets.memory} />
        <McpToolsCard status={status} flags={buckets.mcp} />
        <ScalingStateCard status={status} flags={buckets.scaling} />
      </div>

      {buckets.other.length > 0 && (
        <Section
          Icon={Settings2}
          title="Other flags"
          description="Flags that don't fit a primary bucket — surfaced here for completeness."
          trailing={<Badge variant="muted">{buckets.other.length}</Badge>}
        >
          <FlagList flags={buckets.other} />
        </Section>
      )}
    </div>
  );
}

function SystemHealthCard({ status }: { status: StatusResponse }) {
  const allOk = status.boot_overall_ok;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck size={14} className="text-accent" /> System Health
        </CardTitle>
        <Badge variant={allOk ? "ok" : "bad"}>
          {allOk ? "all stages OK" : "stages failed"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {status.boot_stages.length === 0 ? (
          <div className="text-xs muted">no boot data captured</div>
        ) : (
          <ol className="space-y-1 text-xs font-mono">
            {status.boot_stages.map((s) => (
              <li
                key={s.name}
                className="grid grid-cols-[28px_1fr_60px] items-center gap-2"
              >
                <span className="muted">#{s.order}</span>
                <span className="truncate">{s.name}</span>
                <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
              </li>
            ))}
          </ol>
        )}
        {status.boot_failed_stages.length > 0 && (
          <div className="rounded-md border border-bad/30 bg-bad/[0.06] px-2.5 py-2 text-[11px] text-bad font-mono">
            failed: {status.boot_failed_stages.join(", ")}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MemoryStateCard({
  status, flags,
}: {
  status: StatusResponse;
  flags: FeatureFlagView[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database size={14} className="text-accent" /> Memory State
        </CardTitle>
        <Badge variant="muted">schema {status.schema_version}</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Row k="environment" v={status.environment} />
        <Row k="schema_version" v={status.schema_version} mono />
        <Row k="service" v={status.service} mono />
        {flags.length > 0 && (
          <div className="border-t border-border pt-2 mt-2 space-y-1.5">
            <div className="text-[10px] font-mono uppercase tracking-wider muted">
              graph + index flags
            </div>
            <FlagList flags={flags} compact />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function McpToolsCard({
  status, flags,
}: {
  status: StatusResponse;
  flags: FeatureFlagView[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Workflow size={14} className="text-accent" /> MCP Tools
        </CardTitle>
        <Badge variant="muted">{status.mcp_tool_count} registered</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs muted leading-relaxed">
          Tools registered in the MCP registry. Each call returns a canonical
          JSON envelope with a request_id pinned for replay.
        </p>
        {flags.length > 0 ? (
          <div className="border-t border-border pt-2 mt-2 space-y-1.5">
            <div className="text-[10px] font-mono uppercase tracking-wider muted">
              registry flags
            </div>
            <FlagList flags={flags} compact />
          </div>
        ) : (
          <div className="text-xs muted">No MCP-specific feature flags.</div>
        )}
      </CardContent>
    </Card>
  );
}

function ScalingStateCard({
  status, flags,
}: {
  status: StatusResponse;
  flags: FeatureFlagView[];
}) {
  const safe = status.safe_mode.enabled;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Layers size={14} className="text-accent" /> Scaling State
        </CardTitle>
        <Badge variant={safe ? "warn" : "ok"}>
          {safe ? "SAFE_MODE" : "nominal"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Row
          k="safe_mode"
          v={safe ? `enabled · ${status.safe_mode.triggered_by}` : "off"}
        />
        {safe && status.safe_mode.reason && (
          <div className="rounded-md border border-warn/30 bg-warn/[0.06] px-2.5 py-2 text-[11px]">
            <div className="text-warn font-mono mb-1">reason</div>
            <div className="text-fg/85 leading-relaxed">
              {status.safe_mode.reason}
            </div>
          </div>
        )}
        {flags.length > 0 && (
          <div className="border-t border-border pt-2 mt-2 space-y-1.5">
            <div className="text-[10px] font-mono uppercase tracking-wider muted">
              cache + pool flags
            </div>
            <FlagList flags={flags} compact />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function FlagList({
  flags, compact = false,
}: {
  flags: FeatureFlagView[];
  compact?: boolean;
}) {
  return (
    <ul className="space-y-1.5 text-xs">
      {flags.map((flag) => (
        <li key={flag.name} className="flex items-center gap-2">
          {flag.enabled ? (
            <Zap size={11} className="text-accent shrink-0" />
          ) : (
            <CircleDot size={11} className="text-muted shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <div className="font-mono truncate">{flag.name}</div>
            {!compact && flag.description && (
              <div className="text-[10px] muted truncate">{flag.description}</div>
            )}
          </div>
          <Badge variant={flag.enabled ? "accent" : "muted"}>
            {flag.enabled ? "on" : "off"}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="muted">{k}</span>
      <span className={mono ? "font-mono" : ""}>{v}</span>
    </div>
  );
}
