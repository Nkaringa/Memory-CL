import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** Shape primitive for loading placeholders. Pulses subtly to mark
 *  "data not yet here" without flashing the layout when it arrives. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-border/60",
        className,
      )}
      {...props}
    />
  );
}

/** Common preset: a row with badges + text; reused across list views. */
export function SkeletonRow({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-3 py-3", className)}>
      <Skeleton className="h-4 w-12" />
      <Skeleton className="h-4 flex-1 max-w-[18rem]" />
      <Skeleton className="h-5 w-16 rounded-full" />
      <Skeleton className="h-5 w-12 rounded-full" />
    </div>
  );
}

/** Card-shaped skeleton for the metric tiles + status sections. */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-lg border border-border bg-panel/40 p-4 space-y-3", className)}>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-6 w-32" />
      <Skeleton className="h-2 w-full" />
    </div>
  );
}

/** Inline shimmer for short text fragments. */
export function SkeletonText({
  className, lines = 1,
}: { className?: string; lines?: number }) {
  return (
    <div className={cn("space-y-1.5", className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn(
            "h-3",
            // Stagger widths so the block doesn't read as a uniform bar.
            i === lines - 1 ? "w-2/3" : "w-full",
          )}
        />
      ))}
    </div>
  );
}
