"use client";

import { useState } from "react";
import {
  AlertTriangle, Check, Link2, RefreshCw, ShieldAlert, ShieldCheck, X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { JsonView } from "@/components/ui/json-view";
import { EmptyState } from "@/components/ui/empty-state";
import { cn, truncHash } from "@/lib/utils";
import type { AuditEntryView, AuditTailResponse, AuditVerifyResponse } from "@/lib/types";

interface AuditViewerProps {
  tail: AuditTailResponse | null;
  verify: AuditVerifyResponse | null;
  onRefresh: () => void;
  onVerify: () => void;
  isRefreshing: boolean;
  isVerifying: boolean;
}

export function AuditViewer({
  tail, verify, onRefresh, onVerify, isRefreshing, isVerifying,
}: AuditViewerProps) {
  const [selected, setSelected] = useState<AuditEntryView | null>(null);

  return (
    <div className="space-y-4">

      {/* Chain integrity banner — always at the top so a broken chain
       *  cannot be missed. Renders nothing until the operator verifies. */}
      {verify && <ChainIntegrityBanner verify={verify} />}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4">

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Link2 size={14} className="text-accent" /> Audit chain · tail
            </CardTitle>
            <div className="flex items-center gap-2">
              <Button
                size="sm" variant="secondary"
                onClick={onRefresh} disabled={isRefreshing}
              >
                <RefreshCw size={12} className={cn(isRefreshing && "animate-spin")} />
                {isRefreshing ? "Loading…" : "Refresh"}
              </Button>
              <Button size="sm" onClick={onVerify} disabled={isVerifying}>
                {isVerifying ? "Verifying…" : "Verify chain"}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {!tail ? (
              <EmptyState
                Icon={Link2}
                title="No audit data"
                description="Click Refresh to load the most recent entries from the durable JSONL sink."
              />
            ) : tail.entries.length === 0 ? (
              <EmptyState
                Icon={Link2}
                title="Chain is empty"
                description={`The chain has length ${tail.chain_length} — but no entries were returned in the requested window.`}
              />
            ) : (
              <ul className="divide-y divide-border">
                {tail.entries.map((entry) => {
                  const isBroken =
                    verify?.broken_at_seq !== null &&
                    verify?.broken_at_seq !== undefined &&
                    entry.seq === verify.broken_at_seq;
                  return (
                    <li key={entry.seq}>
                      <button
                        type="button"
                        onClick={() => setSelected(entry)}
                        className={cn(
                          "w-full text-left grid grid-cols-[60px_120px_1fr_180px] gap-2 px-2 py-2 transition-colors",
                          "hover:bg-bg/40",
                          selected?.seq === entry.seq && "bg-bg/60",
                          isBroken && "bg-bad/10 hover:bg-bad/15",
                        )}
                      >
                        <span className="font-mono text-xs muted flex items-center gap-1">
                          {isBroken && <AlertTriangle size={10} className="text-bad" />}
                          #{entry.seq}
                        </span>
                        <Badge variant={isBroken ? "bad" : "muted"}>
                          {(entry.payload.action as string | undefined) ?? "—"}
                        </Badge>
                        <span className="font-mono text-xs truncate">
                          {(entry.payload.entity_id as string | undefined) ?? "—"}
                        </span>
                        <span className="font-mono text-[10px] muted truncate text-right">
                          {truncHash(entry.hash, 12)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {verify?.intact ? (
                  <ShieldCheck size={14} className="text-ok" />
                ) : verify ? (
                  <ShieldAlert size={14} className="text-bad" />
                ) : (
                  <ShieldCheck size={14} className="text-muted" />
                )}
                Chain integrity
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {verify ? (
                <>
                  <div className="flex items-center gap-2">
                    <Badge variant={verify.intact ? "ok" : "bad"}>
                      {verify.intact ? (
                        <><Check size={10} /> intact</>
                      ) : (
                        <><X size={10} /> broken</>
                      )}
                    </Badge>
                    <span className="font-mono text-xs muted">
                      length {verify.chain_length}
                    </span>
                  </div>
                  {!verify.intact && (
                    <div className="rounded-md border border-bad/30 bg-bad/[0.06] p-2.5 space-y-1">
                      <div className="font-mono text-[11px] text-bad">
                        broken at seq {verify.broken_at_seq}
                      </div>
                      <div className="text-[11px] text-fg/80 leading-relaxed">
                        {verify.error}
                      </div>
                    </div>
                  )}
                  {verify.intact && (
                    <p className="text-[11px] muted leading-relaxed">
                      Every prev_hash matches its predecessor's hash. The
                      Phase-8 hash-chain integrity property holds for all{" "}
                      {verify.chain_length} entries.
                    </p>
                  )}
                </>
              ) : (
                <p className="text-xs muted leading-relaxed">
                  Click <span className="font-mono">Verify chain</span> to walk
                  every link from genesis to head.
                </p>
              )}
            </CardContent>
          </Card>

          {selected && (
            <Card>
              <CardHeader>
                <CardTitle>Entry · #{selected.seq}</CardTitle>
                <Badge variant="muted">{truncHash(selected.hash, 10)}</Badge>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="text-[10px] font-mono muted">
                  prev_hash: {truncHash(selected.prev_hash, 14)}
                </div>
                <JsonView value={selected.payload} maxHeight="20rem" />
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function ChainIntegrityBanner({ verify }: { verify: AuditVerifyResponse }) {
  if (verify.intact) {
    return (
      <div className="rounded-md border border-ok/30 bg-ok/[0.06] px-4 py-2.5 flex items-center gap-3">
        <ShieldCheck size={16} className="text-ok shrink-0" />
        <div className="flex-1 text-xs">
          <span className="font-semibold text-ok">Chain intact</span>{" "}
          <span className="muted">
            · all {verify.chain_length} entries verified · safe to trust
            audit-derived claims
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-bad/40 bg-bad/[0.08] px-4 py-3 flex items-start gap-3">
      <AlertTriangle size={16} className="text-bad shrink-0 mt-0.5" />
      <div className="flex-1 space-y-1">
        <div className="text-sm font-semibold text-bad">
          Audit chain is broken at seq {verify.broken_at_seq}
        </div>
        <div className="text-xs text-fg/80 leading-relaxed">
          Replay outcomes and time-travel claims should be treated with
          skepticism until the chain is rebuilt from the durable JSONL sink.
        </div>
        <div className="font-mono text-[11px] text-bad/90 mt-1">
          {verify.error}
        </div>
      </div>
    </div>
  );
}
