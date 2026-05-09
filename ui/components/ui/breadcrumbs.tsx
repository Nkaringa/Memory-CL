import Link from "next/link";
import { ChevronRight, Home } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Crumb {
  label: string;
  href?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
  className?: string;
}

/** Compact breadcrumb trail for deep pages.
 *
 *  Convention: the first crumb is always Home (we render the icon).
 *  Last crumb has no `href` — it's the current page.
 */
export function Breadcrumbs({ items, className }: BreadcrumbsProps) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="breadcrumb" className={cn("flex items-center gap-1.5 text-xs", className)}>
      <Link
        href="/"
        className="flex h-5 items-center justify-center text-muted hover:text-fg transition-colors"
        aria-label="Home"
      >
        <Home size={12} />
      </Link>
      {items.map((c, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={`${c.label}-${i}`} className="flex items-center gap-1.5">
            <ChevronRight size={12} className="text-muted/60" />
            {isLast || !c.href ? (
              <span className="text-fg font-medium" aria-current={isLast ? "page" : undefined}>
                {c.label}
              </span>
            ) : (
              <Link href={c.href as never} className="text-muted hover:text-fg transition-colors">
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}
