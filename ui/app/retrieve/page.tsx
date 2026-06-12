"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton, SkeletonCard } from "@/components/ui/skeleton";
import { QueryBox, type QueryBoxValue } from "@/components/QueryBox";
import { ResultViewer } from "@/components/ResultViewer";
import { FirstRunCard } from "@/components/FirstRunCard";
import { getMemoryClient } from "@/lib/api";
import type { RetrieveResponse } from "@/lib/types";

export default function RetrievePage() {
  const [lastQuery, setLastQuery] = useState<QueryBoxValue | null>(null);
  const { data: statusData } = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    staleTime: 60_000,
  });
  const mutation = useMutation<RetrieveResponse, Error, QueryBoxValue>({
    mutationFn: async (q) => {
      return getMemoryClient().retrieve({
        text: q.text,
        repo_id: q.repo_id,
        top_k: q.top_k,
        unit_kinds: q.unitKinds.length ? q.unitKinds : undefined,
        seed_unit_ids: q.seedUnitIds.length ? q.seedUnitIds : undefined,
      });
    },
    onSuccess: (_data, vars) => setLastQuery(vars),
    onMutate: (vars) => setLastQuery(vars),
  });

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="core"
        title="Retrieve"
        description="Hybrid retrieval over graph + keyword metadata + optional semantic vectors + Phase-4 ranking. Every result carries its breakdown."
        crumbs={[{ label: "Core" }, { label: "Retrieve" }]}
      />

      <FirstRunCard />

      {statusData && statusData.embeddings_enabled === false && (
        <div className="mb-6 rounded-md border border-warn/40 bg-warn/[0.06] px-4 py-3 text-sm text-warn/90 leading-relaxed">
          <strong className="font-semibold">Note:</strong> Semantic vectors are not enabled yet
          (Phase-3 pending) — the vector channel returns 0 hits by design. Graph and keyword
          channels are live.
        </div>
      )}

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Query</CardTitle>
        </CardHeader>
        <CardContent>
          <QueryBox onSubmit={mutation.mutate} pending={mutation.isPending} />
        </CardContent>
      </Card>

      {mutation.isError && (
        <ErrorState
          title="Retrieval failed"
          description="The /retrieve endpoint returned an error. Confirm the repo_id is ingested and the backend is reachable."
          error={mutation.error}
          onRetry={() => lastQuery && mutation.mutate(lastQuery)}
          className="mb-6"
        />
      )}

      {mutation.isPending && <ResultsSkeleton />}

      {!mutation.isPending && mutation.data && <ResultViewer result={mutation.data} />}

      {!mutation.isPending && !mutation.data && !mutation.isError && (
        <EmptyState
          Icon={Sparkles}
          title="No retrieval issued yet"
          description="Submit a query above. Every result surfaces request_id, latency, channel hits, the pipeline trace, and a per-entry 'Why this result?' panel that reconstructs the ranking formula."
        />
      )}

      {lastQuery && mutation.data && (
        <div className="mt-4 text-[10px] muted font-mono">
          last query · top_k={lastQuery.top_k}
        </div>
      )}
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded border border-border bg-bg/30 p-3 space-y-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-5 w-12" />
          </div>
        ))}
      </div>
      <SkeletonCard />
      <SkeletonCard />
    </div>
  );
}
