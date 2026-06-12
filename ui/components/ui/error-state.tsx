import { type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MemoryClientError } from "@/lib/api";

interface ErrorStateProps {
  title?: string;
  description?: string;
  /** Pass the caught Error / message string directly. */
  error?: unknown;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  children?: ReactNode;
}

/** Standard error surface for any panel that failed.
 *
 *  Rendered with the bad-tone palette so users instantly distinguish
 *  it from an empty state. Includes a retry affordance when the
 *  caller passes one. Error message goes in a monospace box so a
 *  user can copy/paste it into a bug report.
 */
export function ErrorState({
  title = "Something went wrong",
  description,
  error,
  onRetry,
  retryLabel = "Retry",
  className,
  children,
}: ErrorStateProps) {
  let message: string | null;
  if (error instanceof MemoryClientError) {
    if (error.status === 401) {
      message = "API key missing or invalid (check MCP_API_KEY)";
    } else {
      // Prefer the FastAPI `detail` field when present.
      const body = error.body as Record<string, unknown> | null | undefined;
      const detail = body?.detail;
      if (typeof detail === "string" && detail) {
        message = detail;
      } else if (detail != null) {
        message = JSON.stringify(detail);
      } else {
        message = error.message;
      }
    }
  } else if (error instanceof Error) {
    if (error.name === "AbortError") {
      message = "Request timed out";
    } else {
      message = error.message;
    }
  } else if (typeof error === "string") {
    message = error;
  } else if (error) {
    message = JSON.stringify(error);
  } else {
    message = null;
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-bad/30 bg-bad/[0.06] p-5 space-y-3",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="text-bad shrink-0 mt-0.5" />
        <div className="flex-1 space-y-1">
          <div className="text-sm font-semibold text-bad">{title}</div>
          {description && (
            <div className="text-xs text-fg/80 leading-relaxed">{description}</div>
          )}
        </div>
        {onRetry && (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            <RefreshCw size={12} /> {retryLabel}
          </Button>
        )}
      </div>
      {message && (
        <pre className="font-mono text-[11px] whitespace-pre-wrap break-all text-bad/90 bg-bad/5 border border-bad/20 rounded p-3 overflow-auto max-h-40">
          {message}
        </pre>
      )}
      {children}
    </div>
  );
}
