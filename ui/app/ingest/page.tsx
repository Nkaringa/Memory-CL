"use client";

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Boxes, FileCode, Sigma } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { JsonView } from "@/components/ui/json-view";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtMs } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import type { IngestResponse } from "@/lib/types";

export default function IngestPage() {
  const [advanced, setAdvanced] = useState(false);
  const [repoId, setRepoId] = useState("");
  const [repoPath, setRepoPath] = useState("/path/to/repo");
  const [commitSha, setCommitSha] = useState("manual");

  const mutation = useMutation<IngestResponse, Error, void>({
    mutationFn: () =>
      getMemoryClient().ingest({
        repo_id: repoId,
        repo_path: repoPath,
        commit_sha: commitSha,
      }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="core"
        title="Ingest"
        description="Trigger Phase-2 IngestionPipeline. Reports per-file outcomes deterministically."
        crumbs={[{ label: "Core" }, { label: "Ingest" }]}
        actions={
          <Switch
            id="ing-advanced"
            checked={advanced}
            onCheckedChange={setAdvanced}
            label="advanced"
          />
        }
      />

      <Card className="mb-6">
        <CardHeader><CardTitle>Repository</CardTitle></CardHeader>
        <CardContent>
          <form
            onSubmit={submit}
            className="grid grid-cols-1 md:grid-cols-[1fr_200px_180px_auto] gap-3"
          >
            <div>
              <label htmlFor="ingest-repo-path" className="text-xs muted block mb-1">repo_path</label>
              <Input
                id="ingest-repo-path"
                required
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder="absolute path on the API host"
              />
            </div>
            <div>
              <label htmlFor="ingest-repo-id" className="text-xs muted block mb-1">repo_id</label>
              <Input
                id="ingest-repo-id"
                required
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
                placeholder="name for this repo (e.g. my-app)"
              />
            </div>
            <div>
              <label htmlFor="ingest-commit-sha" className="text-xs muted block mb-1">commit_sha</label>
              <Input
                id="ingest-commit-sha"
                value={commitSha}
                onChange={(e) => setCommitSha(e.target.value)}
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Ingesting…" : "Ingest"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {mutation.isError && (
        <ErrorState
          title="Ingestion failed"
          description={
            mutation.error?.name === "AbortError"
              ? "The request timed out client-side — ingestion may still be running on the server. Check the Dashboard before retrying."
              : "The /ingest endpoint returned an error. Confirm the repo_path is readable from the API host."
          }
          error={mutation.error}
          onRetry={() => mutation.mutate()}
          className="mb-6"
        />
      )}

      {mutation.isPending && <IngestionSkeleton />}

      {!mutation.isPending && mutation.data && (
        <IngestionResult res={mutation.data} advanced={advanced} />
      )}

      {!mutation.isPending && !mutation.data && !mutation.isError && (
        <Card>
          <CardContent>
            <Pipeline />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function IngestionSkeleton() {
  return (
    <Card>
      <CardHeader><CardTitle>Working…</CardTitle></CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <div
              key={i}
              className="rounded border border-border bg-bg/30 p-3 space-y-2"
            >
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-4 w-10" />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function IngestionResult({ res, advanced }: { res: IngestResponse; advanced: boolean }) {
  const m = res.metrics;
  const cards: Array<{ Icon: typeof Boxes; label: string; value: string | number }> = [
    { Icon: FileCode, label: "files walked", value: m.files_walked ?? 0 },
    { Icon: FileCode, label: "files parsed", value: m.files_parsed ?? 0 },
    { Icon: Boxes, label: "units emitted", value: m.units_emitted ?? 0 },
    { Icon: Boxes, label: "units changed", value: m.units_changed ?? 0 },
    { Icon: Sigma, label: "graph nodes", value: m.nodes_written ?? 0 },
    { Icon: Sigma, label: "graph edges", value: m.edges_written ?? 0 },
    { Icon: Sigma, label: "vector payloads (placeholders)", value: m.vector_payloads_written ?? 0 },
    { Icon: Sigma, label: "duration", value: fmtMs(m.duration_ms ?? 0) },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Result</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant="muted">collection {res.units_collection}</Badge>
          <Badge variant={res.failed_files.length === 0 ? "ok" : "warn"}>
            {res.failed_files.length === 0
              ? "no failed files"
              : `${res.failed_files.length} failed`}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {cards.map(({ Icon, label, value }) => (
            <div
              key={label}
              className="rounded border border-border bg-bg/30 p-3 flex items-center gap-3"
            >
              <Icon size={16} className="text-accent" />
              <div>
                <div className="text-xs muted">{label}</div>
                <div className="font-mono">{value}</div>
              </div>
            </div>
          ))}
        </div>

        {res.failed_files.length > 0 && (
          <div className="rounded border border-bad/30 bg-bad/10 p-3 text-xs">
            <div className="muted mb-1">failed files</div>
            <ul className="font-mono">
              {res.failed_files.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        )}

        {advanced && <JsonView value={res} />}
      </CardContent>
    </Card>
  );
}

function Pipeline() {
  const stages = [
    { label: "walk", desc: "Phase-2 deterministic file walker (gitignore-aware)" },
    { label: "parse", desc: "ast.parse → IngestionUnit per symbol" },
    { label: "graph", desc: "EDGE_RULES-validated nodes + edges → Neo4j" },
    { label: "store", desc: "canonical units → Postgres; placeholder payloads (vectors pending Phase-3) → Qdrant" },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 text-xs">
      {stages.map((s) => (
        <div key={s.label} className="rounded border border-border bg-bg/30 p-3">
          <div className="font-mono text-accent mb-1">{s.label}</div>
          <div className="muted">{s.desc}</div>
        </div>
      ))}
    </div>
  );
}
