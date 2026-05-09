import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { Sidebar } from "@/components/nav/Sidebar";
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
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex-1 min-w-0 px-6 py-6">{children}</main>
          </div>
          <CommandPalette />
        </Providers>
      </body>
    </html>
  );
}
