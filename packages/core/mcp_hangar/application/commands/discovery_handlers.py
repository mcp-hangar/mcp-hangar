"""CQRS command handlers for discovery source management.

All handlers:
- Receive DiscoveryRegistry via constructor (Dependency Inversion Principle)
- Are thread-safe via threading.Lock
- Raise ProviderNotFoundError on missing source_id (maps to HTTP 404 via middleware)
- Return plain dicts for JSON serialization via HangarJSONResponse
"""

import threading
import uuid
from typing import Any

from ...domain.discovery.discovery_source import DiscoveryMode
from ...domain.exceptions import ProviderNotFoundError
from ...domain.value_objects.discovery import DiscoverySourceSpec
from ...domain.contracts.command import CommandHandler
from ...logging_config import get_logger
from ..discovery.discovery_registry import DiscoveryRegistry
from .discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
    TriggerSourceScanCommand,
    UpdateDiscoverySourceCommand,
)

logger = get_logger(__name__)


class RegisterDiscoverySourceHandler(CommandHandler):
    """Handler for RegisterDiscoverySourceCommand.

    Generates a UUID source_id, creates a DiscoverySourceSpec, and registers
    it in the DiscoveryRegistry. Does NOT start the underlying source.
    """

    def __init__(self, registry: DiscoveryRegistry) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry to register sources in.
        """
        self._registry = registry
        self._lock = threading.Lock()

    def handle(self, command: RegisterDiscoverySourceCommand) -> dict[str, Any]:
        """Register a new discovery source.

        Args:
            command: RegisterDiscoverySourceCommand with source configuration.

        Returns:
            Dict with source_id and registered flag.
        """
        source_id = str(uuid.uuid4())
        spec = DiscoverySourceSpec(
            source_id=source_id,
            source_type=command.source_type,
            mode=DiscoveryMode(command.mode),
            enabled=command.enabled,
            config=dict(command.config) if command.config else {},
        )
        self._registry.register_source(spec)
        logger.info(
            "discovery_source_registered",
            source_id=source_id,
            source_type=command.source_type,
            mode=command.mode,
        )
        return {"source_id": source_id, "registered": True}


class UpdateDiscoverySourceHandler(CommandHandler):
    """Handler for UpdateDiscoverySourceCommand.

    Applies non-None field updates to an existing DiscoverySourceSpec using
    DiscoveryRegistry.update_source(). Only fields explicitly set in the command
    (non-None) are forwarded — absent fields leave the spec unchanged.
    """

    def __init__(self, registry: DiscoveryRegistry) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry containing the spec to update.
        """
        self._registry = registry
        self._lock = threading.Lock()

    def handle(self, command: UpdateDiscoverySourceCommand) -> dict[str, Any]:
        """Update an existing discovery source spec.

        Args:
            command: UpdateDiscoverySourceCommand with fields to update.

        Returns:
            Dict with source_id and updated flag.

        Raises:
            ProviderNotFoundError: If source_id is not registered.
        """
        kwargs: dict[str, Any] = {}
        if command.mode is not None:
            kwargs["mode"] = DiscoveryMode(command.mode)
        if command.enabled is not None:
            kwargs["enabled"] = command.enabled
        if command.config is not None:
            kwargs["config"] = dict(command.config)

        try:
            self._registry.update_source(command.source_id, **kwargs)
        except KeyError:
            raise ProviderNotFoundError(provider_id=command.source_id)

        logger.info("discovery_source_updated", source_id=command.source_id)
        return {"source_id": command.source_id, "updated": True}


class DeregisterDiscoverySourceHandler(CommandHandler):
    """Handler for DeregisterDiscoverySourceCommand.

    Removes a DiscoverySourceSpec from the registry.
    Raises ProviderNotFoundError (HTTP 404) if the source_id is unknown.
    """

    def __init__(self, registry: DiscoveryRegistry) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry to remove the source from.
        """
        self._registry = registry
        self._lock = threading.Lock()

    def handle(self, command: DeregisterDiscoverySourceCommand) -> dict[str, Any]:
        """Remove a discovery source from the registry.

        Args:
            command: DeregisterDiscoverySourceCommand with source_id to remove.

        Returns:
            Dict with source_id and deregistered flag.

        Raises:
            ProviderNotFoundError: If source_id is not registered.
        """
        try:
            self._registry.unregister_source(command.source_id)
        except KeyError:
            raise ProviderNotFoundError(provider_id=command.source_id)
        logger.info("discovery_source_deregistered", source_id=command.source_id)
        return {"source_id": command.source_id, "deregistered": True}


class TriggerSourceScanHandler(CommandHandler):
    """Handler for TriggerSourceScanCommand.

    Verifies the source exists, then triggers an immediate discovery scan via
    DiscoveryOrchestrator.trigger_discovery(). Returns the number of providers
    discovered in the scan cycle.
    """

    def __init__(self, registry: DiscoveryRegistry) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry; its orchestrator is used for scanning.
        """
        self._registry = registry
        self._lock = threading.Lock()

    def handle(self, command: TriggerSourceScanCommand) -> dict[str, Any]:
        """Trigger an immediate discovery scan.

        Args:
            command: TriggerSourceScanCommand with source_id to scan.

        Returns:
            Dict with source_id, scan_triggered flag, and providers_found count.

        Raises:
            ProviderNotFoundError: If source_id is not registered.
        """
        spec = self._registry.get_source(command.source_id)
        if spec is None:
            raise ProviderNotFoundError(provider_id=command.source_id)

        result = self._registry.orchestrator.trigger_discovery()
        providers_found = (
            result.get("providers_discovered", 0)
            if isinstance(result, dict)
            else getattr(result, "providers_discovered", 0)
        )

        logger.info(
            "discovery_scan_triggered",
            source_id=command.source_id,
            providers_found=providers_found,
        )
        return {
            "source_id": command.source_id,
            "scan_triggered": True,
            "providers_found": providers_found,
        }


class ToggleDiscoverySourceHandler(CommandHandler):
    """Handler for ToggleDiscoverySourceCommand.

    Enables or disables a DiscoverySourceSpec in the registry by calling
    enable_source() or disable_source() accordingly.
    """

    def __init__(self, registry: DiscoveryRegistry) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry containing the spec to toggle.
        """
        self._registry = registry
        self._lock = threading.Lock()

    def handle(self, command: ToggleDiscoverySourceCommand) -> dict[str, Any]:
        """Enable or disable a discovery source.

        Args:
            command: ToggleDiscoverySourceCommand with source_id and enabled flag.

        Returns:
            Dict with source_id and the resulting enabled state.

        Raises:
            ProviderNotFoundError: If source_id is not registered.
        """
        try:
            if command.enabled:
                spec = self._registry.enable_source(command.source_id)
            else:
                spec = self._registry.disable_source(command.source_id)
        except KeyError:
            raise ProviderNotFoundError(provider_id=command.source_id)

        logger.info(
            "discovery_source_toggled",
            source_id=command.source_id,
            enabled=spec.enabled,
        )
        return {"source_id": command.source_id, "enabled": spec.enabled}


def register_discovery_handlers(command_bus: Any, registry: DiscoveryRegistry) -> None:
    """Register all discovery source command handlers with the command bus.

    Args:
        command_bus: Command bus to register handlers on.
        registry: DiscoveryRegistry instance for handler injection.
    """
    command_bus.register(RegisterDiscoverySourceCommand, RegisterDiscoverySourceHandler(registry=registry))
    command_bus.register(UpdateDiscoverySourceCommand, UpdateDiscoverySourceHandler(registry=registry))
    command_bus.register(DeregisterDiscoverySourceCommand, DeregisterDiscoverySourceHandler(registry=registry))
    command_bus.register(TriggerSourceScanCommand, TriggerSourceScanHandler(registry=registry))
    command_bus.register(ToggleDiscoverySourceCommand, ToggleDiscoverySourceHandler(registry=registry))
    logger.info("discovery_handlers_registered")
