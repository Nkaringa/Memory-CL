import { NextResponse, type NextRequest } from "next/server";

/**
 * /api/* request-header injection.
 *
 * The Memory-CL api requires `X-API-Key: $MCP_API_KEY` for
 * `/mcp/tools/*` endpoints when an API key is configured (always true
 * in staging/prod). The browser-side UI deliberately doesn't carry
 * that secret — anyone with dev-tools could read it. Instead, we
 * inject the key here, server-side, before the Next.js rewrite
 * proxies the request to the api container.
 *
 * Reads `process.env.MCP_API_KEY` per-request (NOT at module load),
 * so the secret can be supplied at container runtime via the compose
 * file's `environment` block. Never appears in the bundled JS.
 *
 * If `MCP_API_KEY` is unset (dev mode without auth), the middleware
 * is a pass-through. No header is added; the api accepts the request
 * because `Settings.mcp_api_key` is None and the auth dependency
 * short-circuits.
 *
 * Adding the header on the non-`/mcp/tools/*` paths (e.g.
 * `/health/*`, `/status`, `/retrieve`) is harmless — those routes
 * don't enforce auth and just ignore the extra header.
 */
export function middleware(request: NextRequest) {
  const apiKey = process.env.MCP_API_KEY ?? "";
  if (!apiKey) {
    return NextResponse.next();
  }
  const headers = new Headers(request.headers);
  headers.set("x-api-key", apiKey);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Only `/api/*` (the rewrite path that proxies to the backend
  // api container). Static assets, the UI's own pages, and Next.js
  // internals are skipped — they don't talk to the backend.
  matcher: "/api/:path*",
};
