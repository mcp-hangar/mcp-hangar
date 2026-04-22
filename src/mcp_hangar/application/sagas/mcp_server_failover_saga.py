"""McpServer Failover Saga - failover to backup mcp_servers on failure.

Architecture
------------
There are two classes here:

``McpServerFailoverSaga``
    A step-based :class:`~mcp_hangar.infrastructure.saga_manager.Saga` subclass.
    Each instance handles one concrete failover (primary -> backup).  It defines
    three named steps with compensation commands so that SagaManager can roll back
    partial work automatically on failure.

    Steps:
        1. ``start_backup``  - StartMcpServerCommand(backup_id)
                               compensation: StopMcpServerCommand(backup_id, reason="compensation")
        2. ``await_primary`` - no-op marker step (primary restart is event-driven)
                               compensation: no-op
        3. ``failback``      - StopMcpServerCommand(backup_id, reason="failback")
                               compensation: StartMcpServerCommand(backup_id)

``McpServerFailoverEventSaga``
    An :class:`~mcp_hangar.infrastructure.saga_manager.EventTriggeredSaga` that
    listens for domain events and starts ``McpServerFailoverSaga`` instances via the
    SagaManager when a configured primary degrades.  This class owns the failover
    configuration and tracks which pairs are currently active.
"""

import time
from dataclasses import dataclass
from typing import Any

from ...domain.events import DomainEvent, McpServerDegraded, McpServerStarted, McpServerStopped
from ...application.ports.saga import EventTriggeredSaga, ISagaManager, Saga, SagaContext
from ...logging_config import get_logger
from ..commands import Command, StartMcpServerCommand, StopMcpServerCommand

logger = get_logger(__name__)


@dataclass
class FailoverConfig:
    """Configuration for a failover pair."""

    primary_id: str
    backup_id: str
    auto_failback: bool = True  # Automatically fail back to primary when it recovers
    failback_delay_s: float = 30.0  # Delay before failing back to primary


@dataclass
class FailoverState:
    """State of an active failover."""

    primary_id: str
    backup_id: str
    failed_at: float
    backup_started_at: float | None = None
    is_active: bool = True


class McpServerFailoverSaga(Saga):
    """
    Step-based saga that orchestrates failover to a single backup mcp_server.

    This saga is started on demand by ``McpServerFailoverEventSaga`` whenever a
    configured primary mcp_server becomes degraded.  It progresses through three
    named steps; if any step fails SagaManager runs compensations in reverse order.

    Steps:
        1. ``start_backup``  - starts the backup mcp_server.
        2. ``await_primary`` - marker step; no command (primary restart is async).
        3. ``failback``      - stops the backup once the primary is healthy again.

    Context data expected in ``initial_data``:
        - ``primary_id`` (str): ID of the degraded primary mcp_server.
        - ``backup_id``  (str): ID of the backup mcp_server to start.
        - ``failback_delay_s`` (float, optional): Seconds to wait before failback.
    """

    @property
    def saga_type(self) -> str:
        return "mcp_server_failover"

    def configure(self, context: SagaContext) -> None:
        """
        Configure saga steps from context data.

        The context must contain ``primary_id`` and ``backup_id``.
        """
        primary_id: str = context.data["primary_id"]
        backup_id: str = context.data["backup_id"]

        self.add_step(
            name="start_backup",
            command=StartMcpServerCommand(mcp_server_id=backup_id),
            compensation_command=StopMcpServerCommand(mcp_server_id=backup_id, reason="compensation"),
        )
        self.add_step(
            name="await_primary",
            command=None,  # no-op: primary recovery is handled by McpServerRecoverySaga
            compensation_command=None,
        )
        self.add_step(
            name="failback",
            command=StopMcpServerCommand(mcp_server_id=backup_id, reason="failback"),
            compensation_command=StartMcpServerCommand(mcp_server_id=backup_id),
        )

        logger.info(
            "failover_saga_configured",
            primary_id=primary_id,
            backup_id=backup_id,
            steps=len(self._steps),
        )


class McpServerFailoverEventSaga(EventTriggeredSaga):
    """
    Event-driven coordinator that starts ``McpServerFailoverSaga`` instances.

    Listens for domain events and starts a new step-based ``McpServerFailoverSaga``
    whenever a configured primary degrades.  Also handles auto-failback using
    ``SagaManager.schedule_command`` so the delay is properly enforced.

    Usage::

        saga = McpServerFailoverEventSaga()
        saga.configure_failover("primary-mcp_server", "backup-mcp_server")
        saga_manager.register_event_saga(saga)
    """

    def __init__(self, saga_manager: ISagaManager | None = None):
        super().__init__()

        self._saga_manager = saga_manager

        # Failover configuration: primary_id -> FailoverConfig
        self._failover_configs: dict[str, FailoverConfig] = {}

        # Active failovers: primary_id -> FailoverState
        self._active_failovers: dict[str, FailoverState] = {}

        # McpServers currently acting as backups (to avoid cascading failovers)
        self._active_backups: set[str] = set()

        # Pending failback timer IDs: primary_id -> timer_id
        self._pending_failback_timers: dict[str, str] = {}

    @property
    def saga_type(self) -> str:
        return "mcp_server_failover_event"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [McpServerDegraded, McpServerStarted, McpServerStopped]

    def configure_failover(
        self,
        primary_id: str,
        backup_id: str,
        auto_failback: bool = True,
        failback_delay_s: float = 30.0,
    ) -> None:
        """
        Configure a failover pair.

        Args:
            primary_id: Primary mcp_server ID.
            backup_id: Backup mcp_server ID.
            auto_failback: Whether to automatically fail back when primary recovers.
            failback_delay_s: Delay in seconds before failing back.
        """
        self._failover_configs[primary_id] = FailoverConfig(
            primary_id=primary_id,
            backup_id=backup_id,
            auto_failback=auto_failback,
            failback_delay_s=failback_delay_s,
        )
        logger.info("failover_configured", primary_id=primary_id, backup_id=backup_id)

    def remove_failover(self, primary_id: str) -> bool:
        """Remove a failover configuration."""
        if primary_id in self._failover_configs:
            del self._failover_configs[primary_id]
            return True
        return False

    def handle(self, event: DomainEvent) -> list[Command]:
        """Handle failover-related events."""
        if isinstance(event, McpServerDegraded):
            return self._handle_degraded(event)
        elif isinstance(event, McpServerStarted):
            return self._handle_started(event)
        elif isinstance(event, McpServerStopped):
            return self._handle_stopped(event)
        return []

    def _handle_degraded(self, event: McpServerDegraded) -> list[Command]:
        """Initiate failover when a primary degrades."""
        mcp_server_id = event.mcp_server_id

        if mcp_server_id in self._active_backups:
            logger.warning("backup_mcp_server_degraded", mcp_server_id=mcp_server_id)
            return []

        config = self._failover_configs.get(mcp_server_id)
        if not config:
            return []

        if mcp_server_id in self._active_failovers:
            logger.debug("failover_already_active", primary_id=mcp_server_id)
            return []

        logger.info("initiating_failover", primary_id=mcp_server_id, backup_id=config.backup_id)

        self._active_failovers[mcp_server_id] = FailoverState(
            primary_id=mcp_server_id,
            backup_id=config.backup_id,
            failed_at=time.time(),
        )
        self._active_backups.add(config.backup_id)

        # Start the step-based McpServerFailoverSaga for this pair.
        # Commands are dispatched by SagaManager; we return empty here.
        if self._saga_manager is not None:
            failover_saga = McpServerFailoverSaga()
            self._saga_manager.start_saga(
                failover_saga,
                initial_data={
                    "primary_id": mcp_server_id,
                    "backup_id": config.backup_id,
                    "failback_delay_s": config.failback_delay_s,
                },
            )
        else:
            from ...infrastructure.saga_manager import get_saga_manager

            saga_manager = get_saga_manager()
            failover_saga = McpServerFailoverSaga()
            saga_manager.start_saga(
                failover_saga,
                initial_data={
                    "primary_id": mcp_server_id,
                    "backup_id": config.backup_id,
                    "failback_delay_s": config.failback_delay_s,
                },
            )

        return []

    def _handle_started(self, event: McpServerStarted) -> list[Command]:
        """Mark backup as started; schedule failback if primary recovers."""
        mcp_server_id = event.mcp_server_id

        # Mark backup start time
        for primary_id, state in self._active_failovers.items():
            if state.backup_id == mcp_server_id and state.backup_started_at is None:
                state.backup_started_at = time.time()
                logger.info("failover_backup_started", primary_id=primary_id, backup_id=mcp_server_id)

        # Primary recovered while failover is active -> schedule failback
        if mcp_server_id in self._active_failovers:
            state = self._active_failovers[mcp_server_id]
            config = self._failover_configs.get(mcp_server_id)

            if config and config.auto_failback:
                logger.info(
                    "primary_recovered_scheduling_failback",
                    primary_id=mcp_server_id,
                    delay_s=config.failback_delay_s,
                )
                stop_cmd = StopMcpServerCommand(mcp_server_id=state.backup_id, reason="failback")
                sm = self._saga_manager
                if sm is None:
                    from ...infrastructure.saga_manager import get_saga_manager

                    sm = get_saga_manager()
                timer_id = sm.schedule_command(stop_cmd, delay_s=config.failback_delay_s)
                self._pending_failback_timers[mcp_server_id] = timer_id

                # Clean up failover tracking
                del self._active_failovers[mcp_server_id]
                self._active_backups.discard(state.backup_id)

        return []

    def _handle_stopped(self, event: McpServerStopped) -> list[Command]:
        """Clean up when a backup is stopped."""
        mcp_server_id = event.mcp_server_id

        if mcp_server_id in self._active_backups:
            self._active_backups.discard(mcp_server_id)

            for primary_id, state in list(self._active_failovers.items()):
                if state.backup_id == mcp_server_id:
                    del self._active_failovers[primary_id]
                    timer_id = self._pending_failback_timers.pop(primary_id, None)
                    if timer_id is not None:
                        try:
                            sm = self._saga_manager
                            if sm is None:
                                from ...infrastructure.saga_manager import get_saga_manager

                                sm = get_saga_manager()
                            sm.cancel_scheduled_command(timer_id)
                        except Exception as e:  # noqa: BLE001 -- fault-barrier: cancel failure must not block event handling
                            logger.warning("failback_timer_cancel_failed", error=str(e))
                    logger.info("failover_ended", primary_id=primary_id, backup_id=mcp_server_id)

        return []

    def get_active_failovers(self) -> dict[str, FailoverState]:
        """Get all active failovers."""
        return dict(self._active_failovers)

    def get_failover_config(self, primary_id: str) -> FailoverConfig | None:
        """Get failover configuration for a mcp_server."""
        return self._failover_configs.get(primary_id)

    def get_all_configs(self) -> dict[str, FailoverConfig]:
        """Get all failover configurations."""
        return dict(self._failover_configs)

    def is_backup_active(self, mcp_server_id: str) -> bool:
        """Check if a mcp_server is currently serving as a backup."""
        return mcp_server_id in self._active_backups

    def force_failback(self, primary_id: str) -> list[Command]:
        """Manually force a failback to primary."""
        state = self._active_failovers.get(primary_id)
        if not state:
            return []
        cmd: Command = StopMcpServerCommand(mcp_server_id=state.backup_id, reason="failback")
        commands: list[Command] = [cmd]
        del self._active_failovers[primary_id]
        self._active_backups.discard(state.backup_id)
        self._pending_failback_timers.pop(primary_id, None)
        return commands

    def cancel_failover(self, primary_id: str) -> bool:
        """Cancel an active failover (keeps backup running)."""
        if primary_id in self._active_failovers:
            state = self._active_failovers[primary_id]
            self._active_backups.discard(state.backup_id)
            del self._active_failovers[primary_id]
            timer_id = self._pending_failback_timers.pop(primary_id, None)
            if timer_id is not None:
                try:
                    sm = self._saga_manager
                    if sm is None:
                        from ...infrastructure.saga_manager import get_saga_manager

                        sm = get_saga_manager()
                    sm.cancel_scheduled_command(timer_id)
                except Exception as e:  # noqa: BLE001 -- fault-barrier: cancel failure must not block caller
                    logger.warning("failback_timer_cancel_failed", error=str(e))
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize failover state for persistence."""
        return {
            "failover_configs": {
                k: {
                    "primary_id": v.primary_id,
                    "backup_id": v.backup_id,
                    "auto_failback": v.auto_failback,
                    "failback_delay_s": v.failback_delay_s,
                }
                for k, v in self._failover_configs.items()
            },
            "active_failovers": {
                k: {
                    "primary_id": v.primary_id,
                    "backup_id": v.backup_id,
                    "failed_at": v.failed_at,
                    "backup_started_at": v.backup_started_at,
                    "is_active": v.is_active,
                }
                for k, v in self._active_failovers.items()
            },
            "active_backups": list(self._active_backups),
            "pending_failback_timers": dict(self._pending_failback_timers),
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restore failover state from persistence."""
        self._failover_configs = {k: FailoverConfig(**v) for k, v in data.get("failover_configs", {}).items()}
        self._active_failovers = {k: FailoverState(**v) for k, v in data.get("active_failovers", {}).items()}
        self._active_backups = set(data.get("active_backups", []))
        # Timers cannot be restored across process restarts; start fresh.
        self._pending_failback_timers = {}

ProviderFailoverSaga = McpServerFailoverSaga
ProviderFailoverEventSaga = McpServerFailoverEventSaga
