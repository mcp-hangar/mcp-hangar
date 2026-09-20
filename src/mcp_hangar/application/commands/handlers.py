"""Command handlers implementation."""

from typing import Any, cast

from ...domain.contracts.command import CommandHandler
from ...domain.contracts.event_bus import IEventBus
from ...domain.contracts.mcp_server_runtime import McpServerRuntime
from ...domain.contracts.runtime_store import IRuntimeMcpServerStore
from ...domain.exceptions import McpServerNotFoundError, ToolInvocationError
from ...domain.repository import IMcpServerRepository
from ...logging_config import get_logger
from ...metrics import mcp_server_stop_reason, observe_tool_call, record_error, record_mcp_server_start
from ...stream_ids import MCP_SERVER
from ..ports.bus import ICommandBus
from ..ports.config_loader import IConfigLoader
from .commands import (
    GiveUpOnMcpServerCommand,
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
            return cast(McpServerRuntime, mcp_server)

        # Then check runtime (hot-loaded) mcp_servers
        if self._runtime_store is not None:
            mcp_server = self._runtime_store.get_mcp_server(mcp_server_id)
            if mcp_server is not None:
                return mcp_server

        raise McpServerNotFoundError(mcp_server_id)

    def _publish_events(self, mcp_server: McpServerRuntime) -> None:
        """Publish collected events from mcp_server (no duck typing)."""
        # The runtime port promises `Iterable`, the store wants a list.
        events = list(mcp_server.collect_events())
        if not events:
            return
        try:
            self._event_bus.publish_aggregate_events(MCP_SERVER, mcp_server.mcp_server_id, events)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: the command already ran; publishing its events must not change its outcome
            # Any exception, not a short list of types. This runs after the
            # command has done its work, which for a tool call means after the
            # upstream executed it, and `InvokeToolHandler` runs it in a
            # `finally`. An exception that got past the old list
            # (RuntimeError, ValueError, TypeError) reported a call that ran as
            # a failure. A client could then retry a non-idempotent action.
            # When the call had raised, the exception also replaced the call's
            # own error. The bus already delivers a batch
            # its store refused. Whatever still reaches here is logged and
            # counted, and the command's result stands.
            record_error("event_publish", type(e).__name__)
            logger.error(
                "event_publish_failed",
                mcp_server_id=mcp_server.mcp_server_id,
                event_types=[type(event).__name__ for event in events],
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
            # Not deliberate: started as a call would start it (see the command).
            mcp_server.ensure_ready(by_call=not command.deliberate)
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
        Stop a mcp_server, recording the stop under the command's reason.

        Not counted here: the stop counter counts the `McpServerStopped` the
        stop records, once, under that reason (#1466). A reason outside the
        counter's closed set, which only a REST stop body can supply, is
        recorded as `manual`; the answer repeats the reason as given.

        Returns:
            Confirmation dict
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)
        mcp_server.shutdown(reason=mcp_server_stop_reason(command.reason))
        self._publish_events(mcp_server)

        return {"stopped": command.mcp_server_id, "reason": command.reason}


class GiveUpOnMcpServerHandler(BaseMcpServerHandler):
    """Handler for GiveUpOnMcpServerCommand."""

    def handle(self, command: GiveUpOnMcpServerCommand) -> dict[str, Any]:
        """
        Leave a degraded mcp_server DEAD (#1361).

        Not counted here: the give-up records a stop with its own reason
        (#1360), and the stop counter counts it from that event, as it counts
        every stop the aggregate records.

        Returns:
            Whether it was given up on, and the state it is in.
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)
        gave_up = mcp_server.give_up(command.reason)
        self._publish_events(mcp_server)

        return {"mcp_server": command.mcp_server_id, "gave_up": gave_up, "state": mcp_server.state.value}


class InvokeToolHandler(BaseMcpServerHandler):
    """Handler for InvokeToolCommand."""

    def handle(self, command: InvokeToolCommand) -> dict[str, Any]:
        """
        Invoke a tool on a mcp_server.

        Returns:
            Tool result
        """
        mcp_server = self._get_mcp_server(command.mcp_server_id)

        try:
            return mcp_server.invoke_tool(
                command.tool_name,
                command.arguments,
                command.timeout,
                l7_approval_id=command.l7_approval_id,
                progress_token=command.progress_token,
            )

        except Exception as e:  # noqa: BLE001 -- fault-barrier: catch for metrics recording, then re-raise
            # A call's Prometheus record is its ToolInvocationCompleted/Failed,
            # written by MetricsEventHandler -- which also sees calls made
            # outside this bus. Observing here as well counted each call twice
            # (#1299). A failure raised before either event (a refusal, an
            # unknown tool, a failed cold start) has none, so it counts here.
            # Every raise after a ToolInvocationFailed carries its correlation_id.
            # Ask the exception, not the batch: a concurrent call can drain it.
            if not (isinstance(e, ToolInvocationError) and "correlation_id" in e.details):
                observe_tool_call(command.mcp_server_id, command.tool_name, 0.0, False, type(e).__name__)
            raise

        finally:
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
    *,
    config_loader: IConfigLoader,
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
    command_bus.register(GiveUpOnMcpServerCommand, GiveUpOnMcpServerHandler(repository, event_bus, runtime_store))
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
