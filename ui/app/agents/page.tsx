"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useMemo, useState } from "react";
import { getMemoryClient } from "@/lib/api";
import { PageHeader, Panel } from "@/components/shell/primitives";
import { copyToClipboard } from "@/lib/utils";
import type { AuditEntryView } from "@/lib/types";

type Tab = "connect" | "tools" | "connected";

const CONNECT_CMD = `claude mcp add --transport sse --scope user \\
  memory-cl http://192.168.200.188:8000/mcp/sse \\
  --header "X-API-Key: ••••••••"`;

/** Short human descriptions for the 14 tools, keyed by registry name. The
 *  registry is the source of truth for WHICH tools exist; this just adds a
 *  one-liner where the backend description is verbose. */
const TOOL_BLURBS: Record<string, string> = {
  search_code: "NL hybrid search → code snippets with file:line.",
  read_unit: "Full source of any unit by name, id, or path.",
  read_file: "A whole file in line order, with a unit outline.",
  explore: "Callers / callees / imports with signatures + edges.",
  find_symbol: "Fuzzy qualified-name substring search.",
  repo_overview: "Language mix, module tree, hotspots.",
  list_repos: "Every ingested repo with counts + languages.",
  get_module_summary: "Summary of one module's units.",
  get_risks: "External dependencies a symbol touches.",
  update_memory: "Append opaque session data, append-only.",
  ingest_repository: "Ingest a server-side path under a repo id.",
  get_context: "DEPRECATED — alias for search_code.",
  query_graph: "DEPRECATED — alias for explore.",
  get_related_components: "DEPRECATED — alias for explore.",
};

export default function AgentsPage() {
  const client = getMemoryClient();
  const [tab, setTab] = useState<Tab>("connect");

  const tools = useQuery({
    queryKey: ["mcp-tools"],
    queryFn: () => client.listTools(),
    enabled: tab === "tools",
  });

  const audit = useQuery({
    queryKey: ["audit-tail-agents"],
    queryFn: () => client.auditTail(50),
    refetchInterval: tab === "connected" ? 5_000 : false,
    enabled: tab === "connected",
  });

  const toolList = tools.data?.tools ?? [];

  const actors = useMemo(() => {
    const entries = audit.data?.entries ?? [];
    const m = new Map<string, { calls: number; lastSeq: number }>();
    for (const e of entries) {
      const p = e.payload as Record<string, unknown>;
      const meta = (p.metadata as Record<string, unknown>) ?? p;
      const actor = (p.actor as string) ?? (meta.user_scope as string) ?? "agent";
      const cur = m.get(actor) ?? { calls: 0, lastSeq: 0 };
      cur.calls += 1;
      cur.lastSeq = Math.max(cur.lastSeq, e.seq);
      m.set(actor, cur);
    }
    return Array.from(m.entries())
      .map(([actor, v]) => ({ actor, ...v }))
      .sort((a, b) => b.lastSeq - a.lastSeq);
  }, [audit.data]);

  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader
        title="Agents"
        subtitle="Connect an agent, browse the tool surface, see who's calling."
      />

      <div className="mb-4 flex gap-1 border-b border-border">
        <TabBtn on={tab === "connect"} onClick={() => setTab("connect")}>Connect</TabBtn>
        <TabBtn on={tab === "tools"} onClick={() => setTab("tools")}>Tools</TabBtn>
        <TabBtn on={tab === "connected"} onClick={() => setTab("connected")}>Connected</TabBtn>
      </div>

      {tab === "connect" && (
        <div className="space-y-3.5">
          <Panel title="Connect Claude Code">
            <div className="px-4 py-4">
              <div className="mb-3 text-[12.5px] text-muted">
                One command — works in every session afterward.
              </div>
              <pre className="overflow-x-auto whitespace-pre rounded-lg bg-[#1d1d1b] px-4 py-3.5 font-mono text-[12.5px] text-[#e6e6e6]">
                {CONNECT_CMD}
              </pre>
              <div className="mt-3 flex gap-2">
                <CopyBtn text={CONNECT_CMD}>copy command</CopyBtn>
              </div>
            </div>
          </Panel>

          <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
            <Panel title="SSE">
              <div className="px-4 py-4">
                <div className="font-mono text-[13px] text-accentInk">/mcp/sse</div>
                <div className="mt-2 text-[12.5px] text-muted">
                  Long-lived stream — Claude Code / remote agents.
                </div>
              </div>
            </Panel>
            <Panel title="stdio (lite)">
              <div className="px-4 py-4">
                <div className="font-mono text-[13px] text-accentInk">memcl mcp</div>
                <div className="mt-2 text-[12.5px] text-muted">
                  Spawned per session, no server — the indie path.
                </div>
              </div>
            </Panel>
          </div>
        </div>
      )}

      {tab === "tools" && (
        <Panel title={<span>Tools {toolList.length ? <span className="font-normal text-muted">· {toolList.length}</span> : null}</span>}>
          <div className="px-3 py-3">
            {tools.isLoading ? (
              <div className="px-1 py-4 text-[13px] text-muted">Loading tool registry…</div>
            ) : toolList.length === 0 ? (
              <div className="px-1 py-4 text-[13px] text-muted">No tools reported.</div>
            ) : (
              <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                {toolList.map((t) => (
                  <div key={t.name} className="rounded-[9px] border border-border bg-bg px-3.5 py-3 hover:border-border2">
                    <div className="font-mono text-[13px] font-semibold text-accentInk">{t.name}</div>
                    <div className="mt-1 text-[12px] text-muted2">
                      {TOOL_BLURBS[t.name] ?? t.schema?.title ?? "—"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Panel>
      )}

      {tab === "connected" && (
        <Panel title="Connected agents — derived from audit actors">
          <div className="px-1 py-1">
            {audit.isLoading ? (
              <div className="px-3 py-5 text-[13px] text-muted">Loading audit tail…</div>
            ) : actors.length === 0 ? (
              <div className="px-3 py-5 text-[13px] text-muted">
                No agent activity in the current tail.
              </div>
            ) : (
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-[11.5px] uppercase tracking-wide text-muted">
                    <th className="px-3 py-2 text-left font-semibold">agent</th>
                    <th className="px-3 py-2 text-left font-semibold">calls</th>
                    <th className="px-3 py-2 text-left font-semibold">last seq</th>
                  </tr>
                </thead>
                <tbody>
                  {actors.map((a) => (
                    <tr key={a.actor} className="border-t border-border">
                      <td className="px-3 py-2.5 font-semibold">{a.actor}</td>
                      <td className="px-3 py-2.5 tabular-nums">{a.calls}</td>
                      <td className="px-3 py-2.5 font-mono text-muted">#{a.lastSeq}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
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

function CopyBtn({ text, children }: { text: string; children: React.ReactNode }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }
      }}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border2 bg-bg px-3 py-1.5 text-[12.5px] font-medium text-muted2 hover:border-muted hover:text-fg"
    >
      {done ? <Check size={13} /> : <Copy size={13} />} {done ? "copied" : children}
    </button>
  );
}
