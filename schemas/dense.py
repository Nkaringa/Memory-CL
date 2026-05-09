from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Per DENSE_NOTATION_SPEC the structure is IMMUTABLE once released.
# Increment this only when introducing a new spec version.
DENSE_VERSION: str = "1"

# Spec rule: max key length 5 chars. Enforced statically by the field set.
DENSE_KEYS: tuple[str, ...] = ("api", "dep", "evt", "file", "id", "risk", "t", "v")


class DenseRecord(BaseModel):
    """Token-optimized record per DENSE_NOTATION_SPEC.

    All keys ≤ 5 chars, arrays alphabetically sorted, deterministic JSON
    output. This object is what the retrieval engine returns in compressed
    context packets — it is NOT a human-readable summary.
    """

    model_config = ConfigDict(extra="forbid")

    MAX_KEY_LEN: ClassVar[int] = 5

    v: str = Field(default=DENSE_VERSION, description="Schema version")
    t: str = Field(description="Type tag, e.g. svc/mod/api/fn")
    id: str = Field(description="Stable identifier")
    dep: list[str] = Field(default_factory=list)
    api: list[str] = Field(default_factory=list)
    risk: list[str] = Field(default_factory=list)
    file: list[str] = Field(default_factory=list)
    evt: list[str] = Field(default_factory=list)

    @field_validator("dep", "api", "risk", "file", "evt")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))

    @field_validator("t")
    @classmethod
    def _short_type(cls, v: str) -> str:
        if not v or len(v) > 8:
            raise ValueError("'t' must be a short type tag (1-8 chars)")
        return v

    def to_dense_json(self, *, drop_empty: bool = True) -> str:
        """Deterministic JSON serialization for storage and transport.

        - keys sorted
        - arrays sorted (already by validator)
        - compact separators
        - empty arrays dropped iff `drop_empty` (default True for token efficiency)

        Same input -> same bytes, always.
        """
        payload = self.model_dump(mode="json")
        if drop_empty:
            payload = {k: v for k, v in payload.items() if not (isinstance(v, list) and not v)}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Compile-time guard: every declared field obeys the 5-char key cap.
# (`v` and `t` and `id` are <=5; `dep/api/risk/file/evt` are <=5.)
for _key in DenseRecord.model_fields:
    if len(_key) > DenseRecord.MAX_KEY_LEN:
        raise RuntimeError(f"DenseRecord field '{_key}' violates max key length")
