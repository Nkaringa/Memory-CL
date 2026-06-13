"""Default ToolRegistry — wires the v2 agent-facing tool surface.

Production code calls `build_default_registry()` exactly once at app
startup; BOTH transports (the REST router under /mcp and the native
MCP-protocol server) list and execute from this same registry, so the
surface can never diverge between them.

Tests can construct their own registry with fakes.
"""

from __future__ import annotations

from core.mcp.execution import ToolRegistry
from core.mcp.tools import (
    ExploreTool,
    FindSymbolTool,
    GetContextTool,
    GetModuleSummaryTool,
    GetRelatedComponentsTool,
    GetRisksTool,
    IngestRepositoryTool,
    ListReposTool,
    QueryGraphTool,
    ReadFileTool,
    ReadUnitTool,
    RepoOverviewTool,
    SearchCodeTool,
    UpdateMemoryTool,
)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    # v2 agent-facing surface
    registry.register(SearchCodeTool())
    registry.register(ReadUnitTool())
    registry.register(ReadFileTool())
    registry.register(ExploreTool())
    registry.register(FindSymbolTool())
    registry.register(ListReposTool())
    registry.register(RepoOverviewTool())
    # kept v1 tools
    registry.register(GetModuleSummaryTool())
    registry.register(GetRisksTool())
    registry.register(IngestRepositoryTool())
    registry.register(UpdateMemoryTool())
    # deprecated v1 aliases (delegate to v2 internals)
    registry.register(GetContextTool())
    registry.register(GetRelatedComponentsTool())
    registry.register(QueryGraphTool())
    return registry
