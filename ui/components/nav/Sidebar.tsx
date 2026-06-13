"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import {
  HOME_ITEM,
  NAV_GROUPS,
  isActive,
  type NavItem,
} from "@/components/nav/nav-items";

export function Sidebar() {
  const pathname = usePathname();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 30_000,
  });
  const healthy = status.data ? status.data.boot_overall_ok : true;

  return (
    <aside className="hidden md:flex md:w-[244px] flex-col border-r border-border bg-panel sticky top-0 h-screen">
      <Link href="/" className="flex items-center gap-2.5 px-4 pt-4 pb-2">
        <span className="h-5 w-5 rounded-[6px] bg-gradient-to-br from-accent to-emerald-400" />
        <span className="text-[15px] font-semibold tracking-tight">Memory-CL</span>
        <span className="ml-auto flex items-center gap-1 rounded-[5px] bg-accentSoft px-1.5 py-0.5 text-[9.5px] font-bold tracking-wide text-accentInk">
          <span className="h-1.5 w-1.5 rounded-full bg-accent animate-blink" />
          LIVE
        </span>
      </Link>

      <nav className="flex-1 px-3 py-2 overflow-y-auto">
        <div className="pt-1">
          <NavLink item={HOME_ITEM} active={isActive(pathname, HOME_ITEM.href)} />
        </div>
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mt-1">
            <div className="px-2.5 pb-1.5 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
              {group.label}
            </div>
            {group.items.map((item) => (
              <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} />
            ))}
          </div>
        ))}
      </nav>

      <footer className="mt-auto flex items-center gap-2 border-t border-border px-4 py-2.5 text-[11.5px] text-muted">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            healthy ? "bg-ok shadow-[0_0_0_3px_rgba(14,159,110,0.15)]" : "bg-warn",
          )}
        />
        {healthy ? "all systems healthy" : "degraded"} · v2
      </footer>
    </aside>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const { href, label, Icon } = item;
  return (
    <Link
      href={href as never}
      className={cn(
        "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors",
        active
          ? "bg-accentSoft text-accentInk"
          : "text-muted2 hover:bg-panel2 hover:text-fg",
      )}
    >
      <Icon size={16} className={active ? "text-accentInk" : "text-muted opacity-90"} strokeWidth={2} />
      <span className="truncate">{label}</span>
    </Link>
  );
}
