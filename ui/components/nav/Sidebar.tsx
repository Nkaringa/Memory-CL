"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import { fetchMe, logout } from "@/lib/auth";
import type { UserView } from "@/lib/types";
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

      <div className="mt-auto border-t border-border">
        {/* system health line */}
        <div className="flex items-center gap-2 px-4 py-2 text-[11.5px] text-muted">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              healthy ? "bg-ok shadow-[0_0_0_3px_rgba(14,159,110,0.15)]" : "bg-warn",
            )}
          />
          {healthy ? "all systems healthy" : "degraded"} · v2
        </div>
        {/* current user chip */}
        <UserFooter />
      </div>
    </aside>
  );
}

/** Renders the signed-in user's email + a log-out button at the bottom of
 *  the sidebar.  Calls fetchMe() once on mount; shows nothing while loading. */
function UserFooter() {
  const router = useRouter();
  const [user, setUser] = useState<UserView | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMe().then((me) => {
      if (!cancelled && me.user) setUser(me.user);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (!user) return null;

  return (
    <div className="flex items-center gap-2 border-t border-border px-4 py-2.5">
      <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accentSoft text-[9px] font-bold uppercase text-accentInk">
        {user.email.charAt(0)}
      </span>
      <span className="flex-1 truncate text-[11.5px] font-medium text-muted2">{user.email}</span>
      <button
        type="button"
        onClick={handleLogout}
        title="Log out"
        className="flex-none rounded-md px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:text-bad"
      >
        out
      </button>
    </div>
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
