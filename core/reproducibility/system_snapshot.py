"""System snapshot — content-hashed bundle of every key state component.

A snapshot id is the SHA-256 over canonical JSON of:
    * graph_state_hash       (sorted node ids + edge tuples)
    * embedding_index_hash   (sorted (point_id, vector_sha) pairs)
    * retrieval_config_hash  (FeatureWeights + thresholds)
    * schema_version
    * mcp_registry_hash      (sorted tool names + their request schemas)
    * lifecycle_state_version (Phase-6 / Phase-8 state version token)

A snapshot is replayable iff every dependency's content hash is
captured here — the replay engine consults the snapshot to confirm
the system is in the same state before re-running.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from schemas import SCHEMA_VERSION, GraphEdge, GraphNode


def _sha(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        default=str, ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotComponents:
    """Per-component hashes that compose a snapshot id."""

    graph_state_hash: str
    embedding_index_hash: str
    retrieval_config_hash: str
    schema_version: str
    mcp_registry_hash: str
    state_version_token: str

    def to_payload(self) -> dict[str, str]:
        return {
            "graph_state_hash": self.graph_state_hash,
            "embedding_index_hash": self.embedding_index_hash,
            "retrieval_config_hash": self.retrieval_config_hash,
            "schema_version": self.schema_version,
            "mcp_registry_hash": self.mcp_registry_hash,
            "state_version_token": self.state_version_token,
        }


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    snapshot_id: str
    tenant_id: str
    captured_at: datetime
    components: SnapshotComponents
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "tenant_id": self.tenant_id,
            "captured_at": self.captured_at.isoformat(),
            "components": self.components.to_payload(),
            "metadata": dict(sorted(self.metadata.items())),
        }


class SystemSnapshotBuilder:
    """Pure builder — every input is data, no live storage calls.

    Callers pass already-materialized projections of the system
    state; the builder hashes them deterministically. This is what
    makes snapshots reproducible: identical inputs always produce
    identical snapshot IDs.
    """

    def build(
        self,
        *,
        tenant_id: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        embeddings: dict[str, Sequence[float]],
        retrieval_config: dict[str, float],
        mcp_tool_names: Sequence[str],
        mcp_request_schemas: dict[str, str],
        state_version_token: str,
        captured_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SystemSnapshot:
        components = SnapshotComponents(
            graph_state_hash=self._graph_hash(nodes, edges),
            embedding_index_hash=self._embeddings_hash(embeddings),
            retrieval_config_hash=_sha(dict(sorted(retrieval_config.items()))),
            schema_version=SCHEMA_VERSION,
            mcp_registry_hash=self._mcp_hash(mcp_tool_names, mcp_request_schemas),
            state_version_token=state_version_token,
        )
        snapshot_id = _sha(components.to_payload())
        return SystemSnapshot(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            captured_at=captured_at or datetime.now(UTC),
            components=components,
            metadata=dict(metadata or {}),
        )

    # ----- per-component hashes -----
    def _graph_hash(
        self, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge],
    ) -> str:
        sorted_nodes = sorted(
            ((n.node_id, n.kind.value, n.qualified_name) for n in nodes),
            key=lambda t: t[0],
        )
        sorted_edges = sorted(
            ((e.src_id, e.kind.value, e.dst_id) for e in edges),
        )
        return _sha({"nodes": sorted_nodes, "edges": sorted_edges})

    def _embeddings_hash(self, embeddings: dict[str, Sequence[float]]) -> str:
        # Hash each vector to a short fingerprint to keep the snapshot
        # input bounded — the snapshot id only needs to detect change,
        # not reconstruct vectors.
        per_entry: list[tuple[str, str]] = []
        for entity_id in sorted(embeddings):
            v = embeddings[entity_id]
            per_entry.append((
                entity_id,
                hashlib.sha256(
                    ",".join(f"{x:.6f}" for x in v).encode("utf-8")
                ).hexdigest(),
            ))
        return _sha(per_entry)

    def _mcp_hash(
        self,
        tool_names: Sequence[str],
        schemas: dict[str, str],
    ) -> str:
        return _sha({
            "tools": sorted(tool_names),
            "schemas": dict(sorted(schemas.items())),
        })


__all__ = [
    "SnapshotComponents",
    "SystemSnapshot",
    "SystemSnapshotBuilder",
]
