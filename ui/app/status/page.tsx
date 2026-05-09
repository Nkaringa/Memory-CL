"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { JsonView } from "@/components/ui/json-view";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton, SkeletonRow } from "@/components/ui/skeleton";
import { StatusPanel } from "@/components/StatusPanel";
import { getMemoryClient } from "@/lib/api";
import { fmtMs, statusVariant } from "@/lib/utils";

export default function StatusPage() {
  const [advanced, setAdvanced] = useState(false);
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 10_000,
  });
  const ready = useQuery({
    queryKey: ["health", "ready"],
    queryFn: () => getMemoryClient().health(),
    refetchInterval: 10_000,
  });

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="system"
        title="Status"
        description="Live boot stages, safe-mode controller, feature flags, and backend readiness — refreshed every 10 seconds."
        crumbs={[{ label: "System" }, { label: "Status" }]}
        actions={
          <Switch
            id="status-advanced"
            checked={advanced}
            onCheckedChange={setAdvanced}
            label="advanced"
          />
        }
      />

      {status.isError && (
        <ErrorState
          title="Could not reach /status"
          description="The status endpoint is unreachable. The four-pillar dashboard cannot render without it."
          error={status.error}
          onRetry={() => status.refetch()}
          className="mb-6"
        />
      )}

      <StatusPanel status={status.data ?? null} className="mb-6" />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity size={14} className="text-accent" /> Backend readiness
          </CardTitle>
          {ready.data && (
            <Badge variant={statusVariant(ready.data.status)}>
              {ready.data.status.toUpperCase()}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {ready.isLoading ? (
            <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {[0, 1, 2, 3].map((i) => (
                <li
                  key={i}
                  className="rounded border border-border bg-bg/30 p-3 space-y-2"
                >
                  <Skeleton className="h-3 w-24" />
                  <SkeletonRow />
                </li>
              ))}
            </ul>
          ) : ready.isError ? (
            <ErrorState
              title="/health/ready failed"
              error={ready.error}
              onRetry={() => ready.refetch()}
            />
          ) : !ready.data ? (
            <div className="text-sm muted">no data</div>
          ) : (
            <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
              {ready.data.components.map((c) => (
                <li
                  key={c.name}
                  className="rounded border border-border bg-bg/30 p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono">{c.name}</span>
                    <Badge variant={statusVariant(c.status)}>{c.status}</Badge>
                  </div>
                  <div className="text-[10px] muted mt-1">
                    latency {fmtMs(c.latency_ms)}
                  </div>
                  {c.error && (
                    <div className="text-[10px] text-bad mt-1 break-all">
                      {c.error}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {advanced && (
        <Card>
          <CardHeader>
            <CardTitle>Raw payloads</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {status.data && (
              <div>
                <div className="text-xs muted mb-1">/status</div>
                <JsonView value={status.data} />
              </div>
            )}
            {ready.data && (
              <div>
                <div className="text-xs muted mb-1">/health/ready</div>
                <JsonView value={ready.data} />
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
