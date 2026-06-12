"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { FileSearch } from "lucide-react";
import { cn } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import { computePosture, PostureBadge } from "@/components/ui/posture-badge";
import {
  HOME_ITEM,
  NAV_GROUPS,
  isActive,
  type NavItem,
} from "@/components/nav/nav-items";

export function Sidebar() {
  const pathname = usePathname();
  // Lightweight posture indicator at the bottom of the sidebar — gives
  // operators a constant heartbeat without leaving the current page.
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 30_000,
  });
  const posture = computePosture(status.data ?? null);

  return (
    <aside className="hidden md:flex md:w-60 lg:w-64 flex-col border-r border-border bg-panel/50">
      <Link href="/" className="flex items-center gap-2 px-5 py-5 border-b border-border">
        <FileSearch size={18} className="text-accent" />
        <div className="leading-tight">
          <div className="text-sm font-semibold">Memory-CL</div>
          <div className="text-[10px] muted font-mono uppercase tracking-wider">
            transparency layer
          </div>
        </div>
      </Link>

      <nav className="flex-1 px-3 py-4 space-y-5 overflow-y-auto">
        <NavLink item={HOME_ITEM} active={isActive(pathname, HOME_ITEM.href)} />

        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="space-y-1">
            <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-wider muted/80">
              {group.label}
            </div>
            {group.items.map((item) => (
              <NavLink
                key={item.href}
                item={item}
                active={isActive(pathname, item.href)}
              />
            ))}
          </div>
        ))}
      </nav>

      <footer className="border-t border-border px-4 py-3 space-y-2">
        <Link href="/status" className="block">
          <PostureBadge posture={posture} size="sm" className="w-full justify-center" />
        </Link>
        <div className="text-[10px] font-mono muted text-center">v0.1</div>
      </footer>
    </aside>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const { href, label, Icon, hint } = item;
  return (
    <Link
      href={href as never}
      className={cn(
        "group flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
        active
          ? "bg-bg text-fg"
          : "text-muted hover:text-fg hover:bg-bg/40",
      )}
    >
      <Icon size={15} className={cn(active ? "text-accent" : "text-muted")} />
      <div className="flex-1 min-w-0">
        <div className="truncate">{label}</div>
        <div className="text-[10px] muted truncate">{hint}</div>
      </div>
    </Link>
  );
}
