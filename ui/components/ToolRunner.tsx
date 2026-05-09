"use client";

import { useMemo, useState } from "react";
import { Play, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonView } from "@/components/ui/json-view";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtMs, truncHash } from "@/lib/utils";
import type { McpToolResponse } from "@/lib/types";
import { getMemoryClient } from "@/lib/api";

interface ToolRunnerProps {
  tool: string;
  schemaName: string;
  /** Optional starting payload — typically the example for that tool. */
  initialPayload?: Record<string, unknown>;
}

const DEFAULT_TEMPLATES: Record<string, Record<string, unknown>> = {
  get_context: { task: "auth flow", repo_id: "acme", top_k: 5 },
  get_module_summary: { module: "pkg.utils", repo_id: "acme" },
  get_related_components: { component: "pkg.utils.add", repo_id: "acme", depth: 1 },
  get_risks: { entity: "pkg.utils.add", repo_id: "acme" },
  query_graph: { node: "pkg.utils.add", repo_id: "acme", depth: 2 },
  ingest_repository: {
    path: "/path/to/repo",
    repo_id: "acme",
    commit_sha: "manual",
  },
  update_memory: {
    session_id: "session-1",
    repo_id: "acme",
    session_data: { note: "from inspector" },
  },
};

export function ToolRunner({ tool, schemaName, initialPayload }: ToolRunnerProps) {
  const start = useMemo(
    () => initialPayload ?? DEFAULT_TEMPLATES[tool] ?? {},
    [tool, initialPayload],
  );
  const [text, setText] = useState(JSON.stringify(start, null, 2));
  const [pending, setPending] = useState(false);
  const [response, setResponse] = useState<McpToolResponse | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  async function run() {
    setRunError(null);
    setParseError(null);
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(text);
    } catch (err) {
      setParseError((err as Error).message);
      return;
    }
    setPending(true);
    try {
      const res = await getMemoryClient().runTool(tool, payload);
      setResponse(res);
    } catch (err) {
      setRunError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="font-mono">{tool}</span>
          <Badge variant="muted">{schemaName}</Badge>
        </CardTitle>
        <Button size="sm" onClick={run} disabled={pending}>
          <Play size={14} />
          {pending ? "Running…" : "Run"}
        </Button>
      </CardHeader>

      <CardContent className="space-y-3">
        <div>
          <div className="text-xs muted mb-1">request payload (JSON)</div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            className="w-full h-48 rounded-md border border-border bg-bg/40 p-3 font-mono text-xs focus-visible:outline-none focus-visible:border-accent"
          />
          {parseError && (
            <div className="mt-1 flex items-center gap-1 text-xs text-bad">
              <AlertTriangle size={12} /> JSON parse: {parseError}
            </div>
          )}
        </div>

        {runError && (
          <div className="rounded border border-bad/40 bg-bad/10 p-3 text-xs text-bad">
            {runError}
          </div>
        )}

        {response && (
          <Tabs defaultValue="data">
            <TabsList>
              <TabsTrigger value="data">Data</TabsTrigger>
              <TabsTrigger value="raw">Raw</TabsTrigger>
            </TabsList>
            <TabsContent value="data">
              <JsonView value={response.data} />
            </TabsContent>
            <TabsContent value="raw">
              <JsonView value={response} />
            </TabsContent>
          </Tabs>
        )}
      </CardContent>

      {response && (
        <CardFooter>
          <span className="font-mono">request_id {truncHash(response.request_id, 8)}</span>
          <span className="flex items-center gap-2">
            <Badge variant={response.status === "success" ? "ok" : "bad"}>
              {response.status}
            </Badge>
            <span className="font-mono">{fmtMs(response.latency_ms)}</span>
          </span>
        </CardFooter>
      )}
    </Card>
  );
}
