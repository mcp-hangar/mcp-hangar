"""CQRS command handlers for Provider and Group CRUD operations.

All handlers:
- Receive dependencies via constructor (Dependency Inversion Principle)
- Emit domain events via the event bus after successful mutations
- Raise domain exceptions on invalid operations (not found, duplicate, etc.)
- Are thread-safe (mutations guarded via repository or injected lock)
"""

import threading
from typing import Any

from ...domain.events import ProviderDeregistered, ProviderRegistered
from ...domain.exceptions import ProviderNotFoundError, ValidationError
from ...domain.model.provider import Provider
from ...domain.model.provider_group import GroupDeleted, ProviderGroup
from ...domain.repository import IProviderRepository
from ...domain.value_objects import LoadBalancerStrategy, ProviderState
from ...domain.contracts.command import CommandHandler
from ...logging_config import get_logger
from .crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateProviderCommand,
    DeleteGroupCommand,
    DeleteProviderCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
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
# Group CRUD Handlers
# =============================================================================


class CreateGroupHandler(CommandHandler):
    """Handler for CreateGroupCommand.

    Creates a new ProviderGroup in the groups dict and emits GroupCreated.
    Raises ValidationError if a group with the same ID already exists.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to ProviderGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: CreateGroupCommand) -> dict[str, Any]:
        """Create a new provider group.

        Args:
            command: CreateGroupCommand with group configuration.

        Returns:
            Dict with group_id and created flag.

        Raises:
            ValidationError: If a group with the same ID already exists.
        """
        with self._lock:
            if command.group_id in self._groups:
                raise ValidationError(f"Group already exists: {command.group_id}")
            group = ProviderGroup(
                group_id=command.group_id,
                strategy=LoadBalancerStrategy(command.strategy),
                min_healthy=command.min_healthy,
                description=command.description,
            )
            self._groups[command.group_id] = group

        # Publish events OUTSIDE lock (no I/O under lock)
        for event in group.collect_events():
            self._event_bus.publish(event)

        logger.info("group_created", group_id=command.group_id, strategy=command.strategy)
        return {"group_id": command.group_id, "created": True}


class UpdateGroupHandler(CommandHandler):
    """Handler for UpdateGroupCommand.

    Updates mutable configuration fields on an existing ProviderGroup via
    ProviderGroup.update(). Raises ProviderNotFoundError if not found.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to ProviderGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: UpdateGroupCommand) -> dict[str, Any]:
        """Update group configuration.

        Delegates field updates to ProviderGroup.update() which acquires the
        group lock internally and records a GroupUpdated event on the aggregate.

        Args:
            command: UpdateGroupCommand with fields to update.

        Returns:
            Dict with group_id and updated flag.

        Raises:
            ProviderNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise ProviderNotFoundError(command.group_id)

        # group.update() acquires its own lock internally
        group.update(
            strategy=command.strategy,
            description=command.description,
            min_healthy=command.min_healthy,
        )

        # Collect the GroupUpdated event and forward through event bus
        for event in group.collect_events():
            self._event_bus.publish(event)

        logger.info("group_updated", group_id=command.group_id, source=command.source)
        return {"group_id": command.group_id, "updated": True}


class DeleteGroupHandler(CommandHandler):
    """Handler for DeleteGroupCommand.

    Removes a ProviderGroup from the groups dict, calls stop_all() on the
    group (outside the lock), and emits GroupDeleted.
    Raises ProviderNotFoundError if not found.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to ProviderGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: DeleteGroupCommand) -> dict[str, Any]:
        """Delete a provider group, stopping all members first.

        Args:
            command: DeleteGroupCommand with group_id to delete.

        Returns:
            Dict with group_id and deleted flag.

        Raises:
            ProviderNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise ProviderNotFoundError(command.group_id)
            del self._groups[command.group_id]

        # I/O (stop) outside lock — stop_all() acquires Provider locks individually
        group.stop_all()

        # Collect any lifecycle events from stop_all(), then emit GroupDeleted
        for event in group.collect_events():
            self._event_bus.publish(event)
        self._event_bus.publish(GroupDeleted(group_id=command.group_id))

        logger.info("group_deleted", group_id=command.group_id, source=command.source)
        return {"group_id": command.group_id, "deleted": True}


class AddGroupMemberHandler(CommandHandler):
    """Handler for AddGroupMemberCommand.

    Adds a Provider to an existing ProviderGroup.
    Raises ProviderNotFoundError if provider or group does not exist.
    Thread-safe via a per-handler threading.Lock for the groups dict lookup.
    """

    def __init__(self, repository: IProviderRepository, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            repository: Provider repository for member lookup.
            groups: Shared groups dict mapping group_id to ProviderGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._repository = repository
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: AddGroupMemberCommand) -> dict[str, Any]:
        """Add a provider to a group.

        Looks up provider first (outside lock), then acquires lock to
        find the group and call group.add_member().

        Args:
            command: AddGroupMemberCommand with group_id and provider_id.

        Returns:
            Dict with group_id, provider_id, and added flag.

        Raises:
            ProviderNotFoundError: If provider or group does not exist.
        """
        # Provider lookup outside lock (read-only, thread-safe via repository)
        provider = self._repository.get(command.provider_id)
        if provider is None:
            raise ProviderNotFoundError(command.provider_id)

        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise ProviderNotFoundError(command.group_id)
            # group.add_member() acquires group's own lock internally
            group.add_member(provider, weight=command.weight, priority=command.priority)

        # Collect GroupMemberAdded event and forward
        for event in group.collect_events():
            self._event_bus.publish(event)

        logger.info("group_member_added", group_id=command.group_id, provider_id=command.provider_id)
        return {"group_id": command.group_id, "provider_id": command.provider_id, "added": True}


class RemoveGroupMemberHandler(CommandHandler):
    """Handler for RemoveGroupMemberCommand.

    Removes a Provider from an existing ProviderGroup.
    Raises ProviderNotFoundError if the group does not exist.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to ProviderGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: RemoveGroupMemberCommand) -> dict[str, Any]:
        """Remove a provider from a group.

        Args:
            command: RemoveGroupMemberCommand with group_id and provider_id.

        Returns:
            Dict with group_id, provider_id, and removed flag.

        Raises:
            ProviderNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise ProviderNotFoundError(command.group_id)
            # group.remove_member() acquires group's own lock internally
            group.remove_member(command.provider_id)

        # Collect GroupMemberRemoved event and forward
        for event in group.collect_events():
            self._event_bus.publish(event)

        logger.info("group_member_removed", group_id=command.group_id, provider_id=command.provider_id)
        return {"group_id": command.group_id, "provider_id": command.provider_id, "removed": True}


# =============================================================================
# Registration
# =============================================================================


def register_crud_handlers(
    command_bus: Any,
    repository: IProviderRepository,
    event_bus: Any,
    groups: dict | None = None,
) -> None:
    """Register all provider and group CRUD command handlers with the command bus.

    Args:
        command_bus: Command bus to register handlers on.
        repository: Provider repository for handler injection.
        event_bus: Event bus for handler injection.
        groups: Groups dict for group handler injection. If None, group handlers
            are not registered.
    """
    # Provider handlers
    command_bus.register(CreateProviderCommand, CreateProviderHandler(repository=repository, event_bus=event_bus))
    command_bus.register(UpdateProviderCommand, UpdateProviderHandler(repository=repository, event_bus=event_bus))
    command_bus.register(DeleteProviderCommand, DeleteProviderHandler(repository=repository, event_bus=event_bus))

    # Group handlers (require groups dict)
    if groups is not None:
        command_bus.register(CreateGroupCommand, CreateGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(UpdateGroupCommand, UpdateGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(DeleteGroupCommand, DeleteGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(
            AddGroupMemberCommand,
            AddGroupMemberHandler(repository=repository, groups=groups, event_bus=event_bus),
        )
        command_bus.register(
            RemoveGroupMemberCommand,
            RemoveGroupMemberHandler(groups=groups, event_bus=event_bus),
        )
        logger.info("crud_group_handlers_registered")

    logger.info("crud_provider_handlers_registered")
