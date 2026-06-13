import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Standard page header — title + optional subtitle + right-aligned actions. */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start gap-3">
      <div>
        <h1 className="text-[19px] font-semibold tracking-tight">{title}</h1>
        {subtitle ? <div className="mt-0.5 text-[13px] text-muted">{subtitle}</div> : null}
      </div>
      {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** A bordered content panel with an optional header bar. */
export function Panel({
  title,
  live,
  action,
  className,
  bodyClass,
  children,
}: {
  title?: ReactNode;
  live?: boolean;
  action?: ReactNode;
  className?: string;
  bodyClass?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("overflow-hidden rounded-xl border border-border bg-bg", className)}>
      {title ? (
        <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-[13.5px] font-semibold">
          {title}
          {live ? <LiveBadge /> : null}
          {action ? <div className="ml-auto">{action}</div> : null}
        </div>
      ) : null}
      <div className={cn("p-1.5", bodyClass)}>{children}</div>
    </div>
  );
}

export function LiveBadge() {
  return (
    <span className="flex items-center gap-1 rounded-[5px] bg-accentSoft px-1.5 py-0.5 text-[9.5px] font-bold tracking-wide text-accentInk">
      <span className="h-1.5 w-1.5 rounded-full bg-accent animate-blink" />
      LIVE
    </span>
  );
}

/** Big-number metric tile with optional sublabel + sparkline. */
export function Tile({
  label,
  value,
  unit,
  sub,
  spark,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  spark?: number[];
}) {
  return (
    <div className="rounded-xl border border-border bg-bg p-4">
      <div className="text-[12px] text-muted">{label}</div>
      <div className="mt-1 text-[25px] font-bold tracking-tight tabular-nums">
        {value}
        {unit ? <span className="text-[14px] text-muted">{unit}</span> : null}
      </div>
      {sub ? <div className="mt-0.5 text-[11.5px] text-muted">{sub}</div> : null}
      {spark ? (
        <div className="mt-2 flex h-[22px] items-end gap-[2px]">
          {spark.map((h, i) => (
            <i
              key={i}
              className={cn(
                "flex-1 rounded-[2px]",
                i === spark.length - 1 ? "bg-accent" : "bg-accentSoft",
              )}
              style={{ height: `${Math.max(8, h)}%` }}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** A button-styled link or button. */
export function Btn({
  children,
  href,
  primary,
  className,
  onClick,
}: {
  children: ReactNode;
  href?: string;
  primary?: boolean;
  className?: string;
  onClick?: () => void;
}) {
  const cls = cn(
    "inline-flex items-center gap-1.5 rounded-lg px-3 py-[7px] text-[13px] font-medium transition-colors",
    primary
      ? "bg-accent text-white hover:bg-accentInk"
      : "border border-border2 bg-bg text-muted2 hover:border-muted hover:text-fg",
    className,
  );
  if (href) {
    return (
      <Link href={href as never} className={cls}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" className={cls} onClick={onClick}>
      {children}
    </button>
  );
}
