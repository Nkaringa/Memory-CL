import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Memory-CL · Command Center",
  description: "Command center over a deterministic AI memory + retrieval engine.",
};

/** Minimal root layout — wraps every route with the global CSS and
 *  React Query / theme providers.  The app shell (sidebar, nav, status
 *  strip) lives in the (main) group layout so unauthenticated pages
 *  (login) get a bare canvas. */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-fg">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
