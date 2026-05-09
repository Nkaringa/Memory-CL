"""Tool response envelope.

Every tool returns the same wire shape: a `ToolResponse` carrying
`tool`, `request_id`, `status`, `data`, `error`, and `latency_ms`.
The `data` field is intentionally untyped — each tool packs its
output verbatim and the agent unpacks it per the tool's contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.base import SCHEMA_VERSION


class ToolStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ToolErrorCode(StrEnum):
    """Coarse error taxonomy used by the executor's structured-error path."""

    VALIDATION = "validation_error"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN_TOOL = "unknown_tool"
    BACKEND = "backend_error"
    INTERNAL = "internal_error"


class ToolResponse(BaseModel):
    """Wire response for every MCP tool call.

    Designed so a client can route on `status` alone — `data` is
    populated iff status==SUCCESS; `error`/`error_code` are populated
    iff status==FAILED. Both pairs are mutually exclusive.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    tool: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: ToolErrorCode | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
