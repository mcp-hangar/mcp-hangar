"""CQRS command handlers for discovery source management.

All handlers:
- Receive DiscoveryRegistry via constructor (Dependency Inversion Principle)
- Are thread-safe via threading.Lock
- Raise McpServerNotFoundError on missing source_id (maps to HTTP 404 via middleware)
- Return plain dicts for JSON serialization via HangarJSONResponse
"""

import threading
import uuid
from collections.abc import Callable
from typing import Any

from ...domain.contracts.command import CommandHandler
from ...domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from ...domain.exceptions import McpServerNotFoundError, ValidationError
from ...domain.value_objects.discovery import DiscoverySourceSpec, config_source_id
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


#: Builds the source a registered spec describes: `(source_type, config) -> DiscoverySource`,
#: where config carries the spec's `mode`. Injected, because building one is
#: infrastructure (`infrastructure/discovery/registry.create_source`).
SourceFactory = Callable[[str, dict[str, Any]], DiscoverySource]


class RegisterDiscoverySourceHandler(CommandHandler):
    """Handler for RegisterDiscoverySourceCommand.

    Generates a UUID source_id, creates a DiscoverySourceSpec, builds the source
    it describes and adds it to the running orchestrator, so the spec is
    listed and scanned and not only stored (#1674). Without a factory (a
    registry used on its own) the spec is stored and nothing is built.
    """

    def __init__(self, registry: DiscoveryRegistry, source_factory: SourceFactory | None = None) -> None:
        """Initialize the handler.

        Args:
            registry: DiscoveryRegistry to register sources in.
            source_factory: Builds the live source for a spec; None stores the spec only.
        """
        self._registry = registry
        self._source_factory = source_factory
        self._lock = threading.Lock()

    def handle(self, command: RegisterDiscoverySourceCommand) -> dict[str, Any]:
        """Register a new discovery source.

        Args:
            command: RegisterDiscoverySourceCommand with source configuration.

        Returns:
            Dict with source_id and registered flag.

        Raises:
            ValidationError: The source type is unknown or unavailable, or a source of
                that type is already held (422).
        """
        # The orchestrator keeps one source per type and lists it under the id derived
        # from that type, so a source that is built takes that id: the id POST returns
        # is then the one GET /sources shows. A spec-only registration keeps a UUID.
        source_id = config_source_id(command.source_type) if self._source_factory else str(uuid.uuid4())
        spec = DiscoverySourceSpec(
            source_id=source_id,
            source_type=command.source_type,
            mode=DiscoveryMode(command.mode),
            enabled=command.enabled,
            config=dict(command.config) if command.config else {},
        )
        if self._source_factory is None:
            self._registry.register_source(spec)
        else:
            try:
                source = self._source_factory(spec.source_type, {**spec.config, "mode": spec.mode.value})
                if not spec.enabled:
                    source.disable()
                with self._lock:
                    self._registry.register_live_source(spec, source)
            except ImportError as exc:
                raise ValidationError(f"The {spec.source_type!r} discovery source is unavailable: {exc}") from exc
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
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
            McpServerNotFoundError: If source_id is not registered.
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
            raise McpServerNotFoundError(mcp_server_id=command.source_id)

        logger.info("discovery_source_updated", source_id=command.source_id)
        return {"source_id": command.source_id, "updated": True}


class DeregisterDiscoverySourceHandler(CommandHandler):
    """Handler for DeregisterDiscoverySourceCommand.

    Removes a DiscoverySourceSpec from the registry.
    Raises McpServerNotFoundError (HTTP 404) if the source_id is unknown.
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
            McpServerNotFoundError: If source_id is not registered.
        """
        try:
            self._registry.unregister_source(command.source_id)
        except KeyError:
            raise McpServerNotFoundError(mcp_server_id=command.source_id)
        logger.info("discovery_source_deregistered", source_id=command.source_id)
        return {"source_id": command.source_id, "deregistered": True}


class TriggerSourceScanHandler(CommandHandler):
    """Handler for TriggerSourceScanCommand.

    Verifies the source exists, then triggers an immediate discovery scan via
    DiscoveryOrchestrator.trigger_discovery(). Returns the number of mcp_servers
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
            Dict with source_id, scan_triggered flag, and mcp_servers_found count.

        Raises:
            McpServerNotFoundError: If source_id is not registered.
        """
        spec = self._registry.get_source(command.source_id)
        if spec is None:
            raise McpServerNotFoundError(mcp_server_id=command.source_id)

        # Synchronous bridge: this handler runs in a worker thread, so it cannot
        # await. trigger_discovery() is a coroutine -- calling it bare left it
        # unawaited (no scan ran) and read the wrong result key. The bridge runs
        # the cycle to completion on discovery's own loop; the real count is
        # `discovered_count` (DiscoveryCycleResult.to_dict), not the never-emitted
        # `mcp_servers_discovered`.
        result = self._registry.orchestrator.trigger_discovery_blocking()
        mcp_servers_found = (
            result.get("discovered_count", 0) if isinstance(result, dict) else getattr(result, "discovered_count", 0)
        )

        logger.info(
            "discovery_scan_triggered",
            source_id=command.source_id,
            mcp_servers_found=mcp_servers_found,
        )
        return {
            "source_id": command.source_id,
            "scan_triggered": True,
            "mcp_servers_found": mcp_servers_found,
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
            McpServerNotFoundError: If source_id is not registered.
        """
        try:
            if command.enabled:
                spec = self._registry.enable_source(command.source_id)
            else:
                spec = self._registry.disable_source(command.source_id)
        except KeyError:
            raise McpServerNotFoundError(mcp_server_id=command.source_id)

        logger.info(
            "discovery_source_toggled",
            source_id=command.source_id,
            enabled=spec.enabled,
        )
        return {"source_id": command.source_id, "enabled": spec.enabled}


def register_discovery_handlers(
    command_bus: Any, registry: DiscoveryRegistry, source_factory: SourceFactory | None = None
) -> None:
    """Register all discovery source command handlers with the command bus.

    Args:
        command_bus: Command bus to register handlers on.
        registry: DiscoveryRegistry instance for handler injection.
        source_factory: Builds the live source for a source registered over REST (#1674).
    """
    command_bus.register(
        RegisterDiscoverySourceCommand,
        RegisterDiscoverySourceHandler(registry=registry, source_factory=source_factory),
    )
    command_bus.register(UpdateDiscoverySourceCommand, UpdateDiscoverySourceHandler(registry=registry))
    command_bus.register(DeregisterDiscoverySourceCommand, DeregisterDiscoverySourceHandler(registry=registry))
    command_bus.register(TriggerSourceScanCommand, TriggerSourceScanHandler(registry=registry))
    command_bus.register(ToggleDiscoverySourceCommand, ToggleDiscoverySourceHandler(registry=registry))
    logger.info("discovery_handlers_registered")
