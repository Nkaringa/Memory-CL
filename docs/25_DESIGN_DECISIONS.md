# 25 · Design Decisions

← back to [index](00_INDEX.md) · related: [01_OVERVIEW](01_OVERVIEW.md), [02_ARCHITECTURE](02_ARCHITECTURE.md), [10_RANKING_ENGINE](10_RANKING_ENGINE.md)

The "why" behind the shapes you see in the code. Each decision lists
the alternative considered and the tradeoff accepted.

## D-1 · Determinism is the primary invariant

**Decision.** Every core path produces byte-identical output for
identical input + state. No PRNG. No clock-derived branching.
Time is threaded as data when timing matters.

**Why.** Agent context is ranked, fused, and replayed. Without
determinism: regressions are unprovable; A/B tests are unmeasurable;
audit chains are useless. Determinism gives us the substrate for
every other invariant.

**Cost.** A small surface-area cost per module — every module that
could read the clock has to accept `now: datetime` as a parameter.
Worth it.

**Where it shows up.** `core/lifecycle/relevance_scorer.py` takes
`now`; `core/safety/health_gate.py` returns deterministic outcomes;
`core/embeddings/embedder.py::DeterministicEmbedder` ships SHA-512
instead of PRNG.

## D-2 · Cross-store identity (`unit_id ≡ node_id ≡ point_id`)

**Decision.** A unit's primary key is the SAME string in Postgres,
Neo4j, and Qdrant. `unit_id = SHA256(repo_id ⊕ file_path ⊕ qualified_name)`.

**Why.** No translation tables. Cross-store joins are trivial.
Sharding is per-repo so the join stays local on a single shard
index. Pinned by `test_phase1_compatibility.py`.

**Alternative considered.** Per-store auto-generated IDs with a
mapping table. Rejected — the table becomes a separate consistency
problem that has to be maintained alongside the data.

## D-3 · Mandated ranking weights

**Decision.** Phase-4 ranking weights are immutable: `0.35 / 0.25 /
0.20 / 0.15 / 0.05`. `FeatureWeights.__post_init__` rejects any
combination that doesn't sum to 1.0.

**Why.** Two reasons. (1) Determinism — the same query at the same
state must always rank the same; runtime-tunable weights would
invalidate every prior result. (2) Clarity — operators don't have
to ask "what weights produced this?". The answer is always the same.

**Cost.** Less optimization knob. Phase-6 `PerformanceAnalyzer`
exists exactly to propose new weights from observed signals — but
those proposals are CONSUMABLE by `RankingModel(weights=...)`, not
mutating the global default. Operators can A/B without changing
the spec.

## D-4 · Edge-rule pre-flight

**Decision.** Every graph edge passes through `is_edge_allowed()`
before write. Violations raise `EdgeRuleViolation` (programmer
error, fail-fast).

**Why.** Graph corruption is the worst kind — it's slow to detect
and corrupts every retrieval that touches the bad subgraph.
Catching the violation at write time makes corruption impossible
without bypassing the API.

**Alternative considered.** Validate periodically (Phase-8
`GraphValidator`). Kept as a backstop — the fail-fast at write is
the primary defense.

## D-5 · Phase boundaries are immutable

**Decision.** Each phase ends with a green test gate. Later phases
do not modify earlier code; they extend it additively.

**Why.** Predictability of regressions. If a Phase-7 commit breaks
a Phase-2 test, that commit violated the additive rule. The blast
radius of a phase change is bounded.

**Cost.** Some duplication when a later phase needs a slight
variation of an earlier helper. Accepted — duplication is cheaper
than churn.

**Tradeoff observed.** Phase-7 had to convert
`core/observability.py` from a single file to a package to add
`latency_tracker` etc. as siblings. The conversion preserved the
exact import surface — `from core.observability import get_tracer`
still works — so it counted as additive, not modification.

## D-6 · Append-only audit + hash chain

**Decision.** Every governance/MCP/policy decision emits an
`audit_event` chained by SHA-256 over `(prev_hash || canonical_json(payload))`.
Tampering breaks the chain; verification walks from genesis.

**Why.** Compliance, debuggability, and trust. An auditor must be
able to prove the system did exactly what it claimed. The chain
provides cryptographic evidence; the chain breaks IFF tampering
occurred between two known-good points.

**Alternative considered.** Database table with row-level signing.
Rejected — easier to tamper with selectively. The chain forces
all-or-nothing tampering.

## D-7 · Never delete data

**Decision.** Phase-6 lifecycle decisions are downgrade-only.
Compaction proposes plans. Quarantine flips a Redis flag. There is
no delete path through the engine.

**Why.** Deletes are irreversible. Drift detection, rollbacks, and
audit replay all require the historical data to still exist.
Storage is cheap; lost provenance is expensive.

**Cost.** Storage grows monotonically. Phase-11+ may add explicit
admin-driven prune paths for retention compliance — but they will
be out-of-band, audited, and deliberate.

## D-8 · MCP failures are in-band

**Decision.** Every MCP tool call returns HTTP 200. Failures are
conveyed via `status: "failed"` + `error_code` in the body.
The executor never raises to the server.

**Why.** Agents route on the tool's contract, not on HTTP. An
in-band error is structured, machine-readable, and stable.
HTTP-coded errors mix transport failures with application failures
and force agents to handle both shapes.

**Cost.** Operators have to look at the body, not the status code,
to detect failures. We document this loudly.

## D-9 · Read-only UI

**Decision.** The Phase-9 inspector and the Phase-10 Next.js UI are
read-only. Every mutation goes through the SDK / CLI / direct API.

**Why.** Mutation surfaces are auth + audit + policy paths. The UI
is a transparency layer — its job is to expose state, not to be
a CMS. Keeping it read-only means the UI never needs its own
permission model on top of the backend's.

**Tradeoff.** Operators can't "click to ingest a repo". They have
to use `memcl ingest` or `POST /ingest`. Acceptable — those
surfaces are explicit and auditable.

## D-10 · Deterministic embedder default

**Decision.** Phase-3 ships `DeterministicEmbedder` (SHA-512 →
L2-normalized) as the default. Real model-backed embedders
(OpenAI, Voyage, Cohere) plug in at the `Embedder` Protocol
boundary.

**Why.** Tests must run without API keys, network, or paid quotas.
Determinism guarantees must hold even when the model provider is
down. The Protocol boundary makes the swap trivial when an
operator wants real embeddings.

**Cost.** Default vectors carry no semantic information. Production
deployments wire a real embedder via the Protocol; nothing else
changes.

## D-11 · Schemas are versioned + immutable

**Decision.** Every persisted contract derives from `VersionedModel`
with `schema_version`, `created_at`, `updated_at`, `source`, `checksum`.
DenseRecord is explicitly immutable once released.

**Why.** Hot-fixing a schema in flight breaks every consumer that
already serialized data. Versioning forces a deliberate migration:
bump the version, write the migration, deploy.

**Where it shows up.** `schemas/base.py::SCHEMA_VERSION = "1"`. To
add a field, write a new model with the new version, leave the old
one in place, migrate at read time.

## D-12 · Strict layer boundaries (`apps → core → storage → schemas`)

**Decision.** `storage/` may not import `apps/` or `core/`.
`schemas/` may import nothing internal except `pydantic` + stdlib.
`infra/` is composed by `apps/` + `core/`, never the reverse.

**Why.** Circular imports are the fastest path to spaghetti.
Storage layers are testable in isolation when they don't know about
the upper layers.

**Verification.** Quick grep:
```bash
grep -rn "from apps" core/ storage/ schemas/ infra/
```
should print nothing.

## D-13 · Phase-9 boot is deterministic

**Decision.** `BootSequence` runs 8 stages in a fixed order. Same
backend state → same outcome.

**Why.** Operators need to reason about boot. A non-deterministic
boot ("sometimes audit_chain comes up before mcp_registry") makes
incident response harder. Fixed order = fixed mental model.

## D-14 · Safe-mode is process-wide, not per-route

**Decision.** `SafeModeController` is a single process-wide flag.
Engaging it does NOT individually rewrite routes — operators wire
the check at the route level (or at a reverse proxy) when they
want it enforced.

**Why.** Two reasons. (1) The flag must be observable from
anywhere in the process for diagnostics. (2) Per-route enforcement
varies by deployment — some operators want safe-mode to gate
ingestion only; others want full read-only. Keeping the flag
generic lets operators make the call.

---

## Open questions / future tradeoffs

- **Multi-vector indexing** — Phase-11. Today: one vector per unit.
- **Learned ranking weights** — Phase-12+ (currently "propose only").
- **Field-level encryption at rest** — not yet; relies on
  disk-level encryption.
- **Re-sharding migration tooling** — Phase-11. Today: changing
  `SCALE_SHARD_COUNT` requires manual data movement.

---

Next: [26 — Glossary](26_GLOSSARY.md)
