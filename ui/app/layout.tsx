import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { Sidebar } from "@/components/nav/Sidebar";
import { MobileNav } from "@/components/nav/MobileNav";
import { CommandPalette } from "@/components/nav/CommandPalette";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Memory-CL · transparency layer",
  description:
    "Cognitive interface over a deterministic AI memory + retrieval engine.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg text-fg">
        <Providers>
          <MobileNav />
          <div className="flex min-h-screen">
            <Sidebar />
            {/* pt-20 below md clears the fixed h-14 mobile top bar (+1.5rem gap,
                matching the desktop py-6 rhythm). */}
            <main className="flex-1 min-w-0 px-6 pb-6 pt-20 md:pt-6">{children}</main>
          </div>
          <CommandPalette />
        </Providers>
      </body>
    </html>
  );
}
