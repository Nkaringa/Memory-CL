import type { ReactNode } from "react";

/** Bare layout for unauthenticated pages (login, etc.).
 *  No sidebar, no status strip — just the children rendered on the bare canvas
 *  provided by the root layout. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
