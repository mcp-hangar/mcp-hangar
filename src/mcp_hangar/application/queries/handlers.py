"""Query handlers implementation."""

import time
from typing import Any

from ...domain.contracts.runtime_store import IRuntimeMcpServerStore
from ...domain.exceptions import McpServerNotFoundError
from ...domain.policies.mcp_server_health import to_health_status_string
from ...domain.repository import IMcpServerRepository
from ...logging_config import get_logger
from ..ports.bus import IQueryBus
from ..read_models import HealthInfo, McpServerDetails, McpServerSummary, SystemMetrics, ToolInfo
from .queries import (
    GetMcpServerHealthQuery,
    GetMcpServerQuery,
    GetMcpServerToolsQuery,
    GetSystemMetricsQuery,
    GetToolInvocationHistoryQuery,
    ListMcpServersQuery,
    QueryHandler,
)

logger = get_logger(__name__)


class BaseQueryHandler(QueryHandler):
    """Base class for query handlers."""

    def __init__(
        self,
        repository: IMcpServerRepository,
        runtime_store: IRuntimeMcpServerStore | None = None,
    ):
        self._repository = repository
        self._runtime_store = runtime_store

    def _get_mcp_server(self, mcp_server_id: str):
        """Get mcp_server or raise McpServerNotFoundError.

        Checks both static repository and runtime (hot-loaded) mcp_servers.
        """
        # First check static repository
        mcp_server = self._repository.get(mcp_server_id)
        if mcp_server is not None:
            return mcp_server

        # Then check runtime (hot-loaded) mcp_servers
        if self._runtime_store is not None:
            mcp_server = self._runtime_store.get_mcp_server(mcp_server_id)
            if mcp_server is not None:
                return mcp_server

        raise McpServerNotFoundError(mcp_server_id)

    def _get_health_status(self, mcp_server) -> str:
        """Determine health status string.

        Delegates classification to a domain policy to keep CQRS query layer free
        from business interpretation logic.
        """
        return to_health_status_string(
            state=mcp_server.state,
            consecutive_failures=mcp_server.health.consecutive_failures,
        )

    def _build_health_info(self, mcp_server) -> HealthInfo:
        """Build HealthInfo from mcp_server."""
        health = mcp_server.health
        now = time.time()

        last_success_ago = None
        if health.last_success_at:
            last_success_ago = now - health.last_success_at

        last_failure_ago = None
        if health.last_failure_at:
            last_failure_ago = now - health.last_failure_at

        return HealthInfo(
            consecutive_failures=health.consecutive_failures,
            total_invocations=health.total_invocations,
            total_failures=health.total_failures,
            success_rate=health.success_rate,
            can_retry=health.can_retry(),
            last_success_ago=last_success_ago,
            last_failure_ago=last_failure_ago,
        )

    def _build_tool_info(self, tool) -> ToolInfo:
        """Build ToolInfo from tool schema."""
        return ToolInfo(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
        )


class ListMcpServersHandler(BaseQueryHandler):
    """Handler for ListMcpServersQuery."""

    def handle(self, query: ListMcpServersQuery) -> list[McpServerSummary]:
        """
        List all mcp_servers with optional state filtering.

        Returns:
            List of McpServerSummary
        """
        result = []
        for mcp_server_id, mcp_server in self._repository.get_all().items():
            state = mcp_server.state.value

            # Apply filter if specified
            if query.state_filter and state != query.state_filter:
                continue

            summary = McpServerSummary(
                mcp_server_id=mcp_server_id,
                state=state,
                mode=mcp_server.mode.value,
                is_alive=mcp_server.is_alive,
                tools_count=mcp_server.tools.count(),
                health_status=self._get_health_status(mcp_server),
                description=mcp_server.description,
                tools_predefined=mcp_server.tools_predefined,
            )
            result.append(summary)

        return result


class GetMcpServerHandler(BaseQueryHandler):
    """Handler for GetMcpServerQuery."""

    def handle(self, query: GetMcpServerQuery) -> McpServerDetails:
        """
        Get detailed information about a mcp_server.

        Returns:
            McpServerDetails
        """
        mcp_server = self._get_mcp_server(query.mcp_server_id)

        tools = [self._build_tool_info(t) for t in mcp_server.tools]
        health = self._build_health_info(mcp_server)

        return McpServerDetails(
            mcp_server_id=query.mcp_server_id,
            state=mcp_server.state.value,
            mode=mcp_server.mode.value,
            is_alive=mcp_server.is_alive,
            tools=tools,
            health=health,
            idle_time=mcp_server.idle_time,
            meta=mcp_server.meta,
        )


class GetMcpServerToolsHandler(BaseQueryHandler):
    """Handler for GetMcpServerToolsQuery."""

    def handle(self, query: GetMcpServerToolsQuery) -> list[ToolInfo]:
        """
        Get tools for a specific mcp_server.

        Returns:
            List of ToolInfo
        """
        mcp_server = self._get_mcp_server(query.mcp_server_id)
        return [self._build_tool_info(t) for t in mcp_server.tools]


class GetMcpServerHealthHandler(BaseQueryHandler):
    """Handler for GetMcpServerHealthQuery."""

    def handle(self, query: GetMcpServerHealthQuery) -> HealthInfo:
        """
        Get health information for a mcp_server.

        Returns:
            HealthInfo
        """
        mcp_server = self._get_mcp_server(query.mcp_server_id)
        return self._build_health_info(mcp_server)


class GetSystemMetricsHandler(BaseQueryHandler):
    """Handler for GetSystemMetricsQuery."""

    def handle(self, query: GetSystemMetricsQuery) -> SystemMetrics:
        """
        Get system-wide metrics.

        Returns:
            SystemMetrics
        """
        mcp_servers = self._repository.get_all()

        total_mcp_servers = len(mcp_servers)
        mcp_servers_by_state: dict[str, int] = {}
        total_tools = 0
        total_invocations = 0
        total_failures = 0

        for mcp_server in mcp_servers.values():
            # Count by state
            state = mcp_server.state.value
            mcp_servers_by_state[state] = mcp_servers_by_state.get(state, 0) + 1

            # Sum metrics
            total_tools += mcp_server.tools.count()
            total_invocations += mcp_server.health.total_invocations
            total_failures += mcp_server.health.total_failures

        # Calculate overall success rate
        if total_invocations > 0:
            overall_success_rate = (total_invocations - total_failures) / total_invocations
        else:
            overall_success_rate = 1.0

        return SystemMetrics(
            total_mcp_servers=total_mcp_servers,
            mcp_servers_by_state=mcp_servers_by_state,
            total_tools=total_tools,
            total_invocations=total_invocations,
            total_failures=total_failures,
            overall_success_rate=overall_success_rate,
        )


class GetToolInvocationHistoryHandler(QueryHandler):
    """Handler for GetToolInvocationHistoryQuery."""

    def __init__(self, event_store: Any = None):
        """Initialize the handler.

        Args:
            event_store: Optional event store instance for reading invocation history.
                Injected from bootstrap; falls back to global singleton if None.
        """
        self._event_store = event_store

    def handle(self, query: GetToolInvocationHistoryQuery) -> dict:
        """Get tool invocation history for a mcp_server from the event store.

        Reads all streams matching the mcp_server's stream ID and filters for
        ToolInvocationCompleted and ToolInvocationFailed events.

        Returns:
            Dict with mcp_server_id, history list, and total count.
        """
        if self._event_store is not None:
            event_store = self._event_store
        else:
            from ...infrastructure.event_store import get_event_store

            event_store = get_event_store()
        target_stream_id = f"mcp_server-{query.mcp_server_id}"
        tool_event_types = {"ToolInvocationCompleted", "ToolInvocationFailed"}
        limit = min(max(1, query.limit), 500)

        history = []
        if event_store.stream_exists(target_stream_id):
            events = event_store.load(target_stream_id)
            for stored_event in events:
                if stored_event.event_type not in tool_event_types:
                    continue
                if stored_event.version <= query.from_position:
                    continue
                history.append(stored_event.to_dict())
                if len(history) >= limit:
                    break

        return {
            "mcp_server_id": query.mcp_server_id,
            "history": history,
            "total": len(history),
        }


def register_all_handlers(
    query_bus: IQueryBus,
    repository: IMcpServerRepository,
    runtime_store: IRuntimeMcpServerStore | None = None,
    event_store: Any = None,
) -> None:
    """
    Register all query handlers with the query bus.

    Args:
        query_bus: The query bus to register handlers with
        repository: McpServer repository
        runtime_store: Optional runtime mcp_server store for hot-loaded mcp_server lookup
        event_store: Optional event store for tool invocation history
    """
    query_bus.register(ListMcpServersQuery, ListMcpServersHandler(repository, runtime_store))
    query_bus.register(GetMcpServerQuery, GetMcpServerHandler(repository, runtime_store))
    query_bus.register(GetMcpServerToolsQuery, GetMcpServerToolsHandler(repository, runtime_store))
    query_bus.register(GetMcpServerHealthQuery, GetMcpServerHealthHandler(repository, runtime_store))
    query_bus.register(GetSystemMetricsQuery, GetSystemMetricsHandler(repository, runtime_store))
    query_bus.register(GetToolInvocationHistoryQuery, GetToolInvocationHistoryHandler(event_store))

    logger.info("query_handlers_registered")
