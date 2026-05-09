"""Default ToolRegistry — wires the 7 mandated tools.

Production code calls `build_default_registry()` exactly once at app
startup. Tests can construct their own registry with fakes.
"""

from __future__ import annotations

from core.mcp.execution import ToolRegistry
from core.mcp.tools import (
    GetContextTool,
    GetModuleSummaryTool,
    GetRelatedComponentsTool,
    GetRisksTool,
    IngestRepositoryTool,
    QueryGraphTool,
    UpdateMemoryTool,
)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GetContextTool())
    registry.register(GetModuleSummaryTool())
    registry.register(GetRelatedComponentsTool())
    registry.register(GetRisksTool())
    registry.register(IngestRepositoryTool())
    registry.register(QueryGraphTool())
    registry.register(UpdateMemoryTool())
    return registry
