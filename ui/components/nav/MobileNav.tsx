"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { getMemoryClient } from "@/lib/api";
import { computePosture, PostureBadge } from "@/components/ui/posture-badge";
import {
  HOME_ITEM,
  NAV_GROUPS,
  isActive,
  type NavItem,
} from "@/components/nav/nav-items";

/** Mobile navigation: slim fixed top bar + slide-over drawer, visible
 *  below `md` only (the Sidebar takes over from `md` up).
 *
 *  Drawer closes on: backdrop click, Escape, route change.
 *  Focus moves into the drawer on open and back to the hamburger on close.
 */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  // Same heartbeat as the Sidebar footer; the query key dedupes the fetch.
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 30_000,
  });
  const posture = computePosture(status.data ?? null);

  // Close when the route changes (link taps inside the drawer).
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Escape closes the drawer.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Focus management: into the drawer on open, back to the button on close.
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      drawerRef.current?.focus();
    } else if (wasOpen.current) {
      wasOpen.current = false;
      buttonRef.current?.focus();
    }
  }, [open]);

  // Lock background scroll while the drawer is open.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  return (
    <>
      <header className="md:hidden fixed inset-x-0 top-0 z-40 flex h-14 items-center justify-between border-b border-border bg-panel/90 px-4 backdrop-blur">
        <Link href="/" className="flex items-center gap-2">
          <FileSearch size={16} className="text-accent" />
          <div className="leading-tight">
            <div className="text-sm font-semibold">Memory-CL</div>
            <div className="text-[10px] muted font-mono uppercase tracking-wider">
              command center
            </div>
          </div>
        </Link>
        <button
          ref={buttonRef}
          type="button"
          aria-label="Open navigation menu"
          aria-expanded={open}
          aria-controls="mobile-nav-drawer"
          onClick={() => setOpen(true)}
          className="rounded-md p-2 text-muted transition-colors hover:bg-bg/40 hover:text-fg"
        >
          <Menu size={18} />
        </button>
      </header>

      {open && (
        <div className="md:hidden fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-bg/70 backdrop-blur-sm"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div
            ref={drawerRef}
            id="mobile-nav-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            tabIndex={-1}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col border-r border-border bg-panel shadow-xl focus-visible:outline-none"
          >
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <Link href="/" className="flex items-center gap-2">
                <FileSearch size={18} className="text-accent" />
                <div className="leading-tight">
                  <div className="text-sm font-semibold">Memory-CL</div>
                  <div className="text-[10px] muted font-mono uppercase tracking-wider">
                    command center
                  </div>
                </div>
              </Link>
              <button
                type="button"
                aria-label="Close navigation menu"
                onClick={() => setOpen(false)}
                className="rounded-md p-2 text-muted transition-colors hover:bg-bg/40 hover:text-fg"
              >
                <X size={16} />
              </button>
            </div>

            <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
              <MobileNavLink
                item={HOME_ITEM}
                active={isActive(pathname, HOME_ITEM.href)}
              />

              {NAV_GROUPS.map((group) => (
                <div key={group.label} className="space-y-1">
                  <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-wider muted/80">
                    {group.label}
                  </div>
                  {group.items.map((item) => (
                    <MobileNavLink
                      key={item.href}
                      item={item}
                      active={isActive(pathname, item.href)}
                    />
                  ))}
                </div>
              ))}
            </nav>

            <footer className="space-y-2 border-t border-border px-4 py-3">
              <Link href="/status" className="block">
                <PostureBadge posture={posture} size="sm" className="w-full justify-center" />
              </Link>
              <div className="text-[10px] font-mono muted text-center">v0.1</div>
            </footer>
          </div>
        </div>
      )}
    </>
  );
}

function MobileNavLink({ item, active }: { item: NavItem; active: boolean }) {
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
