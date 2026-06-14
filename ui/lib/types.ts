/* TypeScript mirrors of the backend Pydantic shapes.
 *
 * These are READ models — UI components consume them, never mutate
 * them. Field names match the backend so a reader of either side can
 * trace request → wire → render without translation tables.
 */

// ---- Health / status ------------------------------------------------------
export type SystemStatusValue = "ok" | "degraded" | "failed";

export interface ComponentHealth {
  name: string;
  status: SystemStatusValue;
  latency_ms: number | null;
  error: string | null;
}

export interface ReadinessResponse {
  schema_version: string;
  status: SystemStatusValue;
  components: ComponentHealth[];
}

export interface SafeModeView {
  enabled: boolean;
  reason: string;
  triggered_by: string;
}

export interface FeatureFlagView {
  name: string;
  description: string;
  enabled: boolean;
}

export interface BootStageView {
  name: string;
  order: number;
  status: "ok" | "degraded" | "failed";
  error: string;
}

export interface FeatureWeightsView {
  semantic: number;
  graph: number;
  recency: number;
  importance: number;
  feedback: number;
}

export interface StatusResponse {
  service: string;
  environment: string;
  safe_mode: SafeModeView;
  feature_flags: FeatureFlagView[];
  boot_overall_ok: boolean;
  boot_failed_stages: string[];
  boot_degraded_stages: string[];
  boot_stages: BootStageView[];
  mcp_tool_count: number;
  schema_version: string;
  embeddings_enabled?: boolean;
  /** Served by newer backends; older ones omit it (UI falls back to
   *  the pinned Phase-4 constants). */
  feature_weights?: FeatureWeightsView;
}

// ---- Retrieval ------------------------------------------------------------
export interface RankingFeatures {
  semantic_similarity: number;
  graph_proximity: number;
  recency: number;
  importance: number;
  user_feedback: number;
}

export type RetrievalChannel = "graph" | "vector" | "metadata";

export interface ContextEntry {
  id: string;
  type: "constraint" | "risk" | "architecture" | "logic" | "code";
  score: number;
  data: Record<string, unknown>;
}

export interface ContextPacket {
  schema_version: string;
  task: string;
  context: ContextEntry[];
  risks: string[];
  constraints: string[];
  changes: string[];
  confidence: number;
}

export interface RetrieveRequest {
  text: string;
  repo_id: string;
  top_k?: number;
  unit_kinds?: string[];
  seed_unit_ids?: string[];
}

export interface RetrieveResponse {
  query_id: string;
  repo_id: string;
  packet: ContextPacket;
  graph_hits: number;
  vector_hits: number;
  metadata_hits: number;
  final_candidates: number;
  ranked_count: number;
  failed_channels: string[];
  latency_ms: number;
}

/** explore() direction enum — mirrors the backend ExploreDirection literal. */
export type ExploreDirection =
  | "callers"
  | "callees"
  | "imports"
  | "imported_by"
  | "inherits"
  | "all";

// ---- Graph (query_graph tool) ---------------------------------------------
export interface GraphQueryCandidate {
  unit_id: string;
  qualified_name: string | null;
  kind: string | null;
  file_path: string | null;
  raw_score: number;
  channel: string;
  depth: number | null;
}

/** Real directed edge among returned candidates (backend ≥ ff56ac0). */
export interface GraphQueryEdge {
  src_id: string;
  kind: string;
  dst_id: string;
}

// ---- Whole-repo graph (GET /repos/{repo_id}/graph, backend ≥ 4f06ac6) ------
export interface RepoGraphNode {
  node_id: string;
  kind: string;
  qualified_name: string;
  name: string;
  file_path: string | null;
  line_start: number | null;
  line_end: number | null;
}

export interface RepoGraphEdge {
  src_id: string;
  kind: string;
  dst_id: string;
}

export interface RepoGraphResponse {
  repo_id: string;
  /** True when the backend hit max_nodes and dropped the remainder. */
  truncated: boolean;
  nodes: RepoGraphNode[];
  edges: RepoGraphEdge[];
  counts: Record<string, number>;
}

// ---- Ingestion ------------------------------------------------------------
export interface IngestRequest {
  repo_id: string;
  repo_path: string;
  commit_sha: string;
}

export interface IngestResponse {
  repo_id: string;
  commit_sha: string;
  units_collection: string;
  metrics: Record<string, number>;
  failed_files: string[];
}

// ---- MCP ------------------------------------------------------------------

/** One property inside a tool's JSON Schema (pydantic model_json_schema). */
export interface ToolSchemaProperty {
  type?: string;
  description?: string;
  default?: unknown;
  /** pydantic emits `str | None` etc. as anyOf variants. */
  anyOf?: Array<{ type?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

/** JSON Schema for a tool's request model, served under `schema`. */
export interface ToolJsonSchema {
  title?: string;
  type?: string;
  properties?: Record<string, ToolSchemaProperty>;
  required?: string[];
  [key: string]: unknown;
}

export interface McpToolEntry {
  name: string;
  request_schema: string;
  /** Full request JSON Schema; optional so older backends still parse. */
  schema?: ToolJsonSchema;
}

export interface McpToolList {
  tools: McpToolEntry[];
}

export interface McpToolResponse {
  schema_version: string;
  tool: string;
  request_id: string;
  status: "success" | "failed";
  data: Record<string, unknown>;
  error: string | null;
  error_code:
    | "validation_error"
    | "unauthorized"
    | "unknown_tool"
    | "backend_error"
    | "internal_error"
    | null;
  latency_ms: number;
}

// ---- Snapshot + Replay ----------------------------------------------------
export interface SnapshotComponents {
  graph_state_hash: string;
  embedding_index_hash: string;
  retrieval_config_hash: string;
  schema_version: string;
  mcp_registry_hash: string;
  state_version_token: string;
}

export interface SnapshotResponse {
  snapshot_id: string;
  tenant_id: string;
  captured_at: string;
  components: SnapshotComponents;
}

export interface ReplayResponse {
  snapshot_id: string;
  matches: boolean;
  expected_hash: string;
  actual_hash: string;
  notes: string;
}

// ---- Audit ----------------------------------------------------------------
export interface AuditEntryView {
  seq: number;
  prev_hash: string;
  hash: string;
  payload: Record<string, unknown> & {
    event?: string;
    phase?: string;
    actor?: string;
    action?: string;
    entity_id?: string;
    tenant_id?: string;
    timestamp?: string;
    before_hash?: string;
    after_hash?: string;
  };
}

export interface AuditTailResponse {
  chain_length: number;
  entries: AuditEntryView[];
}

export interface AuditVerifyResponse {
  chain_length: number;
  intact: boolean;
  error: string;
  broken_at_seq: number | null;
}

// ---- Repos ----------------------------------------------------------------
export interface RepoInfo {
  repo_id: string;
  units: number;
  files: number;
  languages: string[];
}

export interface ReposResponse {
  schema_version: string;
  repos: RepoInfo[];
}

export interface QnameMatch {
  qualified_name: string;
  kind: string;
}

export interface QnamesResponse {
  repo_id: string;
  matches: QnameMatch[];
}

// ---- Config / onboarding --------------------------------------------------
export type EmbeddingMode = "openai" | "local";

/** Onboarding + key state served by `GET /config`. NEVER carries raw keys —
 *  the MCP key is exposed only as a masked hint, and the OpenAI key only as a
 *  set/not-set boolean. */
export interface AppConfig {
  configured: boolean;
  onboarding_completed: boolean;
  embedding_mode: EmbeddingMode;
  embeddings_enabled: boolean;
  has_openai_key: boolean;
  has_webhook_secret: boolean;
  /** Masked tail of the configured MCP key (e.g. "••••abcd"), or null. */
  mcp_key_hint: string | null;
}

/** Result of POST /config/embedding-mode. When the mode actually changed,
 *  every repo's vector collection is rebuilt at the new dimension and its
 *  units are re-embedded — these counts report that work. */
export interface EmbeddingModeResult {
  ok: boolean;
  mode: EmbeddingMode;
  reindexed: boolean;
  repos_reindexed: number;
  units_embedded: number;
  failed_batches: number;
}

// ---- Freshness (Phase 3) --------------------------------------------------
export interface FreshnessRepo {
  repo_id: string;
  source_type: "local" | "managed";
  repo_path: string;
  remote_url: string | null;
  branch: string | null;
  last_commit_sha: string | null;
  watch_enabled: boolean;
  last_synced_at: string | null;
  last_change_at: string | null;
  last_error: string | null;
}

export interface FreshnessList {
  freshness_enabled: boolean;
  repos: FreshnessRepo[];
}

export interface AddManagedResult {
  repo_id: string;
  commit_sha: string | null;
}

export interface SyncResult {
  repo_id: string;
  changed: boolean;
  new_sha: string | null;
  error: string | null;
}

/** One-time webhook secret reveal from generate. */
export interface WebhookSecretResult {
  secret: string;
}

/** One-time key reveal from generate / rotate. */
export interface McpKeyResponse {
  api_key: string;
}

// ---- Generic API error ----------------------------------------------------
export interface ApiErrorBody {
  detail?: string | Record<string, unknown>;
  [key: string]: unknown;
}
