"""CQRS command handlers for Provider and Group CRUD operations.

All handlers:
- Receive dependencies via constructor (Dependency Inversion Principle)
- Emit domain events via the event bus after successful mutations
- Raise domain exceptions on invalid operations (not found, duplicate, etc.)
- Are thread-safe (mutations guarded via repository or injected lock)
"""

from typing import Any

from ...domain.events import ProviderDeregistered, ProviderRegistered
from ...domain.exceptions import ProviderNotFoundError, ValidationError
from ...domain.model.provider import Provider
from ...domain.repository import IProviderRepository
from ...domain.value_objects import ProviderState
from ...infrastructure.command_bus import CommandHandler
from ...logging_config import get_logger
from .crud_commands import (
    CreateProviderCommand,
    DeleteProviderCommand,
    UpdateProviderCommand,
)

logger = get_logger(__name__)


# =============================================================================
# Provider CRUD Handlers
# =============================================================================


class CreateProviderHandler(CommandHandler):
    """Handler for CreateProviderCommand.

    Creates a new Provider in the repository and emits ProviderRegistered.
    Raises ValidationError if a provider with the same ID already exists.
    """

    def __init__(self, repository: IProviderRepository, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            repository: Provider repository for persistence.
            event_bus: Event bus for publishing domain events.
        """
        self._repository = repository
        self._event_bus = event_bus

    def handle(self, command: CreateProviderCommand) -> dict[str, Any]:
        """Create a new provider.

        Args:
            command: CreateProviderCommand with provider configuration.

        Returns:
            Dict with provider_id and created flag.

        Raises:
            ValidationError: If a provider with the same ID already exists.
        """
        if self._repository.exists(command.provider_id):
            raise ValidationError(f"Provider already exists: {command.provider_id}")

        provider = Provider(
            provider_id=command.provider_id,
            mode=command.mode,
            command=command.command,
            image=command.image,
            endpoint=command.endpoint,
            env=dict(command.env) if command.env else None,
            idle_ttl_s=command.idle_ttl_s,
            health_check_interval_s=command.health_check_interval_s,
            description=command.description,
        )
        self._repository.add(command.provider_id, provider)

        self._event_bus.publish(
            ProviderRegistered(
                provider_id=command.provider_id,
                source=command.source,
                mode=command.mode,
            )
        )

        logger.info(
            "provider_created",
            provider_id=command.provider_id,
            mode=command.mode,
            source=command.source,
        )
        return {"provider_id": command.provider_id, "created": True}


class UpdateProviderHandler(CommandHandler):
    """Handler for UpdateProviderCommand.

    Updates mutable configuration fields on an existing Provider via
    Provider.update_config(). Raises ProviderNotFoundError if not found.
    """

    def __init__(self, repository: IProviderRepository, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            repository: Provider repository for persistence.
            event_bus: Event bus for publishing domain events.
        """
        self._repository = repository
        self._event_bus = event_bus

    def _get_provider_or_raise(self, provider_id: str) -> Provider:
        """Retrieve provider or raise ProviderNotFoundError.

        Args:
            provider_id: ID of the provider to retrieve.

        Returns:
            Provider instance.

        Raises:
            ProviderNotFoundError: If no provider with given ID exists.
        """
        provider = self._repository.get(provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        return provider  # type: ignore[return-value]

    def handle(self, command: UpdateProviderCommand) -> dict[str, Any]:
        """Update provider configuration.

        Delegates field updates to Provider.update_config() which acquires the
        provider lock internally and records a ProviderUpdated event on the aggregate.

        Args:
            command: UpdateProviderCommand with fields to update.

        Returns:
            Dict with provider_id and updated flag.

        Raises:
            ProviderNotFoundError: If provider does not exist.
        """
        provider = self._get_provider_or_raise(command.provider_id)

        provider.update_config(
            description=command.description,
            env=command.env,
            idle_ttl_s=command.idle_ttl_s,
            health_check_interval_s=command.health_check_interval_s,
        )

        # Collect the ProviderUpdated event recorded by update_config()
        # and forward it through the event bus.
        for event in provider.collect_events():
            self._event_bus.publish(event)

        logger.info(
            "provider_updated",
            provider_id=command.provider_id,
            source=command.source,
        )
        return {"provider_id": command.provider_id, "updated": True}


class DeleteProviderHandler(CommandHandler):
    """Handler for DeleteProviderCommand.

    Stops a running provider (if not COLD or DEAD), then removes it from the
    repository and emits ProviderDeregistered.
    Raises ProviderNotFoundError if the provider does not exist.
    """

    def __init__(self, repository: IProviderRepository, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            repository: Provider repository for persistence.
            event_bus: Event bus for publishing domain events.
        """
        self._repository = repository
        self._event_bus = event_bus

    def _get_provider_or_raise(self, provider_id: str) -> Provider:
        """Retrieve provider or raise ProviderNotFoundError.

        Args:
            provider_id: ID of the provider to retrieve.

        Returns:
            Provider instance.

        Raises:
            ProviderNotFoundError: If no provider with given ID exists.
        """
        provider = self._repository.get(provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        return provider  # type: ignore[return-value]

    def handle(self, command: DeleteProviderCommand) -> dict[str, Any]:
        """Delete a provider, stopping it first if running.

        Args:
            command: DeleteProviderCommand with provider_id to delete.

        Returns:
            Dict with provider_id and deleted flag.

        Raises:
            ProviderNotFoundError: If provider does not exist.
        """
        provider = self._get_provider_or_raise(command.provider_id)

        # Stop provider if it is in a running state (not COLD or DEAD).
        # I/O (shutdown) is done outside the repository lock to respect
        # the no-I/O-under-lock rule from CLAUDE.md.
        if provider.state not in (ProviderState.COLD, ProviderState.DEAD):
            provider.shutdown()
            # Publish any lifecycle events emitted by shutdown()
            for event in provider.collect_events():
                self._event_bus.publish(event)

        self._repository.remove(command.provider_id)

        self._event_bus.publish(
            ProviderDeregistered(
                provider_id=command.provider_id,
                source=command.source,
            )
        )

        logger.info(
            "provider_deleted",
            provider_id=command.provider_id,
            source=command.source,
        )
        return {"provider_id": command.provider_id, "deleted": True}


# =============================================================================
# Registration
# =============================================================================


def register_crud_handlers(
    command_bus: Any,
    repository: IProviderRepository,
    event_bus: Any,
    groups: dict | None = None,
) -> None:
    """Register all provider CRUD command handlers with the command bus.

    Group CRUD handlers are added in plan 02 (CRUD-02). This function registers
    only the provider commands defined in CRUD-01.

    Args:
        command_bus: Command bus to register handlers on.
        repository: Provider repository for handler injection.
        event_bus: Event bus for handler injection.
        groups: Groups dict (reserved for plan 02 group handlers, unused here).
    """
    from .crud_commands import CreateProviderCommand, DeleteProviderCommand, UpdateProviderCommand

    command_bus.register(CreateProviderCommand, CreateProviderHandler(repository=repository, event_bus=event_bus))
    command_bus.register(UpdateProviderCommand, UpdateProviderHandler(repository=repository, event_bus=event_bus))
    command_bus.register(DeleteProviderCommand, DeleteProviderHandler(repository=repository, event_bus=event_bus))

    logger.info("crud_provider_handlers_registered")
