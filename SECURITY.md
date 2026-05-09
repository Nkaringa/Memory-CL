# Security

Threat model and access-control posture for Memory-CL.

## Cross-references

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — system topology
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — deploy posture, secret handling
- **[RUNBOOK.md](RUNBOOK.md)** — incident response procedures
- **[docs/22_SECURITY_AND_ACCESS_CONTROL.md](docs/22_SECURITY_AND_ACCESS_CONTROL.md)** — security narrative

---

## 1. Threat model

We assume a deployment in your trust boundary (your VPC, your
cluster). The exposed surface is the API + UI on TCP/8000 and
TCP/3000. Threats considered:

| Threat | Mitigation |
|---|---|
| **Unauthenticated MCP execution** | `MCP_API_KEY` required in staging/prod; missing header → 401 |
| **Tampered audit log** | Hash-chained JSONL, `/audit/verify` walks every prev_hash |
| **Container compromise** | Non-root uid 1000; read-only fs (operator-controlled); no shell in runtime |
| **Secret exfiltration via image** | `.dockerignore` excludes `.env*`; `Settings` validates secrets at boot, never logs them |
| **Supply-chain (transitive deps)** | `requirements.lock.txt` pins direct deps; `pip install --no-deps --no-index` rejects network-resolved fallbacks |
| **State drift / regression** | Snapshot + replay; deterministic operations always reproducible against a snapshot |
| **Multi-tenant cross-talk** | `tenant_id` propagated through every layer; storage clients namespaced per tenant |
| **OOM / DoS via unbounded payloads** | Pydantic schemas with `extra="forbid"` + length bounds (e.g. `tenant_id: max_length=128`) |
| **Sentinel passwords in production** | `Settings._enforce_environment_contract` rejects known dev sentinels at boot |

What we do **not** address at this layer (responsibility of your
infrastructure):

- Network ingress firewalling
- TLS termination (run a reverse proxy in front)
- Identity-aware access (SSO/OIDC) — bring your own gateway
- Disk-level encryption for the storage volumes

---

## 2. Access model

### 2.1 Surfaces

| Surface | Auth | Purpose |
|---|---|---|
| `GET /health/*` | None | Orchestrator probes (must be unauthenticated) |
| `GET /status` | None | Operator + dashboard read |
| `POST /retrieve` | None by default; reverse-proxy your call if needed | Hot path; latency-sensitive |
| `POST /ingest` | None by default | Mutating; consider gating at the proxy |
| `POST /mcp/tools/*` | `X-API-Key: $MCP_API_KEY` if set | REST agent surface |
| `GET /mcp/tools` | None | Read-only registry list |
| `* /mcp/sse`, `* /mcp/http` | `X-API-Key: $MCP_API_KEY` or `Authorization: Bearer <key>` if set | Native MCP transports (SSE + streamable HTTP) — same auth rule as REST |
| `POST /snapshot/*` | None by default | Governance; consider gating |
| `GET /audit/*` | None by default | Governance read; consider gating |

`MCP_API_KEY` is the only built-in auth mechanism. It's a single
shared secret that gates the MCP execution surface. For per-tenant
or per-agent identity, run a gateway in front of the API.

### 2.2 Defaults by environment

| Variable | dev | staging | prod |
|---|---|---|---|
| `MCP_API_KEY` | optional (no auth in dev) | required | required |
| `STRICT_BOOTSTRAP` | false | true | true (enforced) |
| `SAFE_MODE_ENABLED` | false | false | false |

In production, missing `MCP_API_KEY` causes `Settings` to raise
`StrictConfigError` at boot — the container exits before binding
the port. There is no path that ships an authenticated surface
unauthenticated.

---

## 3. Secret handling

### 3.1 Where secrets live

- **Image** — never. `.dockerignore` excludes every `.env*` except
  `.env.example` (which contains only template keys).
- **Build args** — never. The Dockerfile takes no `ARG` for secrets.
- **Runtime env** — yes, injected by the orchestrator. Read by
  `Settings` once at startup; held as `pydantic.SecretStr` thereafter
  so accidental `repr` / `str` does not leak.
- **Logs** — never. `SecretStr` does not render its value in
  structlog events.

### 3.2 Pre-flight enforcement

[`core/config.py`](core/config.py) applies a `model_validator(mode="after")`
that rejects:

- `NEO4J_PASSWORD` set to any of `_INSECURE_SENTINELS` in staging/prod
- `MCP_API_KEY` empty or set to a sentinel in staging/prod
- Empty storage URLs in staging/prod
- `LOG_FORMAT != "json"` in production
- `STRICT_BOOTSTRAP != true` in production
- `OTEL_ENABLED != true` in production

The error message lists every offender in one shot, so an operator
can fix the entire `.env.production` in a single pass.

### 3.3 Rotation

| Secret | How to rotate |
|---|---|
| `MCP_API_KEY` | Update in your secret manager; rolling restart picks it up |
| `NEO4J_PASSWORD` | Rotate in Neo4j first, then update env, restart |
| `POSTGRES_PASSWORD` | Same — rotate in Postgres first |

`docker-compose.production.yml`'s `deploy.update_config` ensures one
replica is healthy at all times during a rolling restart.

---

## 4. Container hardening

The production image follows the OWASP container baseline:

- **Multi-stage build** — compilers and build deps stay in the
  builder stage; the runtime image carries only the Python
  interpreter and pre-built wheels.
- **Non-root** — `useradd --uid 1000 memcl`, `USER memcl` before
  ENTRYPOINT. Recommend `read_only: true` and tmpfs mounts in
  production compose.
- **No shell utilities** — no `curl`, `wget`, `bash`. Health probe
  uses Python stdlib (`urllib.request`).
- **Pinned base** — `python:3.12.7-slim-bookworm`. (Pin a digest in
  your registry policy for stronger guarantees.)
- **Init system** — `tini` runs as PID 1 to reap orphans cleanly.
- **Lockfile-strict deps** — `pip install --no-deps --no-index`.

The UI image follows the same shape: standalone Next.js output,
non-root uid 1000, no node_modules in the runtime stage.

---

## 5. Audit posture

The audit chain is the system's tamper-evident ledger.

- Every governance / mutation event becomes one JSONL line in the
  durable sink, with `prev_hash` linking back to the previous entry.
- `/audit/verify` walks every prev_hash from genesis to head; any
  break flips the result to `{"intact": false, "broken_at_seq": N}`.
- The `/health/dependencies` endpoint surfaces audit chain integrity
  as a **non-required** check — chain drift never gates user
  traffic, but operators see it immediately.
- Snapshot + replay verify deterministic outputs against a known-
  good snapshot. A drift between expected and actual hashes is
  evidence; an operator decides whether the cause is legitimate
  state advancement or genuine corruption.

Production should replicate the JSONL sink to immutable storage
(S3 Object Lock, GCS bucket lock, or equivalent) so even root on
the API host cannot alter history.

---

## 6. Incident response

If you suspect compromise:

1. **Engage `mcp_disabled` safe-mode** — refuses MCP tool execution
   while keeping reads online.
2. **Snapshot the audit JSONL** — copy to a host the suspected
   attacker cannot reach.
3. **Verify the chain** — `curl http://api/audit/verify` from a
   trusted host. If broken, the `broken_at_seq` is the first link
   altered.
4. **Rotate `MCP_API_KEY`** — revoke the old key in your secret
   manager. Rolling restart picks up the new value.
5. **Inspect `/audit/tail` for anomalous actions** — any tool
   invocation against an unexpected `entity_id` or by an unexpected
   `actor` is a starting point.
6. **Engage `retrieval_only` mode if needed** — leaves the smallest
   surface online while you investigate.
7. **Restore from a known-good snapshot** — see RUNBOOK §3 backups.

---

## 7. Disclosure

Security issues should be reported through your organization's
internal vulnerability process. Public disclosure of a vulnerability
in a deployed Memory-CL instance — including stack traces from
unauthenticated probes — should be coordinated with the service
owner before any external publication.

---

## 8. Hardening checklist

- [ ] `MCP_API_KEY` set, non-sentinel, ≥32 entropy bits
- [ ] `NEO4J_PASSWORD` set, non-sentinel, rotated since last operator change
- [ ] Reverse proxy in front (TLS termination, WAF, rate limit)
- [ ] Container runs `read_only: true` with explicit tmpfs mounts
- [ ] Container has `cap_drop: [ALL]` and only the caps it actually needs
- [ ] Audit JSONL sink replicated to immutable storage (Object Lock)
- [ ] Storage backups encrypted at rest
- [ ] Network policy restricts egress (DB hosts only)
- [ ] OTEL collector is the only allowed observability egress
- [ ] No interactive shell available in the runtime image
- [ ] CI rebuilds `requirements.lock.txt` with `--generate-hashes`
- [ ] CVE scanner runs against `memory-cl:prod` on every rebuild
