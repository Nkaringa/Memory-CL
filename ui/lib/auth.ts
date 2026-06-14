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

import type { MeResponse, ProviderAdmin, ProviderPublic } from "@/lib/types";

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

// ---- OAuth / Identity provider helpers ------------------------------------

/** GET /auth/providers — public list of enabled providers for the login page.
 *  Never throws; returns [] on any error so the login page always renders. */
export async function fetchProviders(): Promise<ProviderPublic[]> {
  try {
    const resp = await authFetch("/auth/providers");
    if (!resp.ok) return [];
    const body = (await resp.json()) as { providers: ProviderPublic[] };
    return body.providers ?? [];
  } catch {
    return [];
  }
}

/** GET /config/auth/providers — admin list (Settings → Identity panel). */
export async function listProviderConfigs(): Promise<ProviderAdmin[]> {
  const resp = await authFetch("/config/auth/providers");
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail ?? "Could not load providers";
    throw new Error(String(detail));
  }
  const body = (await resp.json()) as { providers: ProviderAdmin[] };
  return body.providers ?? [];
}

export interface CreateProviderBody {
  provider_type: string;
  display_name: string;
  client_id: string;
  client_secret: string;
  discovery_url?: string;
  scopes?: string;
}

/** POST /config/auth/providers — create a new provider config. */
export async function createProvider(body: CreateProviderBody): Promise<ProviderAdmin> {
  const resp = await authFetch("/config/auth/providers", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = (err as { detail?: string }).detail ?? "Could not create provider";
    throw new Error(String(detail));
  }
  return (await resp.json()) as ProviderAdmin;
}

export interface UpdateProviderBody {
  display_name?: string;
  client_id?: string;
  client_secret?: string;
  discovery_url?: string;
  scopes?: string;
}

/** PATCH /config/auth/providers/{id} — update an existing provider. */
export async function updateProvider(id: string, body: UpdateProviderBody): Promise<void> {
  const resp = await authFetch(`/config/auth/providers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = (err as { detail?: string }).detail ?? "Could not update provider";
    throw new Error(String(detail));
  }
}

/** POST /config/auth/providers/{id}/enable — enable or disable a provider. */
export async function setProviderEnabled(id: string, enabled: boolean): Promise<void> {
  const resp = await authFetch(`/config/auth/providers/${encodeURIComponent(id)}/enable`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = (err as { detail?: string }).detail ?? "Could not update provider";
    throw new Error(String(detail));
  }
}

/** DELETE /config/auth/providers/{id} — remove a provider. */
export async function deleteProvider(id: string): Promise<void> {
  const resp = await authFetch(`/config/auth/providers/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = (err as { detail?: string }).detail ?? "Could not delete provider";
    throw new Error(String(detail));
  }
}
