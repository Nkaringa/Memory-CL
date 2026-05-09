import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  Icon?: LucideIcon;
  title: string;
  description?: string;
  /** Primary action — typically a link or button. */
  action?: ReactNode;
  className?: string;
}

/** Standard empty-state for any panel that has nothing to show.
 *
 *  Single primitive across the app — same icon weight, same vertical
 *  rhythm, same subdued chrome — so a user instantly recognizes
 *  "this surface is reachable but currently empty" vs "broken".
 */
export function EmptyState({
  Icon, title, description, action, className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        "rounded-lg border border-dashed border-border bg-panel/30",
        "px-6 py-12 gap-3",
        className,
      )}
    >
      {Icon && (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-bg/60 border border-border">
          <Icon size={18} className="text-muted" />
        </div>
      )}
      <div className="space-y-1 max-w-md">
        <div className="text-sm font-medium text-fg">{title}</div>
        {description && (
          <div className="text-xs muted leading-relaxed">{description}</div>
        )}
      </div>
      {action && <div className="pt-1">{action}</div>}
    </div>
  );
}
