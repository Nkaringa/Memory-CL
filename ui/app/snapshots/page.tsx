"use client";

import { useMutation } from "@tanstack/react-query";
import { Camera, RotateCcw } from "lucide-react";
import { useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel } from "@/components/shell/primitives";
import { truncHash } from "@/lib/utils";
import type { SnapshotResponse } from "@/lib/types";

export default function SnapshotsPage() {
  const client = getMemoryClient();
  const [tenant, setTenant] = useState("default");

  const build = useMutation<SnapshotResponse, Error, void>({
    mutationFn: () => client.buildSnapshot({ tenant_id: tenant }),
  });

  const snap = build.data;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Snapshots"
        subtitle="Deterministic capture of system state for reproducibility."
      />

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
        <Panel title={<span className="flex items-center gap-2"><Camera size={15} /> Build snapshot</span>}>
          <div className="px-4 py-4">
            <div className="mb-2 text-[12.5px] text-muted">
              Capture component hashes (graph, embeddings, retrieval config, registry) under a tenant.
            </div>
            <label className="mb-1 block text-[11.5px] text-muted2">tenant_id</label>
            <input
              value={tenant}
              onChange={(e) => setTenant(e.target.value)}
              className="mb-3 w-full rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
            />
            <button
              onClick={() => build.mutate()}
              disabled={build.isPending || !tenant.trim()}
              className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white hover:bg-accentInk disabled:opacity-50"
            >
              {build.isPending ? "Building…" : "Build snapshot"}
            </button>

            {build.isError && (
              <div className="mt-3 rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2 text-[12px] text-[#8a5a00]">
                Snapshot build failed — this is an operator/production feature and may be disabled on
                lite installs. ({build.error.message})
              </div>
            )}

            {snap && (
              <div className="mt-3 rounded-lg border border-border bg-panel px-3 py-2.5 text-[12px]">
                <div className="mb-1.5 font-mono font-semibold text-accentInk">{snap.snapshot_id}</div>
                <HashRow k="captured_at" v={snap.captured_at} />
                <HashRow k="graph_state" v={truncHash(snap.components.graph_state_hash)} />
                <HashRow k="embedding_index" v={truncHash(snap.components.embedding_index_hash)} />
                <HashRow k="retrieval_config" v={truncHash(snap.components.retrieval_config_hash)} />
                <HashRow k="mcp_registry" v={truncHash(snap.components.mcp_registry_hash)} />
              </div>
            )}
          </div>
        </Panel>

        <Panel title={<span className="flex items-center gap-2"><RotateCcw size={15} /> Replay</span>}>
          <div className="px-4 py-4">
            <div className="mb-3 text-[12.5px] text-muted">
              Verify a payload reproduces a known snapshot hash. Replay takes a snapshot_id plus the
              payload to re-derive — wired through <span className="font-mono">POST /snapshot/replay</span>.
            </div>
            <div className="rounded-lg border border-[#f3e2c0] bg-warnSoft px-3 py-2.5 text-[12px] text-[#8a5a00]">
              Build a snapshot first to get an id, then replay against it. Interactive replay UI is a
              planned operator feature.
            </div>
          </div>
        </Panel>
      </div>

      <div className="mt-3 text-[12px] text-muted">
        Operator / production feature — hidden by default on single-user lite installs.
      </div>
    </div>
  );
}

function HashRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between py-0.5 text-[12px]">
      <span className="text-muted">{k}</span>
      <span className="font-mono text-muted2">{v}</span>
    </div>
  );
}
