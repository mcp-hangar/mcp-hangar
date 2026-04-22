"""Data models for MCP tools responses."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSummary:
    """Summary of a tool.

    Attributes:
        name: Tool name.
        description: Optional tool description.
    """

    name: str
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {"name": self.name}
        if self.description:
            result["description"] = self.description
        return result


@dataclass
class HangarLoadResult:
    """Result of loading a mcp_server via hangar_load.

    Attributes:
        status: Result status ("loaded", "already_loaded", "failed", "missing_secrets").
        mcp_server_id: McpServer ID if loaded.
        mcp_server_name: Server name from registry.
        tools: List of tool summaries if loaded.
        message: Human-readable message.
        warnings: List of warnings.
        instructions: Optional instructions (e.g., for missing secrets).
    """

    status: str
    mcp_server_id: str | None = None
    mcp_server_name: str | None = None
    tools: list[ToolSummary] | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MCP response."""
        result = {
            "status": self.status,
            "message": self.message,
        }
        if self.mcp_server_id:
            result["mcp_server"] = self.mcp_server_id  # Normalized key name
        if self.mcp_server_name:
            result["mcp_server_name"] = self.mcp_server_name
        if self.tools is not None:
            result["tools"] = [t.to_dict() if isinstance(t, ToolSummary) else t for t in self.tools]
            result["tools_count"] = len(self.tools)
        if self.warnings:
            result["warnings"] = self.warnings
        if self.instructions:
            result["instructions"] = self.instructions
        return result


@dataclass
class HangarUnloadResult:
    """Result of unloading a mcp_server via hangar_unload.

    Attributes:
        status: Result status ("unloaded", "not_found", "not_hot_loaded").
        mcp_server_id: McpServer ID that was unloaded (internal field).
        message: Human-readable message.
        lifetime_seconds: How long the mcp_server was loaded.
    """

    status: str
    mcp_server_id: str
    message: str = ""
    lifetime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MCP response."""
        return {
            "status": self.status,
            "mcp_server": self.mcp_server_id,  # Normalized key name
            "message": self.message,
            "lifetime_seconds": round(self.lifetime_seconds, 1),
        }
