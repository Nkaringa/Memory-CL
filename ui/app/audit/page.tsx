"use client";

import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { AuditViewer } from "@/components/AuditViewer";
import { getMemoryClient } from "@/lib/api";

export default function AuditPage() {
  const tail = useQuery({
    queryKey: ["audit", "tail", 50],
    queryFn: () => getMemoryClient().auditTail(50),
    refetchInterval: 15_000,
  });
  const verify = useQuery({
    queryKey: ["audit", "verify"],
    queryFn: () => getMemoryClient().auditVerify(),
    enabled: false, // verify only when the user asks
  });

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader
        eyebrow="system"
        title="Audit"
        description="Phase-8 hash-chained audit trail. Tampering breaks the chain at the first modified link — verify walks every prev_hash to confirm integrity."
        crumbs={[{ label: "System" }, { label: "Audit" }]}
      />

      {tail.isError && (
        <ErrorState
          title="Could not load audit tail"
          description="The /audit/tail endpoint failed. Confirm the durable JSONL sink is reachable."
          error={tail.error}
          onRetry={() => tail.refetch()}
          className="mb-6"
        />
      )}

      <AuditViewer
        tail={tail.data ?? null}
        verify={verify.data ?? null}
        onRefresh={() => tail.refetch()}
        onVerify={() => verify.refetch()}
        isRefreshing={tail.isFetching}
        isVerifying={verify.isFetching}
      />
    </div>
  );
}
