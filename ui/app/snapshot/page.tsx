"use client";

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Database } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { JsonView } from "@/components/ui/json-view";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { SnapshotDiff } from "@/components/SnapshotDiff";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getMemoryClient } from "@/lib/api";
import type { ReplayResponse, SnapshotResponse } from "@/lib/types";

export default function SnapshotPage() {
  const [tenantId, setTenantId] = useState("acme-corp");
  const [stateVersion, setStateVersion] = useState("v0");
  const [history, setHistory] = useState<SnapshotResponse[]>([]);

  const buildMut = useMutation<SnapshotResponse, Error, void>({
    mutationFn: () =>
      getMemoryClient().buildSnapshot({
        tenant_id: tenantId,
        state_version_token: stateVersion,
      }),
    onSuccess: (snap) => setHistory((prev) => [...prev, snap].slice(-2)),
  });

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="system"
        title="Snapshot"
        description="Phase-8 SystemSnapshotBuilder + ReplayEngine. Build twice to compare; replay an arbitrary payload to verify deterministic output."
        crumbs={[{ label: "System" }, { label: "Snapshot" }]}
      />

      <Card className="mb-6">
        <CardHeader><CardTitle>Build snapshot</CardTitle></CardHeader>
        <CardContent>
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              buildMut.mutate();
            }}
            className="grid grid-cols-1 md:grid-cols-[1fr_180px_auto] gap-3"
          >
            <div>
              <label className="text-xs muted block mb-1">tenant_id</label>
              <Input
                required
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs muted block mb-1">state_version_token</label>
              <Input
                value={stateVersion}
                onChange={(e) => setStateVersion(e.target.value)}
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={buildMut.isPending}>
                {buildMut.isPending ? "Building…" : "Build"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {buildMut.isError && (
        <ErrorState
          title="Snapshot build failed"
          description="The /snapshot/build endpoint returned an error. Inspect the message and confirm the tenant has live state to fingerprint."
          error={buildMut.error}
          onRetry={() => buildMut.mutate()}
          className="mb-6"
        />
      )}

      {history.length === 0 && !buildMut.isPending && !buildMut.isError && (
        <EmptyState
          Icon={Database}
          title="No snapshots built yet"
          description="Build at least one snapshot to inspect components. Build a second to compare and detect drift."
        />
      )}

      {(history.length > 0 || buildMut.isPending) && (
        <Tabs defaultValue="diff">
          <TabsList>
            <TabsTrigger value="diff">Diff</TabsTrigger>
            <TabsTrigger value="replay">Replay</TabsTrigger>
          </TabsList>

          <TabsContent value="diff">
            <SnapshotDiff
              left={history[0] ?? null}
              right={history[1] ?? null}
            />
          </TabsContent>

          <TabsContent value="replay">
            <ReplayPanel snapshotId={history.at(-1)?.snapshot_id ?? null} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function ReplayPanel({ snapshotId }: { snapshotId: string | null }) {
  const [payload, setPayload] = useState('{"a": 1, "b": [1, 2, 3]}');
  const [expected, setExpected] = useState('{"a": 1, "b": [1, 2, 3]}');
  const [response, setResponse] = useState<ReplayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function run() {
    if (!snapshotId) return;
    setError(null);
    setPending(true);
    let p: unknown;
    let e: unknown;
    try {
      p = JSON.parse(payload);
      e = expected.trim() ? JSON.parse(expected) : undefined;
    } catch (err) {
      setError(`JSON parse: ${(err as Error).message}`);
      setPending(false);
      return;
    }
    try {
      const res = await getMemoryClient().replay({
        snapshot_id: snapshotId,
        payload: p,
        expected_output: e,
      });
      setResponse(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Replay against snapshot</CardTitle>
        <Badge variant="muted">
          {snapshotId ? snapshotId.slice(0, 12) + "…" : "build a snapshot first"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="text-xs muted block mb-1">payload (JSON)</label>
            <Textarea
              rows={6}
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs muted block mb-1">
              expected_output (JSON, optional)
            </label>
            <Textarea
              rows={6}
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
            />
          </div>
        </div>
        <Button onClick={run} disabled={!snapshotId || pending}>
          {pending ? "Running replay…" : "Run replay"}
        </Button>
        {error && (
          <div className="rounded-md border border-bad/30 bg-bad/[0.06] p-2.5 text-xs text-bad font-mono">
            {error}
          </div>
        )}
        {response && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant={response.matches ? "ok" : "warn"}>
                {response.matches ? "matches" : "drift"}
              </Badge>
              <span className="text-xs muted font-mono">
                expected={response.expected_hash.slice(0, 12)}…
                actual={response.actual_hash.slice(0, 12)}…
              </span>
            </div>
            <JsonView value={response} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
