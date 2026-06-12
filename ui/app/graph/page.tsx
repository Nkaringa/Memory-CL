"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { GitGraph } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { RepoSelect } from "@/components/RepoSelect";
import { ErrorState } from "@/components/ui/error-state";
import { GraphViewer } from "@/components/GraphViewer";
import { getMemoryClient } from "@/lib/api";
import type { McpToolResponse } from "@/lib/types";

export default function GraphPage() {
  const [node, setNode] = useState("");
  const [repoId, setRepoId] = useState("");
  const [depth, setDepth] = useState(2);
  const [submittedDepth, setSubmittedDepth] = useState<number | null>(null);

  const mutation = useMutation<McpToolResponse, Error, { d: number }>({
    mutationFn: async ({ d }) =>
      getMemoryClient().runTool("query_graph", {
        node, repo_id: repoId, depth: d,
      }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setSubmittedDepth(depth);
    mutation.mutate({ d: depth });
  }

  // When the user drags the depth slider in the GraphViewer, automatically
  // re-issue the same query at the new depth — but only if they've already
  // run one query (no surprise network calls before "Traverse" is clicked).
  // Debounced 300 ms so rapid slider drags don't flood the API.
  useEffect(() => {
    if (submittedDepth === null) return;
    if (submittedDepth === depth) return;
    const timer = setTimeout(() => {
      setSubmittedDepth(depth);
      mutation.mutate({ d: depth });
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depth]);

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="core"
        title="Graph"
        description="BFS over the Phase-2 graph (Neo4j). EXTERNAL nodes are dimmed; edges are the stored typed relationships (older backends fall back to seed→neighbor projections)."
        crumbs={[{ label: "Core" }, { label: "Graph" }]}
      />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Traversal</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={submit}
            className="grid grid-cols-1 md:grid-cols-[1fr_180px_120px_auto] gap-3"
          >
            <div>
              <label className="text-xs muted block mb-1">node</label>
              <Input
                required
                value={node}
                onChange={(e) => setNode(e.target.value)}
                placeholder="qualified_name or unit_id"
              />
            </div>
            <div>
              <label className="text-xs muted block mb-1">repo_id</label>
              <RepoSelect value={repoId} onChange={setRepoId} />
            </div>
            <div>
              <label className="text-xs muted block mb-1">depth</label>
              <Input
                type="number"
                min={1}
                max={5}
                value={depth}
                onChange={(e) => setDepth(parseInt(e.target.value || "1", 10))}
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={mutation.isPending || !node.trim() || !repoId}>
                {mutation.isPending ? "Running…" : "Traverse"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {mutation.isError && (
        <ErrorState
          title="Traversal failed"
          description="The query_graph MCP tool returned an error. Confirm the seed node exists for this repo."
          error={mutation.error}
          onRetry={() => mutation.mutate({ d: depth })}
          className="mb-6"
        />
      )}

      <GraphViewer
        response={mutation.data ?? null}
        depth={depth}
        onDepthChange={setDepth}
      />

      {!mutation.data && !mutation.isPending && !mutation.isError && (
        <div className="mt-6 text-xs muted flex items-center gap-2 justify-center">
          <GitGraph size={12} /> submit a node above to populate the graph.
        </div>
      )}
    </div>
  );
}
