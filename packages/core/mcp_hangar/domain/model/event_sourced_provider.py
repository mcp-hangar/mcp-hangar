"""Event Sourced Provider aggregate - provider that rebuilds state from events."""

import threading
from dataclasses import dataclass
from typing import Any, Optional

from ...logging_config import get_logger
from ..events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderIdleDetected,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from ..value_objects import ProviderId
from .health_tracker import HealthTracker
from .provider import Provider, ProviderState
from .tool_catalog import ToolCatalog

logger = get_logger(__name__)


@dataclass
class ProviderSnapshot:
    """Snapshot of provider state for faster loading."""

    provider_id: str
    mode: str
    state: str
    version: int
    command: Optional[list[str]]
    image: Optional[str]
    endpoint: Optional[str]
    env: dict[str, str]
    idle_ttl_s: int
    health_check_interval_s: int
    max_consecutive_failures: int
    consecutive_failures: int
    total_failures: int
    total_invocations: int
    last_success_at: Optional[float]
    last_failure_at: Optional[float]
    tool_names: list[str]
    last_used: float
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "provider_id": self.provider_id,
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
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProviderSnapshot":
        """Create from dictionary."""
        return cls(
            provider_id=d["provider_id"],
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
        )


class EventSourcedProvider(Provider):
    """
    Provider that rebuilds its state from domain events.

    Supports:
    - Loading from event stream
    - Creating snapshots for performance
    - Loading from snapshot + subsequent events
    - Time-travel debugging
    """

    def __init__(
        self,
        provider_id: str,
        mode: str,
        command: Optional[list[str]] = None,
        image: Optional[str] = None,
        endpoint: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
    ):
        # Don't call super().__init__ to avoid recording ProviderStateChanged
        # Instead, manually initialize fields
        from .aggregate import AggregateRoot

        AggregateRoot.__init__(self)

        # Identity
        self._id = ProviderId(provider_id)
        self._mode = mode

        # Configuration
        self._command = command
        self._image = image
        self._endpoint = endpoint
        self._env = env or {}
        self._idle_ttl_s = idle_ttl_s
        self._health_check_interval_s = health_check_interval_s

        # State - start in COLD
        self._state = ProviderState.COLD
        self._health = HealthTracker(max_consecutive_failures=max_consecutive_failures)
        self._tools = ToolCatalog()
        self._client: Optional[Any] = None
        self._meta: dict[str, Any] = {}
        self._last_used: float = 0.0

        # Thread safety
        self._lock = threading.RLock()

        # Event sourcing specific
        self._events_applied: int = 0

    @classmethod
    def from_events(
        cls,
        provider_id: str,
        mode: str,
        events: list[DomainEvent],
        command: Optional[list[str]] = None,
        image: Optional[str] = None,
        endpoint: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
    ) -> "EventSourcedProvider":
        """
        Create a provider by replaying events.

        Args:
            provider_id: Provider identifier
            mode: Provider mode
            events: List of domain events to replay
            command: Command for subprocess mode
            image: Docker image for docker mode
            endpoint: Endpoint for remote mode
            env: Environment variables
            idle_ttl_s: Idle TTL in seconds
            health_check_interval_s: Health check interval
            max_consecutive_failures: Max failures before degradation

        Returns:
            Provider with state rebuilt from events
        """
        provider = cls(
            provider_id=provider_id,
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
            provider._apply_event(event)

        return provider

    @classmethod
    def from_snapshot(
        cls, snapshot: ProviderSnapshot, events: Optional[list[DomainEvent]] = None
    ) -> "EventSourcedProvider":
        """
        Create a provider from snapshot and subsequent events.

        Args:
            snapshot: Provider state snapshot
            events: Events that occurred after the snapshot

        Returns:
            Provider with state rebuilt from snapshot + events
        """
        provider = cls(
            provider_id=snapshot.provider_id,
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
        provider._state = ProviderState(snapshot.state)
        provider._version = snapshot.version

        # Restore health tracker state
        provider._health._consecutive_failures = snapshot.consecutive_failures
        provider._health._total_failures = snapshot.total_failures
        provider._health._total_invocations = snapshot.total_invocations
        provider._health._last_success_at = snapshot.last_success_at
        provider._health._last_failure_at = snapshot.last_failure_at

        # Restore tools (just names, no full schemas)
        for tool_name in snapshot.tool_names:
            provider._tools._tools[tool_name] = {"name": tool_name}

        # Restore other state
        provider._last_used = snapshot.last_used
        provider._meta = dict(snapshot.meta)
        provider._events_applied = snapshot.version

        # Apply subsequent events
        if events:
            for event in events:
                provider._apply_event(event)

        return provider

    def _apply_event(self, event: DomainEvent) -> None:
        """
        Apply a single event to update state.

        This is the core of event sourcing - each event type
        has specific handlers that update the aggregate state.
        """
        self._events_applied += 1
        self._increment_version()

        if isinstance(event, ProviderStarted):
            self._apply_provider_started(event)
        elif isinstance(event, ProviderStopped):
            self._apply_provider_stopped(event)
        elif isinstance(event, ProviderDegraded):
            self._apply_provider_degraded(event)
        elif isinstance(event, ProviderStateChanged):
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
        elif isinstance(event, ProviderIdleDetected):
            self._apply_idle_detected(event)

    def _apply_provider_started(self, event: ProviderStarted) -> None:
        """Apply ProviderStarted event."""
        self._state = ProviderState.READY
        self._mode = event.mode
        self._health._consecutive_failures = 0
        self._last_used = event.occurred_at
        self._meta["started_at"] = event.occurred_at
        self._meta["tools_count"] = event.tools_count

    def _apply_provider_stopped(self, event: ProviderStopped) -> None:
        """Apply ProviderStopped event."""
        self._state = ProviderState.COLD
        self._client = None
        self._tools.clear()

    def _apply_provider_degraded(self, event: ProviderDegraded) -> None:
        """Apply ProviderDegraded event."""
        self._state = ProviderState.DEGRADED
        self._health._consecutive_failures = event.consecutive_failures
        self._health._total_failures = event.total_failures

    def _apply_state_changed(self, event: ProviderStateChanged) -> None:
        """Apply ProviderStateChanged event."""
        self._state = ProviderState(event.new_state)

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

    def _apply_idle_detected(self, event: ProviderIdleDetected) -> None:
        """Apply ProviderIdleDetected event."""
        # Just a marker event, no state change
        pass

    def create_snapshot(self) -> ProviderSnapshot:
        """
        Create a snapshot of current state.

        Returns:
            ProviderSnapshot that can be serialized
        """
        with self._lock:
            return ProviderSnapshot(
                provider_id=self.provider_id,
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

    def replay_to_version(self, target_version: int, events: list[DomainEvent]) -> "EventSourcedProvider":
        """
        Create a new provider at a specific version (time travel).

        Args:
            target_version: Target version to replay to
            events: All events for this provider

        Returns:
            New provider instance at the target version
        """
        provider = EventSourcedProvider(
            provider_id=self.provider_id,
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
            provider._apply_event(event)

        return provider

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
