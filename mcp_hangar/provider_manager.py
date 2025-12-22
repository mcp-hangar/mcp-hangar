"""Provider lifecycle manager - backward compatibility wrapper.

This module provides backward compatibility for code that uses ProviderManager.
New code should use the Provider aggregate directly from mcp_hangar.domain.model.

Deprecated: Use mcp_hangar.domain.model.Provider instead.
"""

import logging
from typing import Any, Dict

from .domain.model import Provider
from .domain.value_objects import ProviderState
from .infrastructure.event_bus import get_event_bus
from .models import ProviderConnection, ProviderSpec, ToolSchema

logger = logging.getLogger(__name__)


class ProviderManager:
    """
    Thread-safe provider lifecycle manager.

    Deprecated: This class is a backward-compatibility wrapper.
    New code should use Provider aggregate from mcp_hangar.domain.model.

    Manages the state machine: COLD → INITIALIZING → READY → DEGRADED → DEAD
    """

    def __init__(self, spec: ProviderSpec, event_bus=None):
        """Initialize manager with provider specification.

        Args:
            spec: Provider specification (deprecated, use Provider directly)
            event_bus: Optional event bus (uses global if not provided)
        """
        # Create the underlying Provider aggregate
        self._provider = Provider(
            provider_id=spec.provider_id,
            mode=spec.mode,
            command=spec.command,
            image=spec.image,
            endpoint=spec.endpoint,
            env=spec.env,
            idle_ttl_s=spec.idle_ttl_s,
            health_check_interval_s=spec.health_check_interval_s,
            max_consecutive_failures=spec.max_consecutive_failures,
        )
        self._event_bus = event_bus or get_event_bus()

        # Create a ProviderConnection for backward compatibility
        self.conn = ProviderConnection(spec=spec)

    def _sync_state_to_conn(self) -> None:
        """Synchronize Provider aggregate state to legacy ProviderConnection."""
        self.conn.state = self._provider.state
        self.conn.client = self._provider._client
        self.conn.last_used = self._provider.last_used

        # Sync health
        health = self._provider.health
        self.conn.health.consecutive_failures = health.consecutive_failures
        self.conn.health.last_success_at = health.last_success_at or 0.0
        self.conn.health.last_failure_at = health.last_failure_at or 0.0
        self.conn.health.total_invocations = health.total_invocations
        self.conn.health.total_failures = health.total_failures

        # Sync tools
        self.conn.tools = {}
        for tool in self._provider.tools:
            self.conn.tools[tool.name] = ToolSchema(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
            )

        # Sync meta
        self.conn.meta = self._provider.meta

    def _publish_events(self) -> None:
        """Publish collected events from the provider."""
        events = self._provider.collect_events()
        for event in events:
            try:
                self._event_bus.publish(event)
            except Exception as e:
                logger.error(f"Failed to publish event {event.__class__.__name__}: {e}")

    def ensure_ready(self) -> None:
        """
        Ensure provider is in READY state, starting if necessary.

        Raises:
            ProviderStartError: If provider fails to start
            ProviderDegradedError: If provider is degraded and backoff hasn't elapsed
        """
        from .domain.exceptions import CannotStartProviderError, ProviderDegradedError

        try:
            self._provider.ensure_ready()
        except CannotStartProviderError as e:
            # Convert to legacy exception format
            raise ProviderDegradedError(
                provider_id=e.provider_id,
                backoff_remaining=e.time_until_retry,
            ) from e
        finally:
            self._publish_events()
            self._sync_state_to_conn()

    def invoke_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """
        Invoke a tool with proper error handling and health tracking.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments
            timeout: Timeout in seconds

        Returns:
            Tool result

        Raises:
            ProviderDegradedError: If provider is degraded
            ToolInvocationError: If tool invocation fails
        """
        try:
            result = self._provider.invoke_tool(tool_name, arguments, timeout)
            return result
        finally:
            self._publish_events()
            self._sync_state_to_conn()

    def health_check(self) -> bool:
        """
        Perform active health check.

        Returns:
            True if healthy, False otherwise
        """
        try:
            result = self._provider.health_check()
            return result
        finally:
            self._publish_events()
            self._sync_state_to_conn()

    def maybe_shutdown_idle(self) -> bool:
        """
        Shutdown if idle past TTL.

        Returns:
            True if shutdown was performed, False otherwise
        """
        try:
            result = self._provider.maybe_shutdown_idle()
            return result
        finally:
            self._publish_events()
            self._sync_state_to_conn()

    def shutdown(self) -> None:
        """Explicit shutdown (public API)."""
        try:
            self._provider.shutdown()
        finally:
            self._publish_events()
            self._sync_state_to_conn()

    # --- Compatibility properties ---

    @property
    def provider(self) -> Provider:
        """Access the underlying Provider aggregate."""
        return self._provider

    @property
    def state(self) -> ProviderState:
        """Get current state."""
        return self._provider.state

    @property
    def is_alive(self) -> bool:
        """Check if provider client is alive."""
        return self._provider.is_alive

    @property
    def tools(self) -> Dict[str, ToolSchema]:
        """Get tools dictionary."""
        return self.conn.tools

    def get_tool_names(self):
        """Get list of tool names."""
        return self._provider.get_tool_names()

    def to_status_dict(self) -> Dict[str, Any]:
        """Get status as dictionary."""
        return self._provider.to_status_dict()
