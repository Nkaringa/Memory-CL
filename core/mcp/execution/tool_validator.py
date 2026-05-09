from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError


class ToolValidationError(ValueError):
    """Raised when a payload fails the tool's request schema."""

    def __init__(self, tool: str, errors: list[dict[str, Any]]) -> None:
        self.tool = tool
        self.errors = errors
        super().__init__(f"validation failed for tool '{tool}': {errors}")


def validate_tool_request(
    tool_name: str, schema: type[BaseModel], payload: dict[str, Any]
) -> BaseModel:
    """Validate `payload` against `schema` and surface a structured error.

    Pydantic's own ValidationError is human-friendly but its formatting
    leaks library-specific shapes; we re-cast it into a stable list of
    {"loc", "msg", "type"} dicts that the executor maps onto the spec'd
    `ToolResponse.error` payload.
    """
    try:
        return schema.model_validate(payload)
    except ValidationError as ve:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in ve.errors()
        ]
        raise ToolValidationError(tool_name, errors) from ve
