from __future__ import annotations

import json

from schemas.base import SCHEMA_VERSION, VersionedModel
from schemas.health import (
    ComponentHealth,
    HealthStatus,
    LivenessResponse,
    ReadinessResponse,
)


class _Sample(VersionedModel):
    name: str
    deps: list[str]


def test_versioned_model_carries_required_metadata() -> None:
    m = _Sample(name="auth", deps=["postgres", "redis"])
    assert m.schema_version == SCHEMA_VERSION
    assert m.created_at is not None
    assert m.updated_at is not None
    assert m.source == "memory-cl"
    assert m.checksum is None  # not auto-populated


def test_checksum_is_deterministic_and_excludes_metadata() -> None:
    m1 = _Sample(name="auth", deps=["postgres", "redis"])
    m2 = _Sample(name="auth", deps=["postgres", "redis"])
    # Different timestamps, different sources — same content checksum.
    assert m1.compute_checksum() == m2.compute_checksum()

    m3 = m1.with_checksum()
    assert m3.checksum == m1.compute_checksum()
    # with_checksum is idempotent.
    assert m3.with_checksum().checksum == m3.checksum


def test_liveness_response_is_token_efficient() -> None:
    r = LivenessResponse()
    payload = json.loads(r.model_dump_json())
    assert payload == {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "service": "memory-cl",
    }


def test_readiness_response_components_round_trip() -> None:
    r = ReadinessResponse(
        status=HealthStatus.DEGRADED,
        components=[
            ComponentHealth(name="postgres", status=HealthStatus.OK, latency_ms=2.1),
            ComponentHealth(
                name="qdrant",
                status=HealthStatus.DOWN,
                latency_ms=10.0,
                error="connection refused",
            ),
        ],
    )
    raw = r.model_dump_json()
    parsed = ReadinessResponse.model_validate_json(raw)
    assert parsed == r
