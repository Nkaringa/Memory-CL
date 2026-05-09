from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: str = "1"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class VersionedModel(BaseModel):
    """Base for every persisted contract.

    Required by ARCHITECTURE_RULES: schema_version, created_at, updated_at,
    source, checksum. Subclasses must NOT override these fields.
    """

    model_config = ConfigDict(
        frozen=False,
        extra="forbid",
        populate_by_name=True,
        ser_json_timedelta="iso8601",
    )

    SCHEMA_VERSION: ClassVar[str] = SCHEMA_VERSION

    schema_version: str = Field(default=SCHEMA_VERSION, description="Immutable schema version")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    source: str = Field(default="memory-cl", description="Producing component")
    checksum: str | None = Field(default=None, description="Deterministic content hash")

    def compute_checksum(self) -> str:
        """Deterministic SHA256 over content fields (excluding metadata).

        Key ordering is sorted to satisfy determinism rules.
        """
        payload: dict[str, Any] = self.model_dump(
            mode="json",
            exclude={"schema_version", "created_at", "updated_at", "source", "checksum"},
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def with_checksum(self) -> VersionedModel:
        """Return a copy with checksum populated; deterministic and idempotent."""
        return self.model_copy(update={"checksum": self.compute_checksum()})
