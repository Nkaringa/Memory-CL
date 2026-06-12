"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Workflow } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ToolRunner } from "@/components/ToolRunner";
import { getMemoryClient } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function McpPage() {
  const tools = useQuery({
    queryKey: ["mcp", "tools"],
    queryFn: () => getMemoryClient().listTools(),
  });
  const [selected, setSelected] = useState<string | null>(null);

  const list = tools.data?.tools ?? [];
  const current = selected ?? list[0]?.name ?? null;
  const currentEntry = list.find((t) => t.name === current);

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="dev tools"
        title="MCP tools"
        description="Phase-5 agent surface — every tool is a thin orchestrator over Phase 2-4. The registry below is the single source of truth for what an agent can call."
        crumbs={[{ label: "Dev Tools" }, { label: "MCP" }]}
      />

      {tools.isError && (
        <ErrorState
          title="Could not load tool registry"
          description="The /mcp/tools endpoint failed."
          error={tools.error}
          onRetry={() => tools.refetch()}
          className="mb-6"
        />
      )}

      {!tools.isError && (
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Registry</CardTitle>
              <Badge variant="muted">{list.length}</Badge>
            </CardHeader>
            <CardContent>
              {tools.isLoading ? (
                <ul className="space-y-2">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <li
                      key={i}
                      className="rounded px-3 py-2 space-y-1.5 border border-border bg-bg/20"
                    >
                      <Skeleton className="h-3 w-32" />
                      <Skeleton className="h-2 w-20" />
                    </li>
                  ))}
                </ul>
              ) : list.length === 0 ? (
                <EmptyState
                  Icon={Workflow}
                  title="Empty registry"
                  description="The backend reports zero registered tools."
                />
              ) : (
                <ul className="space-y-1">
                  {list.map((t) => (
                    <li key={t.name}>
                      <button
                        type="button"
                        onClick={() => setSelected(t.name)}
                        className={cn(
                          "w-full text-left rounded px-3 py-2 text-sm font-mono transition-colors",
                          current === t.name
                            ? "bg-bg text-fg"
                            : "muted hover:text-fg hover:bg-bg/40",
                        )}
                      >
                        <div>{t.name}</div>
                        <div className="text-[10px] muted">{t.request_schema}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {currentEntry ? (
            <ToolRunner
              tool={currentEntry.name}
              schemaName={currentEntry.request_schema}
              schema={currentEntry.schema}
            />
          ) : !tools.isLoading ? (
            <EmptyState
              Icon={Workflow}
              title="Select a tool"
              description="Pick a tool on the left to inspect its request schema and run it interactively."
            />
          ) : (
            <Card>
              <CardHeader><CardTitle>Loading…</CardTitle></CardHeader>
              <CardContent className="space-y-2">
                <Skeleton className="h-3 w-1/2" />
                <Skeleton className="h-3 w-2/3" />
                <Skeleton className="h-32 w-full" />
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
