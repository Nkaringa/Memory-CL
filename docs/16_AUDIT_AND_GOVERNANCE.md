# 16 · Audit + Governance

← back to [index](00_INDEX.md) · related: [17_SNAPSHOT_AND_REPLAY](17_SNAPSHOT_AND_REPLAY.md), [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md), [25_DESIGN_DECISIONS](25_DESIGN_DECISIONS.md)

Phase 8 ships **append-only, hash-chained, tamper-evident** audit
plus tenant + policy-based access control. Source:
`infra/audit/`, `core/governance/`.

## Hash-chained log

`infra/audit/immutable_log_store.py::ImmutableLogStore`:

- Each entry's `hash = SHA256(prev_hash || canonical_json(payload))`.
- `prev_hash` of entry 0 is the genesis (64 zeros).
- Re-walking the chain (`verify_chain`) raises `ChainBrokenError`
  at the **first** broken link — `seq`, expected, actual all reported.
- `to_jsonl()` for backup; `JsonlFileAuditSink.replay()` for restore.

Tampering with any entry's payload changes its hash, which breaks
the next entry's `prev_hash`, which propagates forward. Detection
is local: walk from genesis, compare hashes, stop on first miss.

## Audit logger

`core/governance/audit_logger.py::AuditLogger`:

```python
logger.record(
    actor=AuditActor.AGENT,            # user | agent | system
    action=AuditAction.RETRIEVE,        # ingest | retrieve | update | route | rank | policy_decide | quarantine | snapshot | replay
    entity_id="<unit_id-or-repo>",
    tenant_id="acme-corp",
    before=<state-or-None>,             # auto-hashed if not provided
    after=<state-or-None>,              # auto-hashed if not provided
    metadata={"...": "..."},
    timestamp=<datetime|None>,
    level="info",
)
```

Effects:

1. Append a hash-chained entry to the configured `ImmutableLogStore`.
2. Write the entry to the configured `AuditSink`.
3. Emit a structlog `audit_event` with the spec-mandated fields.

Spec'd `audit_event` payload:

```json
{
  "event": "audit_event",
  "phase": "phase_8",
  "actor": "user|agent|system",
  "action": "ingest|retrieve|update|route|rank|policy_decide|quarantine|snapshot|replay",
  "entity_id": "...",
  "before_hash": "...",
  "after_hash": "...",
  "tenant_id": "...",
  "timestamp": "<ISO-8601>",
  "metadata": { ... }
}
```

`hash_state()` is the deterministic hashing helper — bytes, str,
dicts, dataclasses all flow through one canonical-JSON path.

## Sinks

`infra/audit/audit_sink.py`:

- `InMemoryAuditSink` — tests + ephemeral processes.
- `JsonlFileAuditSink(path)` — append-only JSONL, durable.
  `JsonlFileAuditSink.replay()` rebuilds an `ImmutableLogStore`
  from the file (callers verify the chain after).

A logger always attaches both: store (for fast read + verify) +
sink (for durability).

## Tenant manager

`core/governance/tenant_manager.py::TenantManager`:

- Tenants are first-class. Repos belong to one tenant.
- `assign_repo(tenant_id, repo_id)` — duplicate ownership raises
  `CrossTenantAccessError`.
- `assert_owns_repo(tenant_id, repo_id)` — gate used by access control.

`Tenant` carries `tenant_id`, `name`, `max_repos`,
`max_ingestion_bytes_per_day`. The latter two are budget intents —
the policy engine enforces them.

## Policy engine

`core/governance/policy_engine.py::PolicyEngine`:

- Policies are deterministic predicates: `(context: dict) -> PolicyEffect`.
- `PolicyEffect`: `ALLOW | DENY | NEUTRAL` (NEUTRAL = "skip me").
- Evaluation walks policies in `(priority, name)` order, returning
  the first non-NEUTRAL effect. Default = `ALLOW` (fails-open).
- `PolicyDecision` carries `effect`, `matched_policy`, `reason`,
  `trace`.

Built-in policy factories:

- `deny_external_retrieval()` — reject retrievals targeting EXTERNAL nodes.
- `restrict_mcp_tool_by_role(allowed)` — role → allowed tool set,
  with `*` wildcard.
- `limit_ingestion_size(max_bytes)`
- `enforce_retention(max_age_days)`

## Access control

`core/governance/access_control.py::AccessControl`:

`check(AccessRequest) → AccessDecision`:

1. Resolve tenant ↔ repo ownership (raises → DENY decision).
2. Evaluate the policy engine with the request context.
3. Emit a `policy_decide` audit event (level INFO if allowed,
   WARNING if denied).

The `AccessControl` instance composes a `TenantManager` + a
`PolicyEngine` + an optional `AuditLogger`. All four pieces are
constructor-injected — easy to test in isolation.

## How it ties together

```
HTTP request
  ↓
AccessControl.check(...)
  ├─ TenantManager.assert_owns_repo
  └─ PolicyEngine.evaluate
  ↓
AuditLogger.record(action=POLICY_DECIDE, ...)  ← chain link
  ↓
HTTP handler proceeds OR returns 403
```

Every governance decision leaves a chain link **before** the action
takes effect (or fails). An auditor can replay the chain and verify
that every action had a preceding decision.

## Verification

`GET /audit/verify` re-walks the chain and reports `intact: true|false`.
`Phase-8 CorruptionDetector` aggregates this with checksum + graph +
schema integrity into a single report. See
[24_TROUBLESHOOTING](24_TROUBLESHOOTING.md).

## Determinism

- Audit payloads are canonical JSON (sorted keys).
- `prev_hash || canonical_json(payload)` deterministically produces
  the chain hash.
- Two equal payloads with different insertion orders produce the
  SAME hash (asserted by
  `test_canonical_payload_invariant_under_key_reorder`).

---

Next: [17 — Snapshot + Replay](17_SNAPSHOT_AND_REPLAY.md)
