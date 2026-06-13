/* AsyncMemoryClient — TypeScript SDK wrapper.
 *
 * SINGLE source of truth for backend access. Every UI component
 * that needs data goes through here; no `fetch()` calls anywhere
 * else in the codebase. Enforced by convention (no other module
 * imports `fetch` directly) — a simple grep keeps this honest.
 */

import type {
  AppConfig,
  AuditTailResponse,
  AuditVerifyResponse,
  EmbeddingMode,
  EmbeddingModeResult,
  McpKeyResponse,
  IngestRequest,
  IngestResponse,
  McpToolList,
  McpToolResponse,
  QnamesResponse,
  ReadinessResponse,
  RepoGraphResponse,
  ReplayResponse,
  ReposResponse,
  RetrieveRequest,
  RetrieveResponse,
  SnapshotResponse,
  StatusResponse,
} from "@/lib/types";

export class MemoryClientError extends Error {
  public readonly status: number;
  public readonly url: string;
  public readonly body: unknown;

  constructor(opts: { status: number; url: string; body: unknown }) {
    super(`HTTP ${opts.status} from ${opts.url}`);
    this.status = opts.status;
    this.url = opts.url;
    this.body = opts.body;
    this.name = "MemoryClientError";
  }
}

export interface ClientOptions {
  /** Default points at the Next dev server's /api rewrite. */
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  /**
   * Phase-10 correlation id propagation. Provide a function that
   * returns the X-Request-ID for each request — the UI uses this to
   * tag every API call with a per-page-load uuid so server logs and
   * traces can be reconciled with browser actions.
   *
   * Defaults to a fresh uuid per call when crypto.randomUUID is
   * available; otherwise falls back to no header (the API generates
   * one server-side). */
  requestId?: () => string;
}

export class AsyncMemoryClient {
  private readonly baseUrl: string;
  private readonly apiKey: string | undefined;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly requestId: (() => string) | undefined;

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "/api").replace(/\/$/, "");
    this.apiKey = opts.apiKey;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
    this.fetchImpl = opts.fetchImpl ?? fetch.bind(globalThis);
    this.requestId = opts.requestId ?? defaultRequestId;
  }

  // ------ repos ------------------------------------------------------------
  listRepos(): Promise<ReposResponse> {
    return this.get<ReposResponse>("/repos");
  }

  searchQnames(repoId: string, q: string, limit = 20): Promise<QnamesResponse> {
    const params = new URLSearchParams({ q, limit: String(limit) });
    return this.get<QnamesResponse>(
      `/repos/${encodeURIComponent(repoId)}/qnames?${params.toString()}`,
    );
  }

  /** Whole-repo graph (backend ≥ 4f06ac6). External nodes are excluded
   *  by default server-side; pass includeExternal to opt in. */
  getRepoGraph(
    repoId: string,
    opts: { includeExternal?: boolean; maxNodes?: number } = {},
  ): Promise<RepoGraphResponse> {
    const params = new URLSearchParams();
    if (opts.includeExternal !== undefined) {
      params.set("include_external", String(opts.includeExternal));
    }
    if (opts.maxNodes !== undefined) {
      params.set("max_nodes", String(opts.maxNodes));
    }
    const qs = params.toString();
    return this.get<RepoGraphResponse>(
      `/repos/${encodeURIComponent(repoId)}/graph${qs ? `?${qs}` : ""}`,
    );
  }

  // ------ status -----------------------------------------------------------
  status(): Promise<StatusResponse> {
    return this.get<StatusResponse>("/status");
  }

  health(): Promise<ReadinessResponse> {
    return this.get<ReadinessResponse>("/health/ready");
  }

  // ------ retrieval --------------------------------------------------------
  retrieve(req: RetrieveRequest): Promise<RetrieveResponse> {
    return this.post<RetrieveResponse>("/retrieve", req);
  }

  // ------ ingestion --------------------------------------------------------
  ingest(req: IngestRequest): Promise<IngestResponse> {
    // Ingestion can take several minutes for large repos; use a generous
    // client-side timeout so we don't abort a legitimate long-running run.
    return this.post<IngestResponse>("/ingest", req, { timeoutMs: 600_000 });
  }

  // ------ MCP --------------------------------------------------------------
  listTools(): Promise<McpToolList> {
    return this.get<McpToolList>("/mcp/tools");
  }

  runTool<P extends Record<string, unknown>>(
    name: string,
    payload: P,
  ): Promise<McpToolResponse> {
    return this.post<McpToolResponse>(
      `/mcp/tools/${encodeURIComponent(name)}`,
      payload,
    );
  }

  // ------ snapshot + replay -----------------------------------------------
  buildSnapshot(opts: {
    tenant_id: string;
    state_version_token?: string;
  }): Promise<SnapshotResponse> {
    return this.post<SnapshotResponse>("/snapshot/build", {
      tenant_id: opts.tenant_id,
      state_version_token: opts.state_version_token ?? "v0",
    });
  }

  replay(opts: {
    snapshot_id: string;
    payload: unknown;
    expected_output?: unknown;
  }): Promise<ReplayResponse> {
    return this.post<ReplayResponse>("/snapshot/replay", opts);
  }

  // ------ audit ------------------------------------------------------------
  auditTail(limit = 50): Promise<AuditTailResponse> {
    return this.get<AuditTailResponse>(
      `/audit/tail?limit=${encodeURIComponent(String(limit))}`,
    );
  }

  auditVerify(): Promise<AuditVerifyResponse> {
    return this.get<AuditVerifyResponse>("/audit/verify");
  }

  // ------ config / onboarding ---------------------------------------------
  /** Onboarding + key state. Unauthenticated — the wizard needs it pre-key. */
  getConfig(): Promise<AppConfig> {
    return this.get<AppConfig>("/config");
  }

  /** Generate the first MCP key. The raw key is returned ONCE — copy it now. */
  generateMcpKey(): Promise<McpKeyResponse> {
    return this.post<McpKeyResponse>("/config/mcp-key/generate", {});
  }

  /** Rotate the MCP key (requires the current key). Agents must re-add. */
  rotateMcpKey(): Promise<McpKeyResponse> {
    return this.post<McpKeyResponse>("/config/mcp-key/rotate", {});
  }

  /** Set or clear (pass null) the OpenAI key. */
  setOpenAiKey(apiKey: string | null): Promise<unknown> {
    return this.post<unknown>("/config/openai-key", { api_key: apiKey });
  }

  /** Switch embedding mode. On an actual change the backend rebuilds every
   *  repo's collection at the new dimension and re-embeds — the returned
   *  counts report that re-index. */
  setEmbeddingMode(mode: EmbeddingMode): Promise<EmbeddingModeResult> {
    return this.post<EmbeddingModeResult>("/config/embedding-mode", { mode });
  }

  completeOnboarding(): Promise<unknown> {
    return this.post<unknown>("/config/complete-onboarding", {});
  }

  // ------ internal HTTP plumbing ------------------------------------------
  private get<T>(path: string, opts?: { timeoutMs?: number }): Promise<T> {
    return this.request<T>("GET", path, undefined, opts);
  }

  private post<T>(path: string, body: unknown, opts?: { timeoutMs?: number }): Promise<T> {
    return this.request<T>("POST", path, body, opts);
  }

  private async request<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
    opts?: { timeoutMs?: number },
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const effectiveTimeout = opts?.timeoutMs ?? this.timeoutMs;
    const timer = setTimeout(() => controller.abort(), effectiveTimeout);
    const headers: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json",
    };
    if (this.apiKey) headers["x-api-key"] = this.apiKey;
    // Phase-10: per-call correlation id. The API echoes it back so
    // dev-tools can match browser requests with backend traces.
    const rid = this.requestId?.();
    if (rid) headers["x-request-id"] = rid;

    let resp: Response;
    try {
      resp = await this.fetchImpl(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    let parsed: unknown;
    const text = await resp.text();
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = text;
    }

    if (!resp.ok) {
      throw new MemoryClientError({
        status: resp.status,
        url,
        body: parsed,
      });
    }
    return parsed as T;
  }
}

/** Phase-10 default X-Request-ID generator.
 *
 *  Uses crypto.randomUUID when present (modern browsers, Node 19+);
 *  falls back to a Math.random-derived id otherwise. Returning ""
 *  means "let the server generate one" — a header with an empty
 *  value would just be stripped, so we omit it instead. */
function defaultRequestId(): string {
  const g = globalThis as unknown as {
    crypto?: { randomUUID?: () => string };
  };
  if (g.crypto?.randomUUID) {
    return g.crypto.randomUUID();
  }
  // Last-resort fallback. Not RFC 4122 compliant but distinct enough
  // for log correlation in environments without crypto.randomUUID.
  const t = Date.now().toString(16);
  const r = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
  return `memcl-${t}-${r}`;
}

/** Process-wide singleton. Components import this directly so the
 * call site reads as plain function invocation while the underlying
 * client gets shared automatically. */
let _client: AsyncMemoryClient | null = null;
export function getMemoryClient(): AsyncMemoryClient {
  if (_client === null) {
    _client = new AsyncMemoryClient({
      // The Next.js rewrite in next.config.mjs proxies /api/* to the
      // backend, so the browser always talks same-origin.
      baseUrl: "/api",
    });
  }
  return _client;
}

/** Convenience wrapper consumed by RepoSelect and ToolRunner. */
export function listRepos(): Promise<ReposResponse> {
  return getMemoryClient().listRepos();
}

/** Convenience wrapper consumed by QnameInput. */
export function searchQnames(repoId: string, q: string): Promise<QnamesResponse> {
  return getMemoryClient().searchQnames(repoId, q);
}

/** Convenience wrapper consumed by the Graph page's whole-repo mode. */
export function getRepoGraph(
  repoId: string,
  opts: { includeExternal?: boolean; maxNodes?: number } = {},
): Promise<RepoGraphResponse> {
  return getMemoryClient().getRepoGraph(repoId, opts);
}
