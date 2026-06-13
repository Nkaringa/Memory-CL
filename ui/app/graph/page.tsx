"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getMemoryClient, getRepoGraph, searchQnames } from "@/lib/api";
import { PageHeader, Panel } from "@/components/shell/primitives";
import { RepoSelect } from "@/components/shell/RepoSelect";
import { RepoGraphViewer } from "@/components/RepoGraphViewer";
import type { ExploreDirection } from "@/lib/types";

type Tab = "map" | "trace" | "risks";

interface Neighbor {
  node_id: string;
  qualified_name: string;
  kind: string;
  file_path: string | null;
  signature: string | null;
  relation?: string;
  distance?: number;
}

interface ExploreData {
  found?: boolean;
  seed?: { qualified_name: string; kind: string };
  neighbors?: Neighbor[];
  suggestions?: { qualified_name: string; kind: string }[];
  hint?: string;
}

interface RiskData {
  found?: boolean;
  risks?: { node_id: string; qualified_name: string; kind: string }[];
  risk_count?: number;
  suggestions?: { qualified_name: string }[];
  hint?: string;
}

const DIRECTIONS: ExploreDirection[] = [
  "callers",
  "callees",
  "imports",
  "imported_by",
  "inherits",
  "all",
];

export default function GraphPage() {
  const client = getMemoryClient();
  const [tab, setTab] = useState<Tab>("map");
  const [repo, setRepo] = useState("");

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Graph"
        subtitle="Map the whole repo, trace one symbol's neighbors, or surface external risks."
        actions={<RepoSelect value={repo} onChange={setRepo} className="w-[220px]" />}
      />

      <div className="mb-4 flex gap-1 border-b border-border">
        <TabBtn on={tab === "map"} onClick={() => setTab("map")}>Map (whole repo)</TabBtn>
        <TabBtn on={tab === "trace"} onClick={() => setTab("trace")}>Trace</TabBtn>
        <TabBtn on={tab === "risks"} onClick={() => setTab("risks")}>Risks</TabBtn>
      </div>

      {tab === "map" && <MapTab repo={repo} key={`map-${repo}`} client={client} />}
      {tab === "trace" && <TraceTab repo={repo} client={client} />}
      {tab === "risks" && <RisksTab repo={repo} client={client} />}
    </div>
  );
}

type Client = ReturnType<typeof getMemoryClient>;

function MapTab({ repo }: { repo: string; client: Client }) {
  const graph = useQuery({
    queryKey: ["repo-graph", repo],
    queryFn: () => getRepoGraph(repo, { includeExternal: false }),
    enabled: repo !== "",
    retry: 1,
  });

  if (repo === "") {
    return <Empty>Select a repo to load its graph.</Empty>;
  }

  return (
    <>
      <div className="mb-2.5 text-[12.5px] text-muted">
        {graph.data
          ? `${repo} · ${graph.data.nodes.length} nodes · ${graph.data.edges.length} edges · externals hidden`
          : "loading…"}
      </div>
      {graph.isError ? (
        <Empty>
          GET /repos/{repo}/graph failed. The backend may predate the whole-repo graph endpoint.
        </Empty>
      ) : (
        <RepoGraphViewer
          graph={graph.data ?? null}
          isLoading={graph.isLoading || graph.isFetching}
          onFocusBfs={() => {}}
        />
      )}
    </>
  );
}

function TraceTab({ repo, client }: { repo: string; client: Client }) {
  const [qname, setQname] = useState("");
  const [direction, setDirection] = useState<ExploreDirection>("callers");

  // qname autocomplete via searchQnames.
  const suggest = useQuery({
    queryKey: ["qnames", repo, qname],
    queryFn: () => searchQnames(repo, qname),
    enabled: repo !== "" && qname.trim().length >= 2,
  });

  const explore = useMutation({
    mutationFn: async () => {
      const resp = await client.runTool("explore", {
        qualified_name: qname,
        repo_id: repo,
        direction,
        depth: 2,
      });
      return resp.data as unknown as ExploreData;
    },
  });

  const data = explore.data;
  const neighbors = data?.neighbors ?? [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-[280px] flex-1">
          <input
            value={qname}
            onChange={(e) => setQname(e.target.value)}
            placeholder="app.cli.tailor.run_pipeline"
            className="w-full rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
          />
          {suggest.data && suggest.data.matches.length > 0 && qname.trim().length >= 2 && (
            <div className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-border bg-bg shadow-lg">
              {suggest.data.matches.slice(0, 12).map((m) => (
                <button
                  key={m.qualified_name}
                  onClick={() => setQname(m.qualified_name)}
                  className="block w-full truncate px-3 py-1.5 text-left font-mono text-[12.5px] hover:bg-panel"
                >
                  {m.qualified_name} <span className="text-muted">· {m.kind}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value as ExploreDirection)}
          className="h-9 appearance-none rounded-lg border border-border2 bg-bg px-3 font-mono text-[13px] outline-none focus:border-accent"
        >
          {DIRECTIONS.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <button
          onClick={() => explore.mutate()}
          disabled={explore.isPending || !qname.trim() || repo === ""}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white hover:bg-accentInk disabled:opacity-50"
        >
          {explore.isPending ? "Tracing…" : "Trace"}
        </button>
      </div>

      {!explore.data && !explore.isPending ? (
        <Empty>Enter a symbol and direction, then Trace its graph neighbors.</Empty>
      ) : explore.isPending ? (
        <Empty>Walking the graph…</Empty>
      ) : !data?.found ? (
        <div className="rounded-xl border border-border bg-bg px-5 py-6 text-[13px] text-muted">
          {data?.hint ?? "Symbol not found."}
          {data?.suggestions && data.suggestions.length > 0 && (
            <div className="mt-2 space-y-1">
              {data.suggestions.map((s) => (
                <button
                  key={s.qualified_name}
                  onClick={() => setQname(s.qualified_name)}
                  className="block font-mono text-[12px] text-accentInk hover:underline"
                >
                  {s.qualified_name}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : neighbors.length === 0 ? (
        <Empty>{data?.hint ?? `No ${direction} found.`}</Empty>
      ) : (
        <Panel bodyClass="p-0" title={<span>{data.seed?.qualified_name} <span className="font-normal text-muted">· {direction} · {neighbors.length}</span></span>}>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                <th className="px-3 py-2 text-left font-semibold">relation</th>
                <th className="px-3 py-2 text-left font-semibold">kind</th>
                <th className="px-3 py-2 text-left font-semibold">qualified name</th>
                <th className="px-3 py-2 text-left font-semibold">signature</th>
              </tr>
            </thead>
            <tbody>
              {neighbors.map((n) => (
                <tr key={n.node_id} className="border-t border-border">
                  <td className="px-3 py-2">
                    <span className="rounded-[4px] bg-accent px-1.5 py-px font-mono text-[10.5px] text-white">
                      {n.relation ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted">{n.kind}</td>
                  <td className="px-3 py-2 font-mono">{n.qualified_name}</td>
                  <td className="px-3 py-2 font-mono text-muted">{n.signature ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

function RisksTab({ repo, client }: { repo: string; client: Client }) {
  const [entity, setEntity] = useState("");

  const risks = useMutation({
    mutationFn: async () => {
      const resp = await client.runTool("get_risks", { entity, repo_id: repo });
      return resp.data as unknown as RiskData;
    },
  });

  const data = risks.data;
  const list = data?.risks ?? [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <input
          value={entity}
          onChange={(e) => setEntity(e.target.value)}
          placeholder="core.embeddings.openai_embedder.OpenAIEmbedder"
          className="min-w-[280px] flex-1 rounded-lg border border-border2 bg-bg px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
        />
        <button
          onClick={() => risks.mutate()}
          disabled={risks.isPending || !entity.trim() || repo === ""}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white hover:bg-accentInk disabled:opacity-50"
        >
          {risks.isPending ? "Checking…" : "Find risks"}
        </button>
      </div>

      <div className="mb-3 text-[12.5px] text-muted">
        External dependencies (third-party imports/calls) a symbol touches directly — the structural
        blast radius.
      </div>

      {!risks.data && !risks.isPending ? (
        <Empty>Enter a symbol to list its external dependencies.</Empty>
      ) : risks.isPending ? (
        <Empty>Projecting risks…</Empty>
      ) : !data?.found ? (
        <Empty>{data?.hint ?? "Entity not found."}</Empty>
      ) : list.length === 0 ? (
        <Empty>No external dependencies — this symbol touches only internal nodes.</Empty>
      ) : (
        <Panel bodyClass="p-0" title={<span>External dependencies <span className="font-normal text-muted">· {list.length}</span></span>}>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                <th className="px-3 py-2 text-left font-semibold">external dependency</th>
                <th className="px-3 py-2 text-left font-semibold">kind</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.node_id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono">{r.qualified_name}</td>
                  <td className="px-3 py-2 text-muted">{r.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

function TabBtn({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px border-b-2 px-3.5 py-2 text-[13.5px] font-medium transition-colors ${
        on ? "border-accent text-accentInk" : "border-transparent text-muted2 hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-bg px-5 py-12 text-center text-[13px] text-muted">
      {children}
    </div>
  );
}
