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
export interface McpToolEntry {
  name: string;
  request_schema: string;
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

// ---- Generic API error ----------------------------------------------------
export interface ApiErrorBody {
  detail?: string | Record<string, unknown>;
  [key: string]: unknown;
}
