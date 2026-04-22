"""McpServer application service - orchestrates use cases."""

from typing import Any

from ...domain.exceptions import McpServerNotFoundError
from ...domain.model import McpServer
from ...domain.repository import IMcpServerRepository
from ...domain.contracts.event_bus import IEventBus
from ...logging_config import get_logger
from ...observability.tracing import get_tracer

logger = get_logger(__name__)


class McpServerService:
    """
    Application service for mcp_server operations.

    Orchestrates use cases by:
    - Loading mcp_servers from repository
    - Executing domain operations
    - Publishing collected domain events
    - Returning results
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: IEventBus,
    ):
        self._repository = repository
        self._event_bus = event_bus

    def _publish_events(self, mcp_server: McpServer) -> None:
        """Publish all collected events from mcp_server."""
        events = mcp_server.collect_events()
        for event in events:
            try:
                self._event_bus.publish(event)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: event publishing must not crash mcp_server operations
                logger.error(f"Failed to publish event {event.__class__.__name__}: {e}")

    def _get_mcp_server(self, mcp_server_id: str) -> McpServer:
        """Get mcp_server or raise McpServerNotFoundError."""
        mcp_server = self._repository.get(mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(mcp_server_id)
        return mcp_server

    # --- Use Cases ---

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """
        Use case: List all mcp_servers with their status.

        Returns:
            List of mcp_server status dictionaries
        """
        result = []
        for mcp_server_id, mcp_server in self._repository.get_all().items():
            result.append(mcp_server.to_status_dict())
        return result

    def start_mcp_server(self, mcp_server_id: str) -> dict[str, Any]:
        """
        Use case: Explicitly start a mcp_server.

        Ensures mcp_server is ready and returns its status.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            Dictionary with mcp_server state and tools

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist
        """
        mcp_server = self._get_mcp_server(mcp_server_id)
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("mcp_server.ensure_ready") as span:
            span.set_attribute("mcp.server.id", mcp_server_id)
            span.set_attribute("mcp_server.state_before", mcp_server.state.value)
            mcp_server.ensure_ready()
            span.set_attribute("mcp_server.state_after", mcp_server.state.value)
        self._publish_events(mcp_server)

        return {
            "mcp_server": mcp_server_id,
            "state": mcp_server.state.value,
            "tools": mcp_server.get_tool_names(),
        }

    def stop_mcp_server(self, mcp_server_id: str) -> dict[str, Any]:
        """
        Use case: Explicitly stop a mcp_server.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            Confirmation dictionary

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist
        """
        mcp_server = self._get_mcp_server(mcp_server_id)
        mcp_server.shutdown()
        self._publish_events(mcp_server)

        return {"stopped": mcp_server_id}

    def get_mcp_server_tools(self, mcp_server_id: str) -> dict[str, Any]:
        """
        Use case: Get detailed tool schemas for a mcp_server.

        Ensures mcp_server is ready before returning tools.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            Dictionary with mcp_server ID and tool schemas

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist
        """
        mcp_server = self._get_mcp_server(mcp_server_id)
        mcp_server.ensure_ready()
        self._publish_events(mcp_server)

        tools_list = []
        for tool in mcp_server.tools:
            tools_list.append(tool.to_dict())

        return {"mcp_server": mcp_server_id, "tools": tools_list}

    def invoke_tool(
        self,
        mcp_server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Use case: Invoke a tool on a mcp_server.

        Args:
            mcp_server_id: McpServer identifier
            tool_name: Tool name
            arguments: Tool arguments
            timeout: Timeout in seconds

        Returns:
            Tool result dictionary

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist
            ToolNotFoundError: If tool doesn't exist
            ToolInvocationError: If invocation fails
        """
        mcp_server = self._get_mcp_server(mcp_server_id)
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("mcp_server.invoke_tool") as span:
            span.set_attribute("mcp.server.id", mcp_server_id)
            span.set_attribute("mcp.tool.name", tool_name)
            span.set_attribute("mcp_server.timeout", timeout)
            result = mcp_server.invoke_tool(tool_name, arguments, timeout)
            span.set_attribute("mcp.tool.status", "success")
        self._publish_events(mcp_server)

        return result

    def health_check(self, mcp_server_id: str) -> bool:
        """
        Use case: Perform health check on a mcp_server.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            True if healthy, False otherwise

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist
        """
        mcp_server = self._get_mcp_server(mcp_server_id)
        healthy = mcp_server.health_check()
        self._publish_events(mcp_server)

        return healthy

    def check_all_health(self) -> dict[str, bool]:
        """
        Use case: Check health of all mcp_servers.

        Returns:
            Dictionary mapping mcp_server_id to health status
        """
        results = {}
        for mcp_server_id, mcp_server in self._repository.get_all().items():
            results[mcp_server_id] = mcp_server.health_check()
            self._publish_events(mcp_server)

        return results

    def shutdown_idle_mcp_servers(self) -> list[str]:
        """
        Use case: Shutdown all idle mcp_servers.

        Returns:
            List of mcp_server IDs that were shutdown
        """
        shutdown_ids = []
        for mcp_server_id, mcp_server in self._repository.get_all().items():
            if mcp_server.maybe_shutdown_idle():
                shutdown_ids.append(mcp_server_id)
                self._publish_events(mcp_server)

        return shutdown_ids
