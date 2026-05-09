import { type LucideIcon } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface SectionProps {
  title: string;
  Icon?: LucideIcon;
  description?: string;
  /** Right-aligned controls / counts. */
  trailing?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Labelled grouping primitive used by /status, /dashboard and any
 *  other page that needs to chunk content into named sections.
 *
 *  Same vertical rhythm + same kicker treatment everywhere — readers
 *  learn the section pattern once and never have to re-orient.
 */
export function Section({
  title, Icon, description, trailing, children, className,
}: SectionProps) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex items-end justify-between gap-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            {Icon && <Icon size={14} className="text-accent" />}
            <h2 className="text-xs font-semibold uppercase tracking-wider muted">
              {title}
            </h2>
          </div>
          {description && (
            <p className="text-[11px] muted/80 leading-relaxed">{description}</p>
          )}
        </div>
        {trailing}
      </div>
      <div>{children}</div>
    </section>
  );
}
