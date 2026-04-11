"""Query handlers implementation."""

import time
from typing import Any

from ...domain.contracts.runtime_store import IRuntimeProviderStore
from ...domain.exceptions import ProviderNotFoundError
from ...domain.policies.provider_health import to_health_status_string
from ...domain.repository import IProviderRepository
from ...logging_config import get_logger
from ..ports.bus import IQueryBus
from ..read_models import HealthInfo, ProviderDetails, ProviderSummary, SystemMetrics, ToolInfo
from .queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetSystemMetricsQuery,
    GetToolInvocationHistoryQuery,
    ListProvidersQuery,
    QueryHandler,
)

logger = get_logger(__name__)


class BaseQueryHandler(QueryHandler):
    """Base class for query handlers."""

    def __init__(
        self,
        repository: IProviderRepository,
        runtime_store: IRuntimeProviderStore | None = None,
    ):
        self._repository = repository
        self._runtime_store = runtime_store

    def _get_provider(self, provider_id: str):
        """Get provider or raise ProviderNotFoundError.

        Checks both static repository and runtime (hot-loaded) providers.
        """
        # First check static repository
        provider = self._repository.get(provider_id)
        if provider is not None:
            return provider

        # Then check runtime (hot-loaded) providers
        if self._runtime_store is not None:
            provider = self._runtime_store.get_provider(provider_id)
            if provider is not None:
                return provider

        raise ProviderNotFoundError(provider_id)

    def _get_health_status(self, provider) -> str:
        """Determine health status string.

        Delegates classification to a domain policy to keep CQRS query layer free
        from business interpretation logic.
        """
        return to_health_status_string(
            state=provider.state,
            consecutive_failures=provider.health.consecutive_failures,
        )

    def _build_health_info(self, provider) -> HealthInfo:
        """Build HealthInfo from provider."""
        health = provider.health
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


class ListProvidersHandler(BaseQueryHandler):
    """Handler for ListProvidersQuery."""

    def handle(self, query: ListProvidersQuery) -> list[ProviderSummary]:
        """
        List all providers with optional state filtering.

        Returns:
            List of ProviderSummary
        """
        result = []
        for provider_id, provider in self._repository.get_all().items():
            state = provider.state.value

            # Apply filter if specified
            if query.state_filter and state != query.state_filter:
                continue

            summary = ProviderSummary(
                provider_id=provider_id,
                state=state,
                mode=provider.mode.value,
                is_alive=provider.is_alive,
                tools_count=provider.tools.count(),
                health_status=self._get_health_status(provider),
                description=provider.description,
                tools_predefined=provider.tools_predefined,
            )
            result.append(summary)

        return result


class GetProviderHandler(BaseQueryHandler):
    """Handler for GetProviderQuery."""

    def handle(self, query: GetProviderQuery) -> ProviderDetails:
        """
        Get detailed information about a provider.

        Returns:
            ProviderDetails
        """
        provider = self._get_provider(query.provider_id)

        tools = [self._build_tool_info(t) for t in provider.tools]
        health = self._build_health_info(provider)

        return ProviderDetails(
            provider_id=query.provider_id,
            state=provider.state.value,
            mode=provider.mode.value,
            is_alive=provider.is_alive,
            tools=tools,
            health=health,
            idle_time=provider.idle_time,
            meta=provider.meta,
        )


class GetProviderToolsHandler(BaseQueryHandler):
    """Handler for GetProviderToolsQuery."""

    def handle(self, query: GetProviderToolsQuery) -> list[ToolInfo]:
        """
        Get tools for a specific provider.

        Returns:
            List of ToolInfo
        """
        provider = self._get_provider(query.provider_id)
        return [self._build_tool_info(t) for t in provider.tools]


class GetProviderHealthHandler(BaseQueryHandler):
    """Handler for GetProviderHealthQuery."""

    def handle(self, query: GetProviderHealthQuery) -> HealthInfo:
        """
        Get health information for a provider.

        Returns:
            HealthInfo
        """
        provider = self._get_provider(query.provider_id)
        return self._build_health_info(provider)


class GetSystemMetricsHandler(BaseQueryHandler):
    """Handler for GetSystemMetricsQuery."""

    def handle(self, query: GetSystemMetricsQuery) -> SystemMetrics:
        """
        Get system-wide metrics.

        Returns:
            SystemMetrics
        """
        providers = self._repository.get_all()

        total_providers = len(providers)
        providers_by_state: dict[str, int] = {}
        total_tools = 0
        total_invocations = 0
        total_failures = 0

        for provider in providers.values():
            # Count by state
            state = provider.state.value
            providers_by_state[state] = providers_by_state.get(state, 0) + 1

            # Sum metrics
            total_tools += provider.tools.count()
            total_invocations += provider.health.total_invocations
            total_failures += provider.health.total_failures

        # Calculate overall success rate
        if total_invocations > 0:
            overall_success_rate = (total_invocations - total_failures) / total_invocations
        else:
            overall_success_rate = 1.0

        return SystemMetrics(
            total_providers=total_providers,
            providers_by_state=providers_by_state,
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
        """Get tool invocation history for a provider from the event store.

        Reads all streams matching the provider's stream ID and filters for
        ToolInvocationCompleted and ToolInvocationFailed events.

        Returns:
            Dict with provider_id, history list, and total count.
        """
        if self._event_store is not None:
            event_store = self._event_store
        else:
            from ...infrastructure.event_store import get_event_store

            event_store = get_event_store()
        target_stream_id = f"provider-{query.provider_id}"
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
            "provider_id": query.provider_id,
            "history": history,
            "total": len(history),
        }


def register_all_handlers(
    query_bus: IQueryBus,
    repository: IProviderRepository,
    runtime_store: IRuntimeProviderStore | None = None,
    event_store: Any = None,
) -> None:
    """
    Register all query handlers with the query bus.

    Args:
        query_bus: The query bus to register handlers with
        repository: Provider repository
        runtime_store: Optional runtime provider store for hot-loaded provider lookup
        event_store: Optional event store for tool invocation history
    """
    query_bus.register(ListProvidersQuery, ListProvidersHandler(repository, runtime_store))
    query_bus.register(GetProviderQuery, GetProviderHandler(repository, runtime_store))
    query_bus.register(GetProviderToolsQuery, GetProviderToolsHandler(repository, runtime_store))
    query_bus.register(GetProviderHealthQuery, GetProviderHealthHandler(repository, runtime_store))
    query_bus.register(GetSystemMetricsQuery, GetSystemMetricsHandler(repository, runtime_store))
    query_bus.register(GetToolInvocationHistoryQuery, GetToolInvocationHistoryHandler(event_store))

    logger.info("query_handlers_registered")
