# Runbook

Incident response procedures for Memory-CL. Each scenario lists the
**signal**, the **diagnostic command**, and the **resolution path**.
Bias toward "stop the bleeding, then debug" — degraded modes exist
specifically so you can keep serving traffic while you investigate.

## Cross-references

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — system topology
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — environment contract
- **[SECURITY.md](SECURITY.md)** — incident-response auth flow
- **[docs/24_TROUBLESHOOTING.md](docs/24_TROUBLESHOOTING.md)** — diagnostic appendix

---

## 1. Triage

For any incident, start here:

```bash
# 1. Liveness — is the process up at all?
curl -fsS http://api/health/live

# 2. Readiness — are storage backends + MCP registry healthy?
curl -fsS http://api/health/ready | jq '{status, components}'

# 3. Deep dependencies — what's actually broken?
curl -fsS http://api/health/dependencies | jq '.checks[] | select(.status != "ok")'

# 4. Posture — is safe-mode flipped? Boot stages clean?
curl -fsS http://api/status | jq '{
    safe_mode, environment,
    boot_overall_ok, boot_failed_stages, boot_degraded_stages
}'
```

The four-pillar `/status` response is the single screen most useful
during triage. Drive everything below from there.

---

## 2. Scenarios

### 2.1 Liveness fails

**Signal**: `/health/live` returns non-200 or times out.

**Most likely cause**: Process is wedged or crashed.

**Resolution**:
1. `docker compose ps` — confirm the API container is running
2. `docker logs memory-cl-api --tail 200` — look for unhandled
   exceptions, OOM kills, or `StrictConfigError` lines
3. If the container is repeatedly crash-looping, check
   `.env.production` for missing or sentinel values — see
   [DEPLOYMENT.md §2](DEPLOYMENT.md#2-environment-contract)
4. Restart: `docker compose -f docker-compose.production.yml restart api`

---

### 2.2 Readiness fails (`/health/ready` returns 503)

**Signal**: Liveness OK, readiness 503.

The component list pinpoints which backend is down:

```bash
curl -fsS http://api/health/ready | jq '.components[] | select(.status != "ok")'
```

| Component | Likely cause | Action |
|---|---|---|
| `postgres` | Connection refused / auth fail | Check `POSTGRES_URL`, `POSTGRES_PASSWORD`; `docker logs memory-cl-postgres-prod` |
| `qdrant` | Collection missing / disk full | Check `QDRANT_URL`; `docker exec memory-cl-qdrant-prod df -h` |
| `neo4j` | Auth failure / heap OOM | Check `NEO4J_PASSWORD`; review Neo4j heap settings |
| `redis` | OOM-killed / network partition | Check `REDIS_URL`; `redis-cli info memory` |
| `mcp_registry` | Process started before registry built | Check API logs for `mcp_registry` boot stage failure; restart API |

If two or more storage backends are down simultaneously, suspect a
network/DNS issue at the orchestrator level rather than a single
backend.

---

### 2.3 Boot stage failed

**Signal**: `/status` shows `boot_overall_ok=false`, one or more
stages in `boot_failed_stages`.

The 8 stages run **deterministically** in
[`apps/api/bootstrap.py`](apps/api/bootstrap.py). Stage names:

```
storage_init → schema_validation → graph_vector_validation →
ingestion_readiness → retrieval_warmup → mcp_registry →
audit_chain → api_exposure
```

If `STRICT_BOOTSTRAP=true` and any required stage fails, the
SafeModeController auto-enables `read_only` mode. The API stays up;
mutating writes (`POST /ingest`, `POST /snapshot/build`) return 503.
Reads, retrieval, and the MCP read-only tools continue serving.

**Action**:
1. Read the per-stage error in `/status` → `boot_stages[].error`
2. Fix the underlying issue
3. Restart the API container — boot re-runs from scratch
4. Optionally `POST /ops/safe-mode/disable` once you're confident

---

### 2.4 Safe-mode is engaged

**Signal**: `/status` shows `safe_mode.enabled=true`.

The `mode` field tells you which surfaces are gated:

| `mode` | What's blocked | What still works |
|---|---|---|
| `read_only` | All mutating writes | Reads, retrieval, MCP read-only tools |
| `mcp_disabled` | All MCP tool execution | HTTP ingest, retrieve, status |
| `retrieval_only` | Everything except retrieval | `/retrieve`, `/health/*` |
| `off` | (not in safe mode) | Everything |

`triggered_by` distinguishes the cause:

- `config` — operator set `SAFE_MODE_ENABLED=true` explicitly
- `boot_failure` — health gate auto-flipped after a failed stage
- `runtime_health` — runtime degradation tripped the gate (reserved)
- `manual` — explicit `disable()` call (always shown after exit)

**Action**:
1. Determine why safe-mode engaged (read `safe_mode.reason`)
2. Resolve the underlying cause (see §2.2 / §2.3)
3. Disable safe-mode: API call from a privileged client, or restart
   the container with `SAFE_MODE_ENABLED=false` and a clean boot

---

### 2.5 Audit chain reports broken

**Signal**: `/health/dependencies` shows `audit_chain` with
`status=degraded` and `error="chain verification reported drift"`,
or `/audit/verify` returns `{"intact": false, "broken_at_seq": N}`.

This is **not** a 503 condition — the audit chain is a governance
check, not a serving gate. The API keeps working. But replay
guarantees are degraded until the chain is rebuilt.

**Action**:
1. `curl -fsS http://api/audit/verify | jq` — note `broken_at_seq`
2. Inspect the durable JSONL sink (`storage/governance/audit.jsonl`
   or your configured path) at that sequence number
3. If tampering is suspected, escalate to security
   ([SECURITY.md §6](SECURITY.md#6-incident-response))
4. Rebuild the chain from the durable sink (procedure in
   [`docs/16_AUDIT_AND_GOVERNANCE.md`](docs/16_AUDIT_AND_GOVERNANCE.md))

---

### 2.6 Latency / throughput regression

**Signal**: P99 latency on `/retrieve` jumps; throughput drops.

The built-in trackers (`LatencyTracker`, `ThroughputAnalyzer`) emit
metrics via OTLP. Drive triage from your dashboard, then:

```bash
# Inspect cache hit rate and rate-limiter pressure:
curl -fsS http://api/status | jq '.feature_flags'
```

| Symptom | Likely cause | Action |
|---|---|---|
| Latency up, cache hit rate down | Cache too small or TTL too short | Increase `SCALE_RETRIEVAL_CACHE_SIZE` |
| Latency up, vector_hits up | Embedding index churn | Inspect Qdrant compaction state |
| Throughput drops, no error | Rate limiter throttling | Inspect `SCALE_DEFAULT_RATE_PER_SECOND` |
| Throughput drops, errors up | Backpressure trip | Check `SCALE_BACKPRESSURE_THRESHOLD`, queue depth |

---

### 2.7 MCP tool returning 503

**Signal**: any of the three MCP surfaces is returning 503 / failing
to handshake:

- `POST /mcp/tools/<name>` (REST surface — used by SDK + CLI)
- `GET /mcp/sse`, `POST /mcp/sse/messages/` (SSE transport)
- `* /mcp/http` (streamable HTTP transport)

**Most likely**: safe-mode is in `mcp_disabled` or `retrieval_only`.

Check `/status` for `safe_mode.mode`. If you intended to disable MCP
(security incident), confirm the disable was deliberate. Otherwise
follow §2.4.

If only the **native** transports (`/mcp/sse`, `/mcp/http`) are
broken while the REST surface is fine, look for
`native_mcp_attach_failed` in the API startup log — usually a
missing `mcp` SDK in the deployed image (rebuild) or a transport
session-manager exception. REST keeps serving in this case; the
[stdio bridge](docs/MCP_BRIDGE.md) is your fallback for clients
that need MCP-protocol semantics.

---

## 3. Backups

Memory-CL is a **read amplifier** over your durable stores. The
authoritative state lives in:

| Store | What's there | Backup cadence |
|---|---|---|
| Postgres | Ingestion units, governance metadata | Continuous WAL + nightly snapshot |
| Neo4j | Graph (nodes + edges) | Nightly `neo4j-admin database dump` |
| Qdrant | Vector index | Nightly snapshot via Qdrant `/collections/<c>/snapshots` |
| Redis | Cache + version tokens — **ephemeral** | None required |
| Audit JSONL sink | Tamper-evident hash chain | Append-only, replicated to immutable storage (S3 Object Lock or equivalent) |

The audit JSONL sink **must** survive any other failure; treat it
like a financial ledger. Restore order during DR:

1. Restore audit JSONL sink first (verify chain on restore)
2. Restore Postgres + Neo4j + Qdrant in parallel
3. Bring Redis up empty (rebuilds via cache misses)
4. Boot Memory-CL — `/status` reports clean boot

---

## 4. On-call escalation

| Severity | Trigger | Escalate to |
|---|---|---|
| SEV-1 | Liveness failing on every replica | Platform on-call + service owner |
| SEV-2 | Readiness failing on majority of replicas | Service owner |
| SEV-3 | One replica unhealthy / safe-mode engaged | Next-business-day |
| SEV-4 | Audit chain drift / governance signal | Security on-call |

---

## 5. Recovery primitives

```bash
# Force a clean boot (re-runs the 8-stage health gate):
docker compose -f docker-compose.production.yml restart api

# Disable safe-mode without a restart (requires API key):
curl -X POST -H "X-API-Key: $MCP_API_KEY" http://api/ops/safe-mode/disable

# Drain a replica gracefully (rolling update will replace it):
docker compose -f docker-compose.production.yml stop api
# wait for the second replica to take traffic, then start the original

# Rebuild the lockfile (only on a controlled, audited host):
pip-compile --generate-hashes --output-file=requirements.lock.txt pyproject.toml
git diff requirements.lock.txt   # review every transitive change
```

---

## 6. Common operator mistakes

- **Editing a sentinel password in place** — `.env.production` is a
  template. Set the value via your secret manager, never commit.
- **Disabling `STRICT_BOOTSTRAP` to "get past" a boot error** — this
  silences the very signal that's telling you the deploy is wrong.
- **Tampering with the audit JSONL sink** — even fixing a "stuck"
  entry breaks the chain. The chain is the audit; the audit is the
  ledger; the ledger is the contract.
- **Restarting Postgres/Neo4j without a clean shutdown** — both can
  end up needing manual recovery; the API will safe-mode-flip but
  recovery on the storage side is yours.
