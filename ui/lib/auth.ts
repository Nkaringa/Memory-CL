/* Auth helpers — thin wrappers over the /api/auth/* endpoints.
 *
 * The browser calls these at the /api prefix, which Next.js rewrites to the
 * backend (same origin as the UI). The httpOnly session cookie is therefore
 * set on the UI's origin and flows automatically on every subsequent request.
 *
 * `credentials: "include"` is redundant for same-origin fetches but is
 * explicit here so the intent is clear and the code stays correct if the
 * rewrite target ever becomes cross-origin in a dev environment.
 */

import type { MeResponse } from "@/lib/types";

const BASE = "/api";

async function authFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...opts,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      ...(opts.headers ?? {}),
    },
  });
}

/** GET /auth/me — never throws; returns { authenticated: false, user: null } on
 *  any network / non-2xx error so the guard can treat "unknown" as logged out. */
export async function fetchMe(): Promise<MeResponse> {
  try {
    const resp = await authFetch("/auth/me");
    if (!resp.ok) return { authenticated: false, user: null };
    return (await resp.json()) as MeResponse;
  } catch {
    return { authenticated: false, user: null };
  }
}

/** POST /auth/login — throws on non-2xx (caller shows the error). */
export async function login(email: string, password: string): Promise<MeResponse> {
  const resp = await authFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail ?? "Login failed";
    throw new Error(String(detail));
  }
  return (await resp.json()) as MeResponse;
}

/** POST /auth/register — throws on non-2xx (first user only; subsequent calls
 *  return 401/403 which becomes an error message in the UI). */
export async function register(
  email: string,
  password: string,
  display_name: string,
): Promise<MeResponse> {
  const resp = await authFetch("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail ?? "Registration failed";
    throw new Error(String(detail));
  }
  return (await resp.json()) as MeResponse;
}

/** POST /auth/logout — best-effort; caller redirects regardless. */
export async function logout(): Promise<void> {
  try {
    await authFetch("/auth/logout", { method: "POST" });
  } catch {
    // ignore — the redirect to /login happens regardless
  }
}
