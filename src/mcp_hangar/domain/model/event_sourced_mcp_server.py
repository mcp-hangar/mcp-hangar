"""Event Sourced McpServer aggregate - mcp_server that rebuilds state from events."""

from dataclasses import dataclass
import threading
from typing import Any

from ...logging_config import get_logger
from ..events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerIdleDetected,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from ..value_objects import McpServerId
from .health_tracker import HealthTracker
from .mcp_server import McpServer, McpServerState
from .tool_catalog import ToolCatalog

logger = get_logger(__name__)


@dataclass
class McpServerSnapshot:
    """Snapshot of mcp_server state for faster loading."""

    mcp_server_id: str
    mode: str
    state: str
    version: int
    command: list[str] | None
    image: str | None
    endpoint: str | None
    env: dict[str, str]
    idle_ttl_s: int
    health_check_interval_s: int
    max_consecutive_failures: int
    consecutive_failures: int
    total_failures: int
    total_invocations: int
    last_success_at: float | None
    last_failure_at: float | None
    tool_names: list[str]
    last_used: float
    meta: dict[str, Any]
    circuit_breaker_state: dict[str, Any] | None = None

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mcp_server_id": self.mcp_server_id,
            "mode": self.mode,
            "state": self.state,
            "version": self.version,
            "command": self.command,
            "image": self.image,
            "endpoint": self.endpoint,
            "env": self.env,
            "idle_ttl_s": self.idle_ttl_s,
            "health_check_interval_s": self.health_check_interval_s,
            "max_consecutive_failures": self.max_consecutive_failures,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_invocations": self.total_invocations,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "tool_names": self.tool_names,
            "last_used": self.last_used,
            "meta": self.meta,
            "circuit_breaker_state": self.circuit_breaker_state,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "McpServerSnapshot":
        """Create from dictionary."""
        return cls(
            mcp_server_id=d["mcp_server_id"],
            mode=d["mode"],
            state=d["state"],
            version=d["version"],
            command=d.get("command"),
            image=d.get("image"),
            endpoint=d.get("endpoint"),
            env=d.get("env", {}),
            idle_ttl_s=d.get("idle_ttl_s", 300),
            health_check_interval_s=d.get("health_check_interval_s", 60),
            max_consecutive_failures=d.get("max_consecutive_failures", 3),
            consecutive_failures=d.get("consecutive_failures", 0),
            total_failures=d.get("total_failures", 0),
            total_invocations=d.get("total_invocations", 0),
            last_success_at=d.get("last_success_at"),
            last_failure_at=d.get("last_failure_at"),
            tool_names=d.get("tool_names", []),
            last_used=d.get("last_used", 0.0),
            meta=d.get("meta", {}),
            circuit_breaker_state=d.get("circuit_breaker_state"),
        )


class EventSourcedMcpServer(McpServer):
    """
    McpServer that rebuilds its state from domain events.

    Supports:
    - Loading from event stream
    - Creating snapshots for performance
    - Loading from snapshot + subsequent events
    - Time-travel debugging
    """

    def __init__(
        self,
        mcp_server_id: str,
        mode: str,
        command: list[str] | None = None,
        image: str | None = None,
        endpoint: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
    ):
        # Don't call super().__init__ to avoid recording McpServerStateChanged
        # Instead, manually initialize fields
        from .aggregate import AggregateRoot

        AggregateRoot.__init__(self)

        # Identity
        self._id = McpServerId(mcp_server_id)
        self._mode = mode

        # Configuration
        self._command = command
        self._image = image
        self._endpoint = endpoint
        self._env = env or {}
        self._idle_ttl_s = idle_ttl_s
        self._health_check_interval_s = health_check_interval_s

        # State - start in COLD
        self._state = McpServerState.COLD
        self._health = HealthTracker(max_consecutive_failures=max_consecutive_failures)
        self._tools = ToolCatalog()
        self._client: Any | None = None
        self._meta: dict[str, Any] = {}
        self._last_used: float = 0.0

        # Thread safety
        self._lock = threading.RLock()

        # Event sourcing specific
        self._events_applied: int = 0

    @classmethod
    def from_events(
        cls,
        mcp_server_id: str,
        mode: str,
        events: list[DomainEvent],
        command: list[str] | None = None,
        image: str | None = None,
        endpoint: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
    ) -> "EventSourcedMcpServer":
        """
        Create a mcp_server by replaying events.

        Args:
            mcp_server_id: McpServer identifier
            mode: McpServer mode
            events: List of domain events to replay
            command: Command for subprocess mode
            image: Docker image for docker mode
            endpoint: Endpoint for remote mode
            env: Environment variables
            idle_ttl_s: Idle TTL in seconds
            health_check_interval_s: Health check interval
            max_consecutive_failures: Max failures before degradation

        Returns:
            McpServer with state rebuilt from events
        """
        mcp_server = cls(
            mcp_server_id=mcp_server_id,
            mode=mode,
            command=command,
            image=image,
            endpoint=endpoint,
            env=env,
            idle_ttl_s=idle_ttl_s,
            health_check_interval_s=health_check_interval_s,
            max_consecutive_failures=max_consecutive_failures,
        )

        for event in events:
            mcp_server._apply_event(event)

        return mcp_server

    @classmethod
    def from_snapshot(
        cls, snapshot: McpServerSnapshot, events: list[DomainEvent] | None = None
    ) -> "EventSourcedMcpServer":
        """
        Create a mcp_server from snapshot and subsequent events.

        Args:
            snapshot: McpServer state snapshot
            events: Events that occurred after the snapshot

        Returns:
            McpServer with state rebuilt from snapshot + events
        """
        mcp_server = cls(
            mcp_server_id=snapshot.mcp_server_id,
            mode=snapshot.mode,
            command=snapshot.command,
            image=snapshot.image,
            endpoint=snapshot.endpoint,
            env=snapshot.env,
            idle_ttl_s=snapshot.idle_ttl_s,
            health_check_interval_s=snapshot.health_check_interval_s,
            max_consecutive_failures=snapshot.max_consecutive_failures,
        )

        # Restore state from snapshot
        mcp_server._state = McpServerState(snapshot.state)
        mcp_server._version = snapshot.version

        # Restore health tracker state
        mcp_server._health._consecutive_failures = snapshot.consecutive_failures
        mcp_server._health._total_failures = snapshot.total_failures
        mcp_server._health._total_invocations = snapshot.total_invocations
        mcp_server._health._last_success_at = snapshot.last_success_at
        mcp_server._health._last_failure_at = snapshot.last_failure_at

        # Restore tools (just names, no full schemas)
        for tool_name in snapshot.tool_names:
            mcp_server._tools._tools[tool_name] = {"name": tool_name}

        # Restore other state
        mcp_server._last_used = snapshot.last_used
        mcp_server._meta = dict(snapshot.meta)
        mcp_server._events_applied = snapshot.version

        # Apply subsequent events
        if events:
            for event in events:
                mcp_server._apply_event(event)

        return mcp_server

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id

    def _apply_event(self, event: DomainEvent) -> None:
        """
        Apply a single event to update state.

        This is the core of event sourcing - each event type
        has specific handlers that update the aggregate state.
        """
        self._events_applied += 1
        self._increment_version()

        if isinstance(event, McpServerStarted):
            self._apply_mcp_server_started(event)
        elif isinstance(event, McpServerStopped):
            self._apply_mcp_server_stopped(event)
        elif isinstance(event, McpServerDegraded):
            self._apply_mcp_server_degraded(event)
        elif isinstance(event, McpServerStateChanged):
            self._apply_state_changed(event)
        elif isinstance(event, ToolInvocationRequested):
            self._apply_tool_requested(event)
        elif isinstance(event, ToolInvocationCompleted):
            self._apply_tool_completed(event)
        elif isinstance(event, ToolInvocationFailed):
            self._apply_tool_failed(event)
        elif isinstance(event, HealthCheckPassed):
            self._apply_health_passed(event)
        elif isinstance(event, HealthCheckFailed):
            self._apply_health_failed(event)
        elif isinstance(event, McpServerIdleDetected):
            self._apply_idle_detected(event)

    def _apply_mcp_server_started(self, event: McpServerStarted) -> None:
        """Apply McpServerStarted event."""
        self._state = McpServerState.READY
        self._mode = event.mode
        self._health._consecutive_failures = 0
        self._last_used = event.occurred_at
        self._meta["started_at"] = event.occurred_at
        self._meta["tools_count"] = event.tools_count

    def _apply_mcp_server_stopped(self, event: McpServerStopped) -> None:
        """Apply McpServerStopped event."""
        self._state = McpServerState.COLD
        self._client = None
        self._tools.clear()

    def _apply_mcp_server_degraded(self, event: McpServerDegraded) -> None:
        """Apply McpServerDegraded event."""
        self._state = McpServerState.DEGRADED
        self._health._consecutive_failures = event.consecutive_failures
        self._health._total_failures = event.total_failures

    def _apply_state_changed(self, event: McpServerStateChanged) -> None:
        """Apply McpServerStateChanged event."""
        self._state = McpServerState(event.new_state)

    def _apply_tool_requested(self, event: ToolInvocationRequested) -> None:
        """Apply ToolInvocationRequested event."""
        self._health._total_invocations += 1

    def _apply_tool_completed(self, event: ToolInvocationCompleted) -> None:
        """Apply ToolInvocationCompleted event."""
        self._health._consecutive_failures = 0
        self._health._last_success_at = event.occurred_at
        self._last_used = event.occurred_at

    def _apply_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Apply ToolInvocationFailed event."""
        self._health._consecutive_failures += 1
        self._health._total_failures += 1
        self._health._last_failure_at = event.occurred_at

    def _apply_health_passed(self, event: HealthCheckPassed) -> None:
        """Apply HealthCheckPassed event."""
        self._health._consecutive_failures = 0
        self._health._last_success_at = event.occurred_at

    def _apply_health_failed(self, event: HealthCheckFailed) -> None:
        """Apply HealthCheckFailed event."""
        self._health._consecutive_failures = event.consecutive_failures
        self._health._last_failure_at = event.occurred_at

    def _apply_idle_detected(self, event: McpServerIdleDetected) -> None:
        """Apply McpServerIdleDetected event."""
        # Just a marker event, no state change
        pass

    def create_snapshot(self) -> McpServerSnapshot:
        """
        Create a snapshot of current state.

        Returns:
            McpServerSnapshot that can be serialized
        """
        with self._lock:
            return McpServerSnapshot(
                mcp_server_id=self.mcp_server_id,
                mode=self._mode,
                state=self._state.value,
                version=self._version,
                command=self._command,
                image=self._image,
                endpoint=self._endpoint,
                env=dict(self._env),
                idle_ttl_s=self._idle_ttl_s,
                health_check_interval_s=self._health_check_interval_s,
                max_consecutive_failures=self._health.max_consecutive_failures,
                consecutive_failures=self._health._consecutive_failures,
                total_failures=self._health._total_failures,
                total_invocations=self._health._total_invocations,
                last_success_at=self._health._last_success_at,
                last_failure_at=self._health._last_failure_at,
                tool_names=self._tools.list_names(),
                last_used=self._last_used,
                meta=dict(self._meta),
            )

    @property
    def events_applied(self) -> int:
        """Number of events applied to this aggregate."""
        return self._events_applied

    def replay_to_version(self, target_version: int, events: list[DomainEvent]) -> "EventSourcedMcpServer":
        """
        Create a new mcp_server at a specific version (time travel).

        Args:
            target_version: Target version to replay to
            events: All events for this mcp_server

        Returns:
            New mcp_server instance at the target version
        """
        mcp_server = EventSourcedMcpServer(
            mcp_server_id=self.mcp_server_id,
            mode=self._mode,
            command=self._command,
            image=self._image,
            endpoint=self._endpoint,
            env=self._env,
            idle_ttl_s=self._idle_ttl_s,
            health_check_interval_s=self._health_check_interval_s,
            max_consecutive_failures=self._health.max_consecutive_failures,
        )

        for i, event in enumerate(events):
            if i >= target_version:
                break
            mcp_server._apply_event(event)

        return mcp_server

    def get_uncommitted_events(self) -> list[DomainEvent]:
        """
        Get events recorded but not yet persisted.

        Returns:
            List of uncommitted domain events
        """
        return list(self._uncommitted_events)

    def mark_events_committed(self) -> None:
        """Clear uncommitted events after persistence."""
        self._uncommitted_events.clear()


# legacy aliases
EventSourcedProvider = EventSourcedMcpServer
ProviderSnapshot = McpServerSnapshot
