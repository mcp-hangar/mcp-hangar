"""Command handlers implementation."""

import time
from typing import Any

from ...domain.contracts.provider_runtime import ProviderRuntime
from ...domain.exceptions import ProviderNotFoundError
from ...domain.repository import IProviderRepository
from ...infrastructure.command_bus import CommandBus, CommandHandler
from ...infrastructure.event_bus import EventBus
from ...logging_config import get_logger
from ...metrics import observe_tool_call, record_error, record_provider_start, record_provider_stop
from .commands import (
    HealthCheckCommand,
    InvokeToolCommand,
    ShutdownIdleProvidersCommand,
    StartProviderCommand,
    StopProviderCommand,
)

logger = get_logger(__name__)


class BaseProviderHandler(CommandHandler):
    """Base class for handlers that work with providers."""

    def __init__(self, repository: IProviderRepository, event_bus: EventBus):
        self._repository = repository
        self._event_bus = event_bus

    def _get_provider(self, provider_id: str) -> ProviderRuntime:
        """Get provider or raise domain ProviderNotFoundError.

        Checks both static repository and runtime (hot-loaded) providers.
        """
        # First check static repository
        provider = self._repository.get(provider_id)
        if provider is not None:
            return provider

        # Then check runtime (hot-loaded) providers
        from ...server.state import get_runtime_providers

        runtime_store = get_runtime_providers()
        provider = runtime_store.get_provider(provider_id)
        if provider is not None:
            return provider

        raise ProviderNotFoundError(provider_id)

    def _publish_events(self, provider: ProviderRuntime) -> None:
        """Publish collected events from provider (no duck typing)."""
        for event in provider.collect_events():
            try:
                self._event_bus.publish(event)
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error(
                    "event_publish_failed",
                    event_type=type(event).__name__,
                    error=str(e),
                    exc_info=True,
                )


class StartProviderHandler(BaseProviderHandler):
    """Handler for StartProviderCommand."""

    def handle(self, command: StartProviderCommand) -> dict[str, Any]:
        """
        Start a provider.

        Returns:
            Dict with provider state and tools
        """
        provider = self._get_provider(command.provider_id)
        try:
            provider.ensure_ready()
            record_provider_start(command.provider_id, success=True)
        except Exception as e:
            record_provider_start(command.provider_id, success=False)
            record_error("provider", type(e).__name__)
            raise
        finally:
            self._publish_events(provider)

        return {
            "provider": command.provider_id,
            "state": provider.state.value,
            "tools": provider.get_tool_names(),
        }


class StopProviderHandler(BaseProviderHandler):
    """Handler for StopProviderCommand."""

    def handle(self, command: StopProviderCommand) -> dict[str, Any]:
        """
        Stop a provider.

        Returns:
            Confirmation dict
        """
        provider = self._get_provider(command.provider_id)
        provider.shutdown()
        record_provider_stop(command.provider_id, reason=command.reason or "manual")
        self._publish_events(provider)

        return {"stopped": command.provider_id, "reason": command.reason}


class InvokeToolHandler(BaseProviderHandler):
    """Handler for InvokeToolCommand."""

    def handle(self, command: InvokeToolCommand) -> dict[str, Any]:
        """
        Invoke a tool on a provider.

        Returns:
            Tool result
        """
        provider = self._get_provider(command.provider_id)

        start_time = time.perf_counter()
        error_type = None
        success = False

        try:
            result = provider.invoke_tool(command.tool_name, command.arguments, command.timeout)
            success = True
            return result

        except Exception as e:
            error_type = type(e).__name__
            raise

        finally:
            duration = time.perf_counter() - start_time
            observe_tool_call(
                provider=command.provider_id,
                tool=command.tool_name,
                duration=duration,
                success=success,
                error_type=error_type,
            )
            self._publish_events(provider)


class HealthCheckHandler(BaseProviderHandler):
    """Handler for HealthCheckCommand."""

    def handle(self, command: HealthCheckCommand) -> bool:
        """
        Perform health check on a provider.

        Returns:
            True if healthy, False otherwise
        """
        provider = self._get_provider(command.provider_id)
        result = provider.health_check()
        self._publish_events(provider)

        return result


class ShutdownIdleProvidersHandler(BaseProviderHandler):
    """Handler for ShutdownIdleProvidersCommand."""

    def handle(self, command: ShutdownIdleProvidersCommand) -> list[str]:
        """
        Shutdown all idle providers.

        Returns:
            List of provider IDs that were shutdown
        """
        shutdown_ids = []
        for provider_id, provider in self._repository.get_all().items():
            if provider.maybe_shutdown_idle():
                shutdown_ids.append(provider_id)
                self._publish_events(provider)

        return shutdown_ids


def register_all_handlers(command_bus: CommandBus, repository: IProviderRepository, event_bus: EventBus) -> None:
    """
    Register all command handlers with the command bus.

    Args:
        command_bus: The command bus to register handlers with
        repository: Provider repository
        event_bus: Event bus for publishing events
    """
    command_bus.register(StartProviderCommand, StartProviderHandler(repository, event_bus))
    command_bus.register(StopProviderCommand, StopProviderHandler(repository, event_bus))
    command_bus.register(InvokeToolCommand, InvokeToolHandler(repository, event_bus))
    command_bus.register(HealthCheckCommand, HealthCheckHandler(repository, event_bus))
    command_bus.register(
        ShutdownIdleProvidersCommand,
        ShutdownIdleProvidersHandler(repository, event_bus),
    )

    logger.info("command_handlers_registered")
