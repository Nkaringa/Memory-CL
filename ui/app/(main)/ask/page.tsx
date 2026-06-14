"use client";

import { useMutation } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState, type ReactNode } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader } from "@/components/shell/primitives";
import { useReposList } from "@/components/shell/RepoSelect";
import { cn } from "@/lib/utils";

interface SearchHit {
  repo_id: string;
  qualified_name: string;
  kind: string | null;
  file_path: string | null;
  lines: string | null;
  score: number;
  channels: string[];
  snippet: string | null;
  snippet_truncated?: boolean;
}

interface SearchData {
  results: SearchHit[];
  total_matches?: number;
  truncated?: boolean;
  hint?: string;
  failed_repos?: string[];
}

export default function AskPage() {
  const client = getMemoryClient();
  const { data: reposData } = useReposList();
  const repos = reposData?.repos ?? [];

  // "" means all-repos (repo_id omitted from the call).
  const [repo, setRepo] = useState<string>("");
  const [question, setQuestion] = useState("");

  const search = useMutation({
    mutationFn: async (q: string) => {
      const params: Record<string, unknown> = { question: q, top_k: 8 };
      if (repo) params.repo_id = repo;
      const resp = await client.runTool("search_code", params);
      return { data: resp.data as unknown as SearchData, latency: resp.latency_ms };
    },
  });

  const submit = () => {
    const q = question.trim();
    if (q) search.mutate(q);
  };

  const data = search.data?.data;
  const results = data?.results ?? [];
  const total = data?.total_matches ?? results.length;

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Ask your code"
        subtitle="Hybrid retrieval — vector + graph + keyword — over your ingested repos."
      />

      {/* repo chips */}
      <div className="mb-3.5 flex flex-wrap gap-2">
        {repos.map((r) => (
          <Chip key={r.repo_id} on={repo === r.repo_id} onClick={() => setRepo(r.repo_id)}>
            {r.repo_id}
          </Chip>
        ))}
        <Chip on={repo === ""} onClick={() => setRepo("")}>
          all repos
        </Chip>
      </div>

      {/* search bar */}
      <div className="flex items-center gap-3 rounded-xl border-[1.5px] border-border2 bg-bg px-4 py-3 focus-within:border-accent">
        <Search size={17} className="flex-none text-muted" />
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="how does the camera follow the player…"
          className="flex-1 bg-transparent text-[15px] text-fg outline-none placeholder:text-muted"
        />
        <button
          onClick={submit}
          disabled={search.isPending || !question.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-accentInk disabled:opacity-50"
        >
          {search.isPending ? "Searching…" : "Search"}
        </button>
      </div>

      {/* status line */}
      {search.data && (
        <div className="mx-0.5 mb-4 mt-3 text-[12.5px] text-muted">
          Hybrid · <b className="font-semibold text-accentInk">vector + graph + keyword</b> ·{" "}
          {total} matches
          {results.length !== total ? ` (${results.length} shown)` : ""} ·{" "}
          <span className="font-mono">{search.data.latency.toFixed(0)}ms</span>
          {data?.truncated ? " · truncated to token budget" : ""}
          <span> · logged to activity</span>
        </div>
      )}

      {/* results */}
      <div className="mt-4">
        {search.isPending ? (
          <Empty>Running hybrid retrieval…</Empty>
        ) : search.isError ? (
          <div className="rounded-xl border border-[#f0c9c9] bg-[#fef2f2] px-5 py-6 text-[13px] text-bad">
            Search failed: {String((search.error as Error)?.message ?? "unknown error")}
          </div>
        ) : !search.data ? (
          <Empty>Ask a question above to search your code.</Empty>
        ) : results.length === 0 ? (
          <Empty>{data?.hint ?? "No results. Try rephrasing the question or a different repo."}</Empty>
        ) : (
          <div className="space-y-2.5">
            {results.map((r, i) => (
              <ResultCard key={`${r.repo_id}:${r.qualified_name}:${i}`} hit={r} showRepo={!repo} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-bg px-5 py-12 text-center text-[13px] text-muted">
      {children}
    </div>
  );
}

function Chip({
  children,
  on,
  onClick,
}: {
  children: ReactNode;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-[12.5px] transition-colors",
        on
          ? "border-fg bg-fg text-white"
          : "border-border2 bg-bg text-muted2 hover:border-muted hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

function ResultCard({ hit, showRepo }: { hit: SearchHit; showRepo: boolean }) {
  const pct = Math.max(4, Math.min(100, Math.round((hit.score || 0) * 100)));
  const loc = hit.file_path
    ? `${hit.file_path}${hit.lines ? `:${hit.lines.split("-")[0]}` : ""}`
    : "—";
  return (
    <div className="rounded-[10px] border border-border bg-bg px-4 py-3.5 hover:border-border2">
      <div className="mb-2 flex flex-wrap items-center gap-2.5">
        <span className="h-[5px] w-11 flex-none overflow-hidden rounded-[3px] bg-panel2">
          <i className="block h-full rounded-[3px] bg-accent" style={{ width: `${pct}%` }} />
        </span>
        <span className="font-mono text-[13px] font-semibold">{hit.qualified_name}</span>
        {hit.kind && (
          <span className="rounded-[5px] border border-border bg-panel px-1.5 py-px text-[11px] text-muted2">
            {hit.kind}
          </span>
        )}
        {showRepo && (
          <span className="rounded-[5px] border border-border bg-panel px-1.5 py-px text-[11px] text-muted2">
            {hit.repo_id}
          </span>
        )}
        {(hit.channels ?? []).map((c) => (
          <span key={c} className="rounded-[4px] bg-accent px-1.5 py-px text-[10.5px] text-white">
            {c}
          </span>
        ))}
        <span className="ml-auto font-mono text-[12px] text-muted">{loc}</span>
      </div>
      {hit.snippet && (
        <pre className="overflow-x-auto whitespace-pre rounded-[7px] border border-border bg-panel px-3 py-2 font-mono text-[12.5px] leading-relaxed text-muted2">
          {hit.snippet}
          {hit.snippet_truncated ? "\n…" : ""}
        </pre>
      )}
    </div>
  );
}
