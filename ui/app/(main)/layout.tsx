import type { ReactNode } from "react";
import { Sidebar } from "@/components/nav/Sidebar";
import { MobileNav } from "@/components/nav/MobileNav";
import { CommandPalette } from "@/components/nav/CommandPalette";
import { StatusStrip } from "@/components/shell/StatusStrip";
import { AuthGuard } from "@/components/AuthGuard";

/** App-shell layout — sidebar, mobile nav, status strip, auth guard.
 *  Applied to every route in the (main) group (all authenticated pages). */
export default function MainLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <MobileNav />
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="hidden md:block">
            <StatusStrip />
          </div>
          <main className="min-w-0 flex-1 px-6 pb-12 pt-16 md:pt-5">{children}</main>
        </div>
      </div>
      <CommandPalette />
    </AuthGuard>
  );
}
