"""Provider application service - orchestrates use cases."""

from typing import Any

from ...domain.exceptions import ProviderNotFoundError
from ...domain.model import Provider
from ...domain.repository import IProviderRepository
from ...infrastructure.event_bus import EventBus
from ...logging_config import get_logger

logger = get_logger(__name__)


class ProviderService:
    """
    Application service for provider operations.

    Orchestrates use cases by:
    - Loading providers from repository
    - Executing domain operations
    - Publishing collected domain events
    - Returning results
    """

    def __init__(
        self,
        repository: IProviderRepository,
        event_bus: EventBus,
    ):
        self._repository = repository
        self._event_bus = event_bus

    def _publish_events(self, provider: Provider) -> None:
        """Publish all collected events from provider."""
        events = provider.collect_events()
        for event in events:
            try:
                self._event_bus.publish(event)
            except Exception as e:
                logger.error(f"Failed to publish event {event.__class__.__name__}: {e}")

    def _get_provider(self, provider_id: str) -> Provider:
        """Get provider or raise ProviderNotFoundError."""
        provider = self._repository.get(provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        return provider

    # --- Use Cases ---

    def list_providers(self) -> list[dict[str, Any]]:
        """
        Use case: List all providers with their status.

        Returns:
            List of provider status dictionaries
        """
        result = []
        for provider_id, provider in self._repository.get_all().items():
            result.append(provider.to_status_dict())
        return result

    def start_provider(self, provider_id: str) -> dict[str, Any]:
        """
        Use case: Explicitly start a provider.

        Ensures provider is ready and returns its status.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with provider state and tools

        Raises:
            ProviderNotFoundError: If provider doesn't exist
        """
        provider = self._get_provider(provider_id)
        provider.ensure_ready()
        self._publish_events(provider)

        return {
            "provider": provider_id,
            "state": provider.state.value,
            "tools": provider.get_tool_names(),
        }

    def stop_provider(self, provider_id: str) -> dict[str, Any]:
        """
        Use case: Explicitly stop a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Confirmation dictionary

        Raises:
            ProviderNotFoundError: If provider doesn't exist
        """
        provider = self._get_provider(provider_id)
        provider.shutdown()
        self._publish_events(provider)

        return {"stopped": provider_id}

    def get_provider_tools(self, provider_id: str) -> dict[str, Any]:
        """
        Use case: Get detailed tool schemas for a provider.

        Ensures provider is ready before returning tools.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with provider ID and tool schemas

        Raises:
            ProviderNotFoundError: If provider doesn't exist
        """
        provider = self._get_provider(provider_id)
        provider.ensure_ready()
        self._publish_events(provider)

        tools_list = []
        for tool in provider.tools:
            tools_list.append(tool.to_dict())

        return {"provider": provider_id, "tools": tools_list}

    def invoke_tool(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Use case: Invoke a tool on a provider.

        Args:
            provider_id: Provider identifier
            tool_name: Tool name
            arguments: Tool arguments
            timeout: Timeout in seconds

        Returns:
            Tool result dictionary

        Raises:
            ProviderNotFoundError: If provider doesn't exist
            ToolNotFoundError: If tool doesn't exist
            ToolInvocationError: If invocation fails
        """
        provider = self._get_provider(provider_id)
        result = provider.invoke_tool(tool_name, arguments, timeout)
        self._publish_events(provider)

        return result

    def health_check(self, provider_id: str) -> bool:
        """
        Use case: Perform health check on a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if healthy, False otherwise

        Raises:
            ProviderNotFoundError: If provider doesn't exist
        """
        provider = self._get_provider(provider_id)
        healthy = provider.health_check()
        self._publish_events(provider)

        return healthy

    def check_all_health(self) -> dict[str, bool]:
        """
        Use case: Check health of all providers.

        Returns:
            Dictionary mapping provider_id to health status
        """
        results = {}
        for provider_id, provider in self._repository.get_all().items():
            results[provider_id] = provider.health_check()
            self._publish_events(provider)

        return results

    def shutdown_idle_providers(self) -> list[str]:
        """
        Use case: Shutdown all idle providers.

        Returns:
            List of provider IDs that were shutdown
        """
        shutdown_ids = []
        for provider_id, provider in self._repository.get_all().items():
            if provider.maybe_shutdown_idle():
                shutdown_ids.append(provider_id)
                self._publish_events(provider)

        return shutdown_ids
