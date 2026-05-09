"use client";

import { useMemo, useState } from "react";
import { Copy, Check } from "lucide-react";
import { canonicalJson, copyToClipboard } from "@/lib/utils";
import { cn } from "@/lib/utils";

/** Lightweight Monaco-equivalent for read-only JSON.
 *
 * Why not Monaco? Monaco is heavy and only adds value when editing.
 * For Phase-9 inspector pages every JSON pane is read-only, so we
 * render canonical JSON as syntax-tinted <pre>. Monaco can be
 * dropped in here later for the writable surfaces (e.g. tool input).
 */
export function JsonView({
  value, className, maxHeight = "60vh",
}: {
  value: unknown;
  className?: string;
  maxHeight?: string;
}) {
  const text = useMemo(() => canonicalJson(value), [value]);
  const [copied, setCopied] = useState(false);
  return (
    <div className={cn("relative rounded-md border border-border bg-bg/40", className)}>
      <button
        type="button"
        onClick={async () => {
          if (await copyToClipboard(text)) {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }
        }}
        className="absolute top-2 right-2 inline-flex h-7 w-7 items-center justify-center rounded text-muted hover:text-fg hover:bg-panel"
        aria-label="copy JSON"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
      <pre
        className="font-mono text-xs leading-relaxed whitespace-pre overflow-auto p-4 pr-10 text-fg"
        style={{ maxHeight }}
      >
        {text}
      </pre>
    </div>
  );
}
