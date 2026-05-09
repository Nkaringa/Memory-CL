"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, Link2 } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { JsonView } from "@/components/ui/json-view";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton, SkeletonRow } from "@/components/ui/skeleton";
import { computePosture, PostureBadge } from "@/components/ui/posture-badge";
import { StatusPanel } from "@/components/StatusPanel";
import { getMemoryClient } from "@/lib/api";

export default function DashboardPage() {
  const [advanced, setAdvanced] = useState(false);
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 15_000,
  });
  const audit = useQuery({
    queryKey: ["audit", "tail", 10],
    queryFn: () => getMemoryClient().auditTail(10),
    refetchInterval: 30_000,
  });
  const posture = computePosture(status.data ?? null);

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        title="Dashboard"
        description="System pulse — live /status snapshot, recent audit activity, and pipeline summary at a glance."
        crumbs={[{ label: "Dashboard" }]}
        actions={
          <div className="flex items-center gap-3">
            <PostureBadge posture={posture} size="md" />
            <Switch
              id="dash-advanced"
              checked={advanced}
              onCheckedChange={setAdvanced}
              label="advanced"
            />
          </div>
        }
      />

      {status.isError && (
        <ErrorState
          title="Backend unreachable"
          description="The /status endpoint failed. The dashboard cannot render without it."
          error={status.error}
          onRetry={() => status.refetch()}
          className="mb-6"
        />
      )}

      <StatusPanel status={status.data ?? null} className="mb-6" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity size={14} className="text-accent" /> Recent audit events
            </CardTitle>
            <Link
              href="/audit"
              className="text-xs muted hover:text-fg flex items-center gap-1"
            >
              open audit <ArrowRight size={12} />
            </Link>
          </CardHeader>
          <CardContent>
            {audit.isLoading ? (
              <ul className="space-y-2">
                {[0, 1, 2, 3, 4].map((i) => (
                  <li key={i}><SkeletonRow /></li>
                ))}
              </ul>
            ) : audit.isError ? (
              <ErrorState
                title="Audit unavailable"
                error={audit.error}
                onRetry={() => audit.refetch()}
              />
            ) : !audit.data || audit.data.entries.length === 0 ? (
              <EmptyState
                Icon={Link2}
                title="No events captured"
                description="The audit chain is empty for this window. New events appear here as actions are recorded."
              />
            ) : (
              <ul className="space-y-1 text-xs">
                {audit.data.entries.slice(-8).reverse().map((e) => (
                  <li
                    key={e.seq}
                    className="grid grid-cols-[40px_120px_1fr] gap-2 items-center"
                  >
                    <span className="muted font-mono">#{e.seq}</span>
                    <Badge variant="muted">
                      {(e.payload.action as string | undefined) ?? "—"}
                    </Badge>
                    <span className="font-mono truncate">
                      {(e.payload.entity_id as string | undefined) ?? "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pipelines · summary</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="text-sm space-y-2">
              <Pipeline label="Phase 2" name="Ingestion"   desc="walk → parse → graph + vector + canonical" />
              <Pipeline label="Phase 3" name="Compression" desc="dense encode → summarize → embed" />
              <Pipeline label="Phase 4" name="Retrieval"   desc="hybrid + ranking + context" />
              <Pipeline label="Phase 5" name="MCP"         desc="7 tools over Phase 1-4" />
              <Pipeline label="Phase 6" name="Lifecycle"   desc="decay / refresh / compaction" />
              <Pipeline label="Phase 7" name="Scale"       desc="sharding + cache + backpressure" />
              <Pipeline label="Phase 8" name="Governance"  desc="audit chain + integrity + replay" />
            </ul>
          </CardContent>
        </Card>
      </div>

      {advanced && status.data && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Raw /status payload</CardTitle>
          </CardHeader>
          <CardContent>
            {status.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : (
              <JsonView value={status.data} />
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Pipeline({ label, name, desc }: { label: string; name: string; desc: string }) {
  return (
    <li className="flex items-start gap-3">
      <Badge variant="muted">{label}</Badge>
      <div>
        <div className="text-fg">{name}</div>
        <div className="text-xs muted">{desc}</div>
      </div>
    </li>
  );
}
