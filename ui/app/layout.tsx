import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { Sidebar } from "@/components/nav/Sidebar";
import { MobileNav } from "@/components/nav/MobileNav";
import { CommandPalette } from "@/components/nav/CommandPalette";
import { StatusStrip } from "@/components/shell/StatusStrip";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Memory-CL · Command Center",
  description: "Command center over a deterministic AI memory + retrieval engine.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-fg">
        <Providers>
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
        </Providers>
      </body>
    </html>
  );
}
