"use client";

import { useQuery } from "@tanstack/react-query";
import { getMemoryClient } from "@/lib/api";

/** The persistent live system-pulse bar shown under the top bar on every
 *  page — the constant "the system is alive" signal of a control room. */
export function StatusStrip() {
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 30_000,
  });
  const repos = useQuery({
    queryKey: ["repos"],
    queryFn: () => getMemoryClient().listRepos(),
    refetchInterval: 60_000,
  });
  const audit = useQuery({
    queryKey: ["audit-tail-strip"],
    queryFn: () => getMemoryClient().auditTail(50),
    refetchInterval: 5_000,
  });

  const s = status.data;
  const healthy = s ? s.boot_overall_ok : true;
  const embeddings = s?.embeddings_enabled ?? null;
  const tools = s?.mcp_tool_count ?? null;
  const repoCount = repos.data?.repos.length ?? null;
  const unitCount = repos.data?.repos.reduce((a, r) => a + r.units, 0) ?? null;
  const calls = audit.data?.entries?.length ?? 0;

  return (
    <div className="sticky top-0 z-[5] flex h-[38px] items-center gap-0 border-b border-border bg-gradient-to-b from-[#fcfdfc] to-[#fafbfa] px-6 text-[12.5px]">
      <Seg first>
        <span className={`h-[7px] w-[7px] rounded-full ${healthy ? "bg-ok" : "bg-warn"}`} />
        {healthy ? "healthy" : "degraded"}
      </Seg>
      <Seg>
        embeddings{" "}
        <b className={embeddings ? "text-ok" : "text-warn"}>
          {embeddings === null ? "—" : embeddings ? "on" : "off"}
        </b>
      </Seg>
      <Seg>
        <b>{tools ?? "—"}</b> tools
      </Seg>
      <Seg>
        <b>{repoCount ?? "—"}</b> repos · <b>{unitCount?.toLocaleString() ?? "—"}</b> units
      </Seg>
      <div className="ml-auto flex items-center gap-1.5 font-semibold text-accentInk">
        <span className="h-1.5 w-1.5 rounded-full bg-accent animate-blink" />
        {calls} call{calls === 1 ? "" : "s"} recent
      </div>
    </div>
  );
}

function Seg({ children, first }: { children: React.ReactNode; first?: boolean }) {
  return (
    <div
      className={`flex h-[18px] items-center gap-1.5 border-r border-border text-muted2 ${
        first ? "pr-4" : "px-4"
      }`}
    >
      <span className="contents [&_b]:tabular-nums [&_b]:text-fg">{children}</span>
    </div>
  );
}
