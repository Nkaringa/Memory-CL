"use client";

import { useEffect, useMemo, useState } from "react";
import { Play, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonView } from "@/components/ui/json-view";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtMs, truncHash } from "@/lib/utils";
import type { McpToolResponse, ToolJsonSchema, ToolSchemaProperty } from "@/lib/types";
import { getMemoryClient } from "@/lib/api";
import { useRepos } from "@/components/RepoSelect";

interface ToolRunnerProps {
  tool: string;
  schemaName: string;
  /** Request JSON Schema from /mcp/tools — renders field hints when present. */
  schema?: ToolJsonSchema;
  /** Optional starting payload — typically the example for that tool. */
  initialPayload?: Record<string, unknown>;
}

/** Human-readable type for a schema property, unwrapping pydantic's
 *  anyOf encoding of optionals (`str | None` → "string | null"). */
function propType(prop: ToolSchemaProperty): string {
  if (typeof prop.type === "string") return prop.type;
  if (Array.isArray(prop.anyOf)) {
    return prop.anyOf
      .map((v) => (typeof v.type === "string" ? v.type : "any"))
      .join(" | ");
  }
  return "any";
}

/** Compact per-field hints — names, types, required markers, defaults,
 *  descriptions. Deliberately NOT a generated form: the textarea below
 *  stays the single source of truth for the payload. */
function SchemaHints({ schema }: { schema: ToolJsonSchema }) {
  const props = schema.properties ?? {};
  const entries = Object.entries(props);
  if (entries.length === 0) return null;
  const required = new Set(schema.required ?? []);
  return (
    <div className="rounded-md border border-border bg-bg/30 p-3">
      <div className="text-xs muted mb-2">
        fields <span className="text-accent">*</span>
        <span className="muted"> = required</span>
      </div>
      <ul className="space-y-1">
        {entries.map(([name, prop]) => (
          <li key={name} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <span className="font-mono text-fg">
              {name}
              {required.has(name) && <span className="text-accent">*</span>}
            </span>
            <span className="font-mono muted">{propType(prop)}</span>
            {prop.default !== undefined && (
              <span className="font-mono muted">
                = {JSON.stringify(prop.default)}
              </span>
            )}
            {typeof prop.description === "string" && (
              <span className="muted">— {prop.description}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function buildTemplates(repoId: string): Record<string, Record<string, unknown>> {
  return {
    get_context: { task: "auth flow", repo_id: repoId, top_k: 5 },
    get_module_summary: { module: "<module — find one via /retrieve>", repo_id: repoId },
    get_related_components: {
      component: "<qualified_name — find one via /retrieve>",
      repo_id: repoId,
      depth: 1,
    },
    get_risks: { entity: "<qualified_name — find one via /retrieve>", repo_id: repoId },
    query_graph: {
      node: "<qualified_name — find one via /retrieve>",
      repo_id: repoId,
      depth: 2,
    },
    ingest_repository: {
      path: "/path/to/repo",
      repo_id: repoId,
      commit_sha: "manual",
    },
    update_memory: {
      session_id: "session-1",
      repo_id: repoId,
      session_data: { note: "from inspector" },
    },
  };
}

export function ToolRunner({ tool, schemaName, schema, initialPayload }: ToolRunnerProps) {
  const { data: reposData } = useRepos();
  const firstRepoId = reposData?.repos[0]?.repo_id ?? "<repo-id>";

  const start = useMemo(
    () => initialPayload ?? buildTemplates(firstRepoId)[tool] ?? {},
    // firstRepoId is intentionally excluded: we only want the initial text to
    // reflect what was known at mount; users can edit the JSON manually.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tool, initialPayload],
  );
  const [text, setText] = useState(JSON.stringify(start, null, 2));

  // Once the repos load (after mount), patch the placeholder repo_id in the
  // textarea if the user hasn't already changed it.
  useEffect(() => {
    const firstRepo = reposData?.repos[0];
    if (!firstRepo) return;
    const rid = firstRepo.repo_id;
    setText((prev) => {
      try {
        const parsed = JSON.parse(prev) as Record<string, unknown>;
        if (parsed.repo_id === "<repo-id>") {
          return JSON.stringify({ ...parsed, repo_id: rid }, null, 2);
        }
      } catch {
        // not parseable — leave as-is
      }
      return prev;
    });
  }, [reposData]);
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
        {schema && <SchemaHints schema={schema} />}

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
