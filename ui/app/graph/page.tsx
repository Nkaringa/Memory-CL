"use client";

import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { GitGraph } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { RepoSelect } from "@/components/RepoSelect";
import { QnameInput } from "@/components/QnameInput";
import { ErrorState } from "@/components/ui/error-state";
import { GraphViewer } from "@/components/GraphViewer";
import { RepoGraphViewer } from "@/components/RepoGraphViewer";
import { getMemoryClient, getRepoGraph } from "@/lib/api";
import type { McpToolResponse, RepoGraphResponse } from "@/lib/types";

type GraphMode = "repo" | "seed";

// useSearchParams needs a Suspense boundary for static prerendering in the
// app router, so the page itself is a thin wrapper around the real content.
export default function GraphPage() {
  return (
    <Suspense fallback={null}>
      <GraphPageInner />
    </Suspense>
  );
}

function GraphPageInner() {
  const searchParams = useSearchParams();
  const initialNode = searchParams.get("node") ?? "";
  const initialRepo = searchParams.get("repo") ?? "";

  // Deep links with a node (e.g. from Retrieve's "graph →") land directly
  // in seed mode; otherwise the whole-repo overview is the default.
  const [mode, setMode] = useState<GraphMode>(initialNode ? "seed" : "repo");
  const [node, setNode] = useState(initialNode);
  const [repoId, setRepoId] = useState(initialRepo);
  const [depth, setDepth] = useState(2);
  const [submittedDepth, setSubmittedDepth] = useState<number | null>(null);
  const [includeExternal, setIncludeExternal] = useState(false);

  const mutation = useMutation<McpToolResponse, Error, { d: number; n?: string }>({
    mutationFn: async ({ d, n }) =>
      getMemoryClient().runTool("query_graph", {
        node: n ?? node, repo_id: repoId, depth: d,
      }),
  });

  // Whole-repo graph — auto-loads as soon as a repo is selected.
  const repoGraph = useQuery<RepoGraphResponse, Error>({
    queryKey: ["repo-graph", repoId, includeExternal],
    queryFn: () => getRepoGraph(repoId, { includeExternal }),
    enabled: mode === "repo" && repoId !== "",
    retry: 1,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setSubmittedDepth(depth);
    mutation.mutate({ d: depth });
  }

  /** "Focus BFS here" handoff from the whole-repo viewer: switch to seed
   *  mode with the clicked node prefilled and run the traversal. */
  function focusBfs(qualifiedName: string) {
    setNode(qualifiedName);
    setMode("seed");
    setSubmittedDepth(depth);
    // Pass the qname explicitly — setNode hasn't re-rendered yet, so the
    // mutationFn closure still sees the old `node` value.
    mutation.mutate({ d: depth, n: qualifiedName });
  }

  // Deep-link support: /graph?node=<qname>&repo=<repo_id> (e.g. from the
  // Retrieve page's "graph →" action) auto-runs the traversal exactly once.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    autoRan.current = true;
    if (initialNode && initialRepo) {
      setSubmittedDepth(depth);
      mutation.mutate({ d: depth });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        description="Two views over the Phase-2 graph (Neo4j): the whole-repo graph as stored, or a BFS from a seed node. EXTERNAL nodes are dimmed; edges are the stored typed relationships (older backends fall back to seed→neighbor projections in seed mode)."
        crumbs={[{ label: "Core" }, { label: "Graph" }]}
      />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Traversal</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs
            defaultValue="repo"
            value={mode}
            onValueChange={(v) => setMode(v as GraphMode)}
          >
            <TabsList>
              <TabsTrigger value="repo">Whole repo</TabsTrigger>
              <TabsTrigger value="seed">From seed</TabsTrigger>
            </TabsList>

            <TabsContent value="repo">
              <div className="grid grid-cols-1 md:grid-cols-[180px_auto_auto_1fr] gap-3 items-end">
                <div>
                  <label htmlFor="repo-graph-repo" className="text-xs muted block mb-1">repo_id</label>
                  <RepoSelect id="repo-graph-repo" value={repoId} onChange={setRepoId} />
                </div>
                <div className="flex items-center h-9">
                  <Switch
                    id="repo-graph-external"
                    checked={includeExternal}
                    onCheckedChange={setIncludeExternal}
                    label="include external"
                  />
                </div>
                <Button
                  type="button"
                  disabled={!repoId || repoGraph.isFetching}
                  onClick={() => repoGraph.refetch()}
                >
                  {repoGraph.isFetching ? "Loading…" : "Load graph"}
                </Button>
                {repoGraph.data && (
                  <div className="text-xs muted font-mono self-center md:text-right">
                    {repoGraph.data.nodes.length} nodes · {repoGraph.data.edges.length} edges
                    {repoGraph.data.truncated && (
                      <span className="text-warn"> · truncated at node cap</span>
                    )}
                  </div>
                )}
              </div>
              {repoGraph.data?.truncated && (
                <p className="mt-2 text-xs text-warn">
                  The backend hit its max_nodes cap — this view is a partial graph.
                </p>
              )}
            </TabsContent>

            <TabsContent value="seed">
              <form
                onSubmit={submit}
                className="grid grid-cols-1 md:grid-cols-[1fr_180px_120px_auto] gap-3"
              >
                <div>
                  <label htmlFor="graph-node" className="text-xs muted block mb-1">node</label>
                  <QnameInput
                    id="graph-node"
                    required
                    value={node}
                    onChange={setNode}
                    repoId={repoId}
                    placeholder="qualified_name or unit_id — type to search"
                  />
                </div>
                <div>
                  <label htmlFor="graph-repo" className="text-xs muted block mb-1">repo_id</label>
                  <RepoSelect id="graph-repo" value={repoId} onChange={setRepoId} />
                </div>
                <div>
                  <label htmlFor="graph-depth" className="text-xs muted block mb-1">depth</label>
                  <Input
                    id="graph-depth"
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
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {mode === "repo" ? (
        <>
          {repoGraph.isError && (
            <ErrorState
              title="Repo graph failed"
              description="GET /repos/{repo_id}/graph returned an error. The backend may predate commit 4f06ac6 — use seed mode instead."
              error={repoGraph.error}
              onRetry={() => repoGraph.refetch()}
              className="mb-6"
            />
          )}
          <RepoGraphViewer
            graph={repoGraph.data ?? null}
            isLoading={repoGraph.isLoading || repoGraph.isFetching}
            onFocusBfs={focusBfs}
          />
        </>
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}
