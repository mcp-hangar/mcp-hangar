"""Read models (views) for mcp_server queries.

Read models are optimized for specific query use cases.
They are immutable and contain only the data needed for display.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ...domain.model.mcp_server import DeadStatus

#: `DeadInfo.revived_by`: a call, once its backoff has passed, or a deliberate start.
REVIVED_BY_CALL_OR_START = "call_or_start"
#: `DeadInfo.revived_by`: only a deliberate start, such as `hangar_start` or the REST start.
REVIVED_BY_START = "start"


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


def _utc(timestamp: float | None) -> str | None:
    """An epoch time as ISO 8601 UTC, or None."""
    return None if timestamp is None else datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


@dataclass(frozen=True)
class DeadInfo:
    """Why a server is dead, as every read surface reports it (#1418).

    The `dead` field of `hangar_details`, `hangar_list`, `hangar_status`,
    `GET /api/mcp_servers` and `GET /api/mcp_servers/{id}`. It is null on each
    while the server is not dead. Each value is a fixed code or a time, never
    text an upstream sent.

    - `reason`: `given_up` (the recovery saga ran out of retries), `crashed`
      (its process or connection died), `start_failed` (a start failed) or
      `capability_blocked` (block or quarantine mode found a tool outside its
      `expected_tools`). `unknown` only for a server restored from a record
      written before the reason was kept.
    - `since`: when it went dead, ISO 8601 UTC.
    - `retry_allowed_at`: the latest time its backoff ends, ISO 8601 UTC; from
      then on a call is not refused for backoff. Null when no call starts it,
      or no failure is recorded, so no backoff applies.
    - `revived_by`: `call_or_start`, a call once its backoff has passed or a
      deliberate start; or `start`, only a deliberate start (`capability_blocked`).
    """

    reason: str
    since: float | None
    retry_allowed_at: float | None
    revived_by: str

    @classmethod
    def of(cls, status: DeadStatus) -> "DeadInfo":
        """The read model of the aggregate's `DeadStatus`."""
        return cls(
            reason=status.reason,
            since=status.since,
            retry_allowed_at=status.retry_allowed_at,
            revived_by=REVIVED_BY_CALL_OR_START if status.revived_by_call else REVIVED_BY_START,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, times as ISO 8601 UTC."""
        return {
            "reason": self.reason,
            "since": _utc(self.since),
            "retry_allowed_at": _utc(self.retry_allowed_at),
            "revived_by": self.revived_by,
        }


def dead_info(server: Any, state: str) -> DeadInfo | None:
    """Why ``server`` is dead, or None unless ``state``, the state a surface reports, is dead.

    Keyed on the reported state too, so a surface never pairs a state other
    than dead with a reason.
    """
    if state != "dead":
        return None
    read = getattr(server, "dead_status", None)
    status = read() if callable(read) else None
    return DeadInfo.of(status) if isinstance(status, DeadStatus) else None


def dead_dict(dead: DeadInfo | None) -> dict[str, Any] | None:
    """The `dead` field: `DeadInfo.to_dict()`, or None."""
    return dead.to_dict() if dead is not None else None


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
    dead: DeadInfo | None = None  # why it is dead, while it is

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
            "dead": dead_dict(self.dead),
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
    dead: DeadInfo | None = None  # why it is dead, while it is

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
            "dead": dead_dict(self.dead),
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
