"""Provider Failover Saga - failover to backup providers on failure.

Architecture
------------
There are two classes here:

``ProviderFailoverSaga``
    A step-based :class:`~mcp_hangar.infrastructure.saga_manager.Saga` subclass.
    Each instance handles one concrete failover (primary -> backup).  It defines
    three named steps with compensation commands so that SagaManager can roll back
    partial work automatically on failure.

    Steps:
        1. ``start_backup``  - StartProviderCommand(backup_id)
                               compensation: StopProviderCommand(backup_id, reason="compensation")
        2. ``await_primary`` - no-op marker step (primary restart is event-driven)
                               compensation: no-op
        3. ``failback``      - StopProviderCommand(backup_id, reason="failback")
                               compensation: StartProviderCommand(backup_id)

``ProviderFailoverEventSaga``
    An :class:`~mcp_hangar.infrastructure.saga_manager.EventTriggeredSaga` that
    listens for domain events and starts ``ProviderFailoverSaga`` instances via the
    SagaManager when a configured primary degrades.  This class owns the failover
    configuration and tracks which pairs are currently active.
"""

import time
from dataclasses import dataclass
from typing import Any

from ...domain.events import DomainEvent, ProviderDegraded, ProviderStarted, ProviderStopped
from ...infrastructure.saga_manager import EventTriggeredSaga, Saga, SagaContext, get_saga_manager
from ...logging_config import get_logger
from ..commands import Command, StartProviderCommand, StopProviderCommand

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


class ProviderFailoverSaga(Saga):
    """
    Step-based saga that orchestrates failover to a single backup provider.

    This saga is started on demand by ``ProviderFailoverEventSaga`` whenever a
    configured primary provider becomes degraded.  It progresses through three
    named steps; if any step fails SagaManager runs compensations in reverse order.

    Steps:
        1. ``start_backup``  - starts the backup provider.
        2. ``await_primary`` - marker step; no command (primary restart is async).
        3. ``failback``      - stops the backup once the primary is healthy again.

    Context data expected in ``initial_data``:
        - ``primary_id`` (str): ID of the degraded primary provider.
        - ``backup_id``  (str): ID of the backup provider to start.
        - ``failback_delay_s`` (float, optional): Seconds to wait before failback.
    """

    @property
    def saga_type(self) -> str:
        return "provider_failover"

    def configure(self, context: SagaContext) -> None:
        """
        Configure saga steps from context data.

        The context must contain ``primary_id`` and ``backup_id``.
        """
        primary_id: str = context.data["primary_id"]
        backup_id: str = context.data["backup_id"]

        self.add_step(
            name="start_backup",
            command=StartProviderCommand(provider_id=backup_id),
            compensation_command=StopProviderCommand(provider_id=backup_id, reason="compensation"),
        )
        self.add_step(
            name="await_primary",
            command=None,  # no-op: primary recovery is handled by ProviderRecoverySaga
            compensation_command=None,
        )
        self.add_step(
            name="failback",
            command=StopProviderCommand(provider_id=backup_id, reason="failback"),
            compensation_command=StartProviderCommand(provider_id=backup_id),
        )

        logger.info(
            "failover_saga_configured",
            primary_id=primary_id,
            backup_id=backup_id,
            steps=len(self._steps),
        )


class ProviderFailoverEventSaga(EventTriggeredSaga):
    """
    Event-driven coordinator that starts ``ProviderFailoverSaga`` instances.

    Listens for domain events and starts a new step-based ``ProviderFailoverSaga``
    whenever a configured primary degrades.  Also handles auto-failback using
    ``SagaManager.schedule_command`` so the delay is properly enforced.

    Usage::

        saga = ProviderFailoverEventSaga()
        saga.configure_failover("primary-provider", "backup-provider")
        saga_manager.register_event_saga(saga)
    """

    def __init__(self):
        super().__init__()

        # Failover configuration: primary_id -> FailoverConfig
        self._failover_configs: dict[str, FailoverConfig] = {}

        # Active failovers: primary_id -> FailoverState
        self._active_failovers: dict[str, FailoverState] = {}

        # Providers currently acting as backups (to avoid cascading failovers)
        self._active_backups: set[str] = set()

        # Pending failback timer IDs: primary_id -> timer_id
        self._pending_failback_timers: dict[str, str] = {}

    @property
    def saga_type(self) -> str:
        return "provider_failover_event"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [ProviderDegraded, ProviderStarted, ProviderStopped]

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
            primary_id: Primary provider ID.
            backup_id: Backup provider ID.
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
        if isinstance(event, ProviderDegraded):
            return self._handle_degraded(event)
        elif isinstance(event, ProviderStarted):
            return self._handle_started(event)
        elif isinstance(event, ProviderStopped):
            return self._handle_stopped(event)
        return []

    def _handle_degraded(self, event: ProviderDegraded) -> list[Command]:
        """Initiate failover when a primary degrades."""
        provider_id = event.provider_id

        if provider_id in self._active_backups:
            logger.warning("backup_provider_degraded", provider_id=provider_id)
            return []

        config = self._failover_configs.get(provider_id)
        if not config:
            return []

        if provider_id in self._active_failovers:
            logger.debug("failover_already_active", primary_id=provider_id)
            return []

        logger.info("initiating_failover", primary_id=provider_id, backup_id=config.backup_id)

        self._active_failovers[provider_id] = FailoverState(
            primary_id=provider_id,
            backup_id=config.backup_id,
            failed_at=time.time(),
        )
        self._active_backups.add(config.backup_id)

        # Start the step-based ProviderFailoverSaga for this pair.
        # Commands are dispatched by SagaManager; we return empty here.
        saga_manager = get_saga_manager()
        failover_saga = ProviderFailoverSaga()
        saga_manager.start_saga(
            failover_saga,
            initial_data={
                "primary_id": provider_id,
                "backup_id": config.backup_id,
                "failback_delay_s": config.failback_delay_s,
            },
        )

        return []

    def _handle_started(self, event: ProviderStarted) -> list[Command]:
        """Mark backup as started; schedule failback if primary recovers."""
        provider_id = event.provider_id

        # Mark backup start time
        for primary_id, state in self._active_failovers.items():
            if state.backup_id == provider_id and state.backup_started_at is None:
                state.backup_started_at = time.time()
                logger.info("failover_backup_started", primary_id=primary_id, backup_id=provider_id)

        # Primary recovered while failover is active -> schedule failback
        if provider_id in self._active_failovers:
            state = self._active_failovers[provider_id]
            config = self._failover_configs.get(provider_id)

            if config and config.auto_failback:
                logger.info(
                    "primary_recovered_scheduling_failback",
                    primary_id=provider_id,
                    delay_s=config.failback_delay_s,
                )
                stop_cmd = StopProviderCommand(provider_id=state.backup_id, reason="failback")
                saga_manager = get_saga_manager()
                timer_id = saga_manager.schedule_command(stop_cmd, delay_s=config.failback_delay_s)
                self._pending_failback_timers[provider_id] = timer_id

                # Clean up failover tracking
                del self._active_failovers[provider_id]
                self._active_backups.discard(state.backup_id)

        return []

    def _handle_stopped(self, event: ProviderStopped) -> list[Command]:
        """Clean up when a backup is stopped."""
        provider_id = event.provider_id

        if provider_id in self._active_backups:
            self._active_backups.discard(provider_id)

            for primary_id, state in list(self._active_failovers.items()):
                if state.backup_id == provider_id:
                    del self._active_failovers[primary_id]
                    timer_id = self._pending_failback_timers.pop(primary_id, None)
                    if timer_id is not None:
                        try:
                            get_saga_manager().cancel_scheduled_command(timer_id)
                        except Exception as e:  # noqa: BLE001 -- fault-barrier: cancel failure must not block event handling
                            logger.warning("failback_timer_cancel_failed", error=str(e))
                    logger.info("failover_ended", primary_id=primary_id, backup_id=provider_id)

        return []

    def get_active_failovers(self) -> dict[str, FailoverState]:
        """Get all active failovers."""
        return dict(self._active_failovers)

    def get_failover_config(self, primary_id: str) -> FailoverConfig | None:
        """Get failover configuration for a provider."""
        return self._failover_configs.get(primary_id)

    def get_all_configs(self) -> dict[str, FailoverConfig]:
        """Get all failover configurations."""
        return dict(self._failover_configs)

    def is_backup_active(self, provider_id: str) -> bool:
        """Check if a provider is currently serving as a backup."""
        return provider_id in self._active_backups

    def force_failback(self, primary_id: str) -> list[Command]:
        """Manually force a failback to primary."""
        state = self._active_failovers.get(primary_id)
        if not state:
            return []
        cmd: Command = StopProviderCommand(provider_id=state.backup_id, reason="failback")
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
                    get_saga_manager().cancel_scheduled_command(timer_id)
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
