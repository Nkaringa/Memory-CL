"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Terminal } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SkeletonRow } from "@/components/ui/skeleton";
import { ToolRunner } from "@/components/ToolRunner";
import { getMemoryClient } from "@/lib/api";

/** Focused tool-runner surface — for engineers who already know which
 *  tool they want to invoke. The /mcp page is the explorer; this is
 *  the ad-hoc REPL.
 */
export default function ToolRunnerPage() {
  const tools = useQuery({
    queryKey: ["mcp", "tools"],
    queryFn: () => getMemoryClient().listTools(),
  });
  const list = useMemo(() => tools.data?.tools ?? [], [tools.data]);
  const [selected, setSelected] = useState<string>("");
  const current = list.find((t) => t.name === (selected || list[0]?.name));

  return (
    <div className="max-w-5xl mx-auto">
      <PageHeader
        eyebrow="dev tools"
        title="Tool Runner"
        description="Ad-hoc MCP tool invocation. Pick a tool, edit the JSON payload, hit Run. Identical inputs always produce identical responses."
        crumbs={[{ label: "Dev Tools" }, { label: "Tool Runner" }]}
        actions={
          list.length > 0 && (
            <select
              value={selected || list[0]?.name}
              onChange={(e) => setSelected(e.target.value)}
              className="h-9 rounded-md border border-border bg-panel px-3 text-sm font-mono focus-visible:outline-none focus-visible:border-accent"
            >
              {list.map((t) => (
                <option key={t.name} value={t.name}>{t.name}</option>
              ))}
            </select>
          )
        }
      />

      {tools.isLoading && (
        <Card>
          <CardHeader><CardTitle>Loading tools…</CardTitle></CardHeader>
          <CardContent>
            <SkeletonRow />
            <SkeletonRow />
          </CardContent>
        </Card>
      )}

      {tools.isError && (
        <ErrorState
          title="Could not load the MCP registry"
          description="The /mcp/tools endpoint failed. Confirm the backend is reachable."
          error={tools.error}
          onRetry={() => tools.refetch()}
        />
      )}

      {!tools.isLoading && !tools.isError && list.length === 0 && (
        <EmptyState
          Icon={Terminal}
          title="No MCP tools registered"
          description="The backend reports an empty tool registry. Configure tools and restart the API."
        />
      )}

      {current && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center gap-2 text-xs muted">
            <Badge variant="muted">{current.request_schema}</Badge>
            <span>schema · canonical-JSON output · request_id pinned per call</span>
          </div>
          <ToolRunner tool={current.name} schemaName={current.request_schema} />
        </div>
      )}
    </div>
  );
}
