"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchMe } from "@/lib/auth";

/** Paths that are always accessible without authentication. */
const PUBLIC_PATHS = ["/login", "/setup"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

/** App-wide auth guard.
 *
 * On mount, calls GET /auth/me.  If unauthenticated and the current path is
 * not a public path, redirects to /login.  While the check is in flight,
 * protected routes show a blank loading screen to avoid flashing protected
 * content.  Public routes (login, setup) render immediately.
 *
 * The current-user display lives in the Sidebar's UserFooter component and
 * reads its own fetchMe() call (fast because the same session cookie is used
 * and the browser deduplicates the identical in-flight requests).
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMe().then((me) => {
      if (cancelled) return;
      setChecked(true);
      if (!me.authenticated && !isPublic(pathname)) {
        router.replace("/login");
      }
    });
    return () => {
      cancelled = true;
    };
    // Re-run whenever the path changes so navigating to a protected route
    // after the session expires is caught immediately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // Block render on protected routes until we know the auth state.
  if (!checked && !isPublic(pathname)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <span className="text-[13px] text-muted">Loading…</span>
      </div>
    );
  }

  return <>{children}</>;
}
