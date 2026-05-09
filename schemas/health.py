from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from schemas.base import SCHEMA_VERSION


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: HealthStatus
    latency_ms: float | None = None
    error: str | None = None


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    status: HealthStatus = HealthStatus.OK
    service: str = "memory-cl"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    status: HealthStatus
    components: list[ComponentHealth]


class DependencyKind(StrEnum):
    """Coarse classification surfaced in /health/dependencies.

    Operators slice the report by kind to know which kinds of probes
    failed: storage outage vs. control-plane outage vs. governance.
    """

    STORAGE = "storage"
    CONTROL = "control"
    GOVERNANCE = "governance"


class DependencyCheck(BaseModel):
    """One row of the deep dependency report.

    `kind` separates raw infra (postgres/qdrant/neo4j/redis) from
    control-plane checks (MCP registry) and governance checks (audit
    chain). `required` is True when degradation here MUST flip the
    overall status to anything other than OK.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: DependencyKind
    status: HealthStatus
    required: bool = True
    latency_ms: float | None = None
    detail: str | None = None
    error: str | None = None


class DependencyReport(BaseModel):
    """Phase-10 expanded dependency report — surfaced at /health/dependencies.

    Summarizes both raw storage probes (already covered by /health/ready)
    AND control-plane / governance probes that the simple readiness
    check intentionally skips. The overall status follows the same
    aggregation rule: OK iff every required check is OK.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    status: HealthStatus
    checks: list[DependencyCheck]
    safe_mode: bool = False
    safe_mode_reason: str | None = None
