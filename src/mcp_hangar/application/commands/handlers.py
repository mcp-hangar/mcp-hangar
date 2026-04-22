"""Command handlers implementation."""

import time
from typing import Any

from ...domain.contracts.command import CommandHandler
from ...domain.contracts.event_bus import IEventBus
from ...domain.contracts.mcp_server_runtime import McpServerRuntime
from ...domain.contracts.runtime_store import IRuntimeMcpServerStore
from ...domain.exceptions import McpServerNotFoundError
from ...domain.repository import IMcpServerRepository
from ...logging_config import get_logger
from ...metrics import observe_tool_call, record_error, record_mcp_server_start, record_mcp_server_stop
from ..ports.bus import ICommandBus
from .commands import (
    HealthCheckCommand,
    InvokeToolCommand,
    ShutdownIdleMcpServersCommand,
    StartMcpServerCommand,
    StopMcpServerCommand,
)

logger = get_logger(__name__)


class BaseMcpServerHandler(CommandHandler):
    """Base class for handlers that work with mcp_servers."""

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: IEventBus,
        runtime_store: IRuntimeMcpServerStore | None = None,
    ):
        self._repository = repository
        self._event_bus = event_bus
        self._runtime_store = runtime_store

    def _get_mcp_server(self, mcp_server_id: str) -> McpServerRuntime:
        """Get mcp_server or raise domain McpServerNotFoundError.

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

    def _publish_events(self, mcp_server: McpServerRuntime) -> None:
        """Publish collected events from mcp_server (no duck typing)."""
        for event in mcp_server.collect_events():
            try:
                self._event_bus.publish(event)
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error(
                    "event_publish_failed",
                    event_type=type(event).__name__,
                    error=str(e),
                    exc_info=True,
                )


class StartMcpServerHandler(BaseMcpServerHandler):
    """Handler for StartMcpServerCommand."""

    def handle(self, command: StartMcpServerCommand) -> dict[str, Any]:
        """
        Start a mcp_server.

        Returns:
            Dict with mcp_server state and tools
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)
        try:
            mcp_server.ensure_ready()
            record_mcp_server_start(command.mcp_server_id, success=True)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: catch for metrics recording, then re-raise
            record_mcp_server_start(command.mcp_server_id, success=False)
            record_error("mcp_server", type(e).__name__)
            raise
        finally:
            self._publish_events(mcp_server)

        return {
            "mcp_server": command.mcp_server_id,
            "state": mcp_server.state.value,
            "tools": mcp_server.get_tool_names(),
        }


class StopMcpServerHandler(BaseMcpServerHandler):
    """Handler for StopMcpServerCommand."""

    def handle(self, command: StopMcpServerCommand) -> dict[str, Any]:
        """
        Stop a mcp_server.

        Returns:
            Confirmation dict
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)
        mcp_server.shutdown()
        record_mcp_server_stop(command.mcp_server_id, reason=command.reason or "manual")
        self._publish_events(mcp_server)

        return {"stopped": command.mcp_server_id, "reason": command.reason}


class InvokeToolHandler(BaseMcpServerHandler):
    """Handler for InvokeToolCommand."""

    def handle(self, command: InvokeToolCommand) -> dict[str, Any]:
        """
        Invoke a tool on a mcp_server.

        Returns:
            Tool result
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)

        start_time = time.perf_counter()
        error_type = None
        success = False

        try:
            result = mcp_server.invoke_tool(command.tool_name, command.arguments, command.timeout)
            success = True
            return result

        except Exception as e:  # noqa: BLE001 -- fault-barrier: catch for metrics recording, then re-raise
            error_type = type(e).__name__
            raise

        finally:
            duration = time.perf_counter() - start_time
            observe_tool_call(
                mcp_server=command.mcp_server_id,
                tool=command.tool_name,
                duration=duration,
                success=success,
                error_type=error_type,
            )
            self._publish_events(mcp_server)


class HealthCheckHandler(BaseMcpServerHandler):
    """Handler for HealthCheckCommand."""

    def handle(self, command: HealthCheckCommand) -> bool:
        """
        Perform health check on a mcp_server.

        Returns:
            True if healthy, False otherwise
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)
        result = mcp_server.health_check()
        self._publish_events(mcp_server)

        return result


class ShutdownIdleMcpServersHandler(BaseMcpServerHandler):
    """Handler for ShutdownIdleMcpServersCommand."""

    def handle(self, command: ShutdownIdleMcpServersCommand) -> list[str]:
        """
        Shutdown all idle mcp_servers.

        Returns:
            List of mcp_server IDs that were shutdown
        """
        shutdown_ids = []
        for mcp_server_id, mcp_server in self._repository.get_all().items():
            if mcp_server.maybe_shutdown_idle():
                shutdown_ids.append(mcp_server_id)
                self._publish_events(mcp_server)

        return shutdown_ids


def register_all_handlers(
    command_bus: ICommandBus,
    repository: IMcpServerRepository,
    event_bus: IEventBus,
    current_config_path: str | None = None,
    config_loader=None,
    groups: dict | None = None,
    runtime_store: IRuntimeMcpServerStore | None = None,
) -> None:
    """
    Register all command handlers with the command bus.

    Args:
        command_bus: The command bus to register handlers with
        repository: McpServer repository
        event_bus: Event bus for publishing events
        current_config_path: Current configuration file path for reload handler
        config_loader: IConfigLoader implementation for reload handler
        groups: Groups dict for reload handler
        runtime_store: Optional runtime mcp_server store for hot-loaded mcp_server lookup
    """
    from .commands import ReloadConfigurationCommand
    from .reload_handler import ReloadConfigurationHandler

    command_bus.register(StartMcpServerCommand, StartMcpServerHandler(repository, event_bus, runtime_store))
    command_bus.register(StopMcpServerCommand, StopMcpServerHandler(repository, event_bus, runtime_store))
    command_bus.register(InvokeToolCommand, InvokeToolHandler(repository, event_bus, runtime_store))
    command_bus.register(HealthCheckCommand, HealthCheckHandler(repository, event_bus, runtime_store))
    command_bus.register(
        ShutdownIdleMcpServersCommand,
        ShutdownIdleMcpServersHandler(repository, event_bus, runtime_store),
    )
    command_bus.register(
        ReloadConfigurationCommand,
        ReloadConfigurationHandler(
            repository,
            event_bus,
            current_config_path,
            config_loader=config_loader,
            groups=groups,
        ),
    )

    logger.info("command_handlers_registered")
