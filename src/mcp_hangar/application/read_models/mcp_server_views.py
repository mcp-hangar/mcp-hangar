"""Read models (views) for mcp_server queries.

Read models are optimized for specific query use cases.
They are immutable and contain only the data needed for display.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolInfo:
    """Read model for tool information.

    Mirrors :class:`~domain.model.tool_catalog.ToolSchema` field for field. It
    did not, so the REST tool views dropped `title`, `annotations`, `execution`,
    `icons` and `_meta` even once discovery started carrying them (#880) --
    leaving the inspection surface disagreeing with what the MCP surface serves.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    title: str | None = None
    annotations: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    icons: list[Any] | None = None
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, in wire (camelCase) names, omitting unset fields."""
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        for key, value in (
            ("outputSchema", self.output_schema),
            ("title", self.title),
            ("annotations", self.annotations),
            ("execution", self.execution),
            ("icons", self.icons),
            ("_meta", self.meta),
        ):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class HealthInfo:
    """Read model for health information."""

    consecutive_failures: int
    total_invocations: int
    total_failures: int
    success_rate: float
    can_retry: bool
    last_success_ago: float | None = None  # seconds since last success
    last_failure_ago: float | None = None  # seconds since last failure

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "consecutive_failures": self.consecutive_failures,
            "total_invocations": self.total_invocations,
            "total_failures": self.total_failures,
            "success_rate": self.success_rate,
            "can_retry": self.can_retry,
            "last_success_ago": self.last_success_ago,
            "last_failure_ago": self.last_failure_ago,
        }


@dataclass(frozen=True)
class McpServerSummary:
    """Read model for mcp_server list view.

    Contains minimal information for listing mcp_servers.
    """

    mcp_server_id: str
    state: str
    mode: str
    is_alive: bool
    tools_count: int
    health_status: str  # healthy, degraded, unhealthy
    description: str | None = None
    tools_predefined: bool = False  # True if tools were defined in config (no startup needed)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "mcp_server_id": self.mcp_server_id,
            "state": self.state,
            "mode": self.mode,
            "alive": self.is_alive,
            "tools_count": self.tools_count,
            "health_status": self.health_status,
            "tools_predefined": self.tools_predefined,
        }
        if self.description:
            result["description"] = self.description
        return result


@dataclass(frozen=True)
class McpServerDetails:
    """Read model for detailed mcp_server view.

    Contains full information about a mcp_server.
    """

    mcp_server_id: str
    state: str
    mode: str
    is_alive: bool
    tools: list[ToolInfo]
    health: HealthInfo
    idle_time: float
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mcp_server_id": self.mcp_server_id,
            "state": self.state,
            "mode": self.mode,
            "alive": self.is_alive,
            "tools": [t.to_dict() for t in self.tools],
            "health": self.health.to_dict(),
            "idle_time": self.idle_time,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class SystemMetrics:
    """Read model for system-wide metrics."""

    total_mcp_servers: int
    mcp_servers_by_state: dict[str, int]
    total_tools: int
    total_invocations: int
    total_failures: int
    overall_success_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_mcp_servers": self.total_mcp_servers,
            "mcp_servers_by_state": self.mcp_servers_by_state,
            "total_tools": self.total_tools,
            "total_invocations": self.total_invocations,
            "total_failures": self.total_failures,
            "overall_success_rate": self.overall_success_rate,
        }


# legacy aliases
ProviderSummary = McpServerSummary
ProviderDetails = McpServerDetails
