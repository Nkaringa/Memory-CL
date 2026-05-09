import { type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Breadcrumbs, type Crumb } from "@/components/ui/breadcrumbs";

interface PageHeaderProps {
  title: string;
  description?: string;
  /** Optional kicker shown above the title (small uppercase label). */
  eyebrow?: string;
  /** Right-aligned controls (toggles, actions, badges). */
  actions?: ReactNode;
  crumbs?: Crumb[];
  className?: string;
}

/** Single page-header primitive every page in the app uses.
 *
 *  Eliminates the per-page header drift that was creeping in — same
 *  vertical rhythm, same border, same bottom padding everywhere.
 */
export function PageHeader({
  title, description, eyebrow, actions, crumbs, className,
}: PageHeaderProps) {
  return (
    <header className={cn("border-b border-border pb-5 mb-6 space-y-3", className)}>
      {crumbs && crumbs.length > 0 && <Breadcrumbs items={crumbs} />}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div className="space-y-1 min-w-0">
          {eyebrow && (
            <div className="text-[10px] font-mono muted uppercase tracking-wider">
              {eyebrow}
            </div>
          )}
          <h1 className="text-2xl font-semibold tracking-tight leading-tight">
            {title}
          </h1>
          {description && (
            <p className="text-sm muted max-w-2xl leading-relaxed">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
    </header>
  );
}
