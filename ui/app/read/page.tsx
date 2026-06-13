"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type ReactNode } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel } from "@/components/shell/primitives";
import { RepoSelect } from "@/components/shell/RepoSelect";

interface SymbolMatch {
  repo_id?: string;
  qualified_name: string;
  kind: string;
  file_path: string | null;
  lines?: string;
  unit_id?: string;
}

interface UnitData {
  found?: boolean;
  qualified_name?: string;
  kind?: string;
  file_path?: string | null;
  lines?: string | null;
  language?: string;
  signature?: string | null;
  content?: string;
  truncated?: boolean;
  suggestions?: SymbolMatch[];
  hint?: string;
}

export default function ReadPage() {
  const client = getMemoryClient();
  const [repo, setRepo] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string>("");

  // Symbol finder — find_symbol substring search, gated on a non-empty query.
  const find = useQuery({
    queryKey: ["find-symbol", repo, query],
    queryFn: async () => {
      const resp = await client.runTool("find_symbol", {
        query,
        repo_id: repo,
        limit: 40,
      });
      const d = resp.data as Record<string, unknown>;
      return (d.matches as SymbolMatch[]) ?? [];
    },
    enabled: repo !== "" && query.trim().length >= 2,
  });

  // Unit reader — runs when a symbol is picked.
  const read = useMutation({
    mutationFn: async (reference: string) => {
      const resp = await client.runTool("read_unit", { reference, repo_id: repo });
      return { data: resp.data as unknown as UnitData, latency: resp.latency_ms };
    },
  });

  const pick = (qn: string) => {
    setSelected(qn);
    read.mutate(qn);
  };

  const matches = find.data ?? [];
  const unit = read.data?.data;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Read"
        subtitle="Browse symbols and read full source straight from the canonical store."
      />

      {/* controls */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <RepoSelect value={repo} onChange={setRepo} className="w-[220px]" />
        <div className="flex min-w-[260px] flex-1 items-center gap-2.5 rounded-lg border border-border2 bg-bg px-3 py-2 focus-within:border-accent">
          <Search size={15} className="flex-none text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="find a symbol by name (e.g. HybridRetriever)…"
            className="flex-1 bg-transparent font-mono text-[13px] text-fg outline-none placeholder:text-muted"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-[300px_1fr]">
        {/* symbol list */}
        <Panel title={<span>Symbols {matches.length > 0 ? <span className="font-normal text-muted">· {matches.length}</span> : null}</span>} bodyClass="p-1.5">
          {repo === "" ? (
            <Side>Select a repo to begin.</Side>
          ) : query.trim().length < 2 ? (
            <Side>Type at least 2 characters to search symbols.</Side>
          ) : find.isLoading ? (
            <Side>Searching…</Side>
          ) : matches.length === 0 ? (
            <Side>No symbols match that substring.</Side>
          ) : (
            <div className="max-h-[520px] space-y-px overflow-auto">
              {matches.map((m) => (
                <button
                  key={`${m.qualified_name}:${m.unit_id ?? ""}`}
                  onClick={() => pick(m.qualified_name)}
                  className={`block w-full rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-panel ${
                    selected === m.qualified_name ? "bg-accentSoft" : ""
                  }`}
                >
                  <div className={`truncate font-mono text-[12.5px] ${selected === m.qualified_name ? "font-semibold text-accentInk" : ""}`}>
                    {m.qualified_name}
                  </div>
                  <div className="truncate text-[11px] text-muted">
                    {m.kind} · {m.file_path ?? "—"}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        {/* source viewer */}
        <Panel
          title={
            unit?.found ? (
              <span className="flex min-w-0 items-center gap-2">
                <span className="truncate font-mono">{unit.qualified_name}</span>
              </span>
            ) : (
              "Source"
            )
          }
          bodyClass="p-0"
        >
          {!read.data && !read.isPending ? (
            <Side className="p-8">Pick a symbol on the left to read its full source.</Side>
          ) : read.isPending ? (
            <Side className="p-8">Loading unit…</Side>
          ) : !unit?.found ? (
            <div className="p-5 text-[13px] text-muted">
              {unit?.hint ?? "Unit not found."}
              {unit?.suggestions && unit.suggestions.length > 0 && (
                <div className="mt-3 space-y-1">
                  {unit.suggestions.map((s) => (
                    <button
                      key={s.qualified_name}
                      onClick={() => pick(s.qualified_name)}
                      className="block font-mono text-[12px] text-accentInk hover:underline"
                    >
                      {s.qualified_name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2.5 font-mono text-[12px] text-muted">
                <span>
                  {unit.file_path ?? "—"}
                  {unit.lines ? `:${unit.lines}` : ""}
                </span>
                {unit.kind && <span className="rounded-[5px] border border-border bg-panel px-1.5 py-px text-muted2">{unit.kind}</span>}
                {unit.language && <span>{unit.language}</span>}
                {read.data && <span className="ml-auto">{read.data.latency.toFixed(0)}ms</span>}
              </div>
              {unit.signature && (
                <div className="border-b border-border bg-panel px-4 py-2 font-mono text-[12.5px] text-accentInk">
                  {unit.signature}
                </div>
              )}
              <pre className="max-h-[560px] overflow-auto whitespace-pre px-4 py-3 font-mono text-[12.5px] leading-relaxed text-fg">
                {unit.content ?? ""}
                {unit.truncated ? "\n\n… (truncated to token budget — read_unit on a child symbol for the rest)" : ""}
              </pre>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Side({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`px-3 py-6 text-center text-[12.5px] text-muted ${className ?? ""}`}>{children}</div>;
}
