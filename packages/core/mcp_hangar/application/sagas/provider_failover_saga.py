"""Provider Failover Saga - failover to backup providers on failure."""

import time
from dataclasses import dataclass

from ...domain.events import DomainEvent, ProviderDegraded, ProviderStarted, ProviderStopped
from ...infrastructure.saga_manager import EventTriggeredSaga
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


class ProviderFailoverSaga(EventTriggeredSaga):
    """
    Saga that orchestrates failover to backup providers.

    Failover Strategy:
    1. Configure primary-backup pairs
    2. When primary is degraded/stopped, start the backup
    3. Optionally, fail back to primary when it recovers
    4. Track active failovers to prevent cycles

    Configuration:
    - Failover pairs: Define which providers are backups for others
    - Auto-failback: Whether to automatically switch back to primary
    - Failback delay: How long to wait before failing back

    Usage:
        saga = ProviderFailoverSaga()
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

        # Providers pending failback: primary_id -> scheduled_time
        self._pending_failbacks: dict[str, float] = {}

    @property
    def saga_type(self) -> str:
        return "provider_failover"

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
            primary_id: Primary provider ID
            backup_id: Backup provider ID
            auto_failback: Whether to automatically fail back when primary recovers
            failback_delay_s: Delay before failing back
        """
        self._failover_configs[primary_id] = FailoverConfig(
            primary_id=primary_id,
            backup_id=backup_id,
            auto_failback=auto_failback,
            failback_delay_s=failback_delay_s,
        )
        logger.info(f"Configured failover: {primary_id} -> {backup_id}")

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
        """
        Handle provider degraded event.

        Initiates failover if this is a primary provider with a configured backup.
        """
        provider_id = event.provider_id
        commands = []

        # Check if this provider is a backup currently serving
        if provider_id in self._active_backups:
            logger.warning(f"Backup provider {provider_id} degraded - no further failover")
            return []

        # Check if this provider has a backup configured
        config = self._failover_configs.get(provider_id)
        if not config:
            return []

        # Check if failover is already active
        if provider_id in self._active_failovers:
            logger.debug(f"Failover already active for {provider_id}")
            return []

        # Initiate failover
        logger.info(f"Initiating failover: {provider_id} -> {config.backup_id}")

        self._active_failovers[provider_id] = FailoverState(
            primary_id=provider_id,
            backup_id=config.backup_id,
            failed_at=time.time(),
        )
        self._active_backups.add(config.backup_id)

        # Start backup provider
        commands.append(StartProviderCommand(provider_id=config.backup_id))

        return commands

    def _handle_started(self, event: ProviderStarted) -> list[Command]:
        """
        Handle provider started event.

        - If it's a backup being started, mark failover as complete
        - If it's a primary recovering, consider failback
        """
        provider_id = event.provider_id
        commands = []

        # Check if this is a backup being started for failover
        for primary_id, state in self._active_failovers.items():
            if state.backup_id == provider_id and state.backup_started_at is None:
                state.backup_started_at = time.time()
                logger.info(f"Failover complete: {primary_id} -> {provider_id}")

        # Check if this is a primary recovering
        if provider_id in self._active_failovers:
            state = self._active_failovers[provider_id]
            config = self._failover_configs.get(provider_id)

            if config and config.auto_failback:
                # Schedule failback
                failback_time = time.time() + config.failback_delay_s
                self._pending_failbacks[provider_id] = failback_time

                logger.info(f"Primary {provider_id} recovered, scheduling failback in {config.failback_delay_s}s")

                # In a real implementation, you'd use a scheduler
                # For now, immediately trigger failback commands
                commands.extend(self._execute_failback(provider_id))

        return commands

    def _handle_stopped(self, event: ProviderStopped) -> list[Command]:
        """
        Handle provider stopped event.

        Clean up failover state if a backup is stopped.
        """
        provider_id = event.provider_id
        commands = []

        # If a backup is stopped, clean up
        if provider_id in self._active_backups:
            self._active_backups.discard(provider_id)

            # Find and clean up the failover state
            for primary_id, state in list(self._active_failovers.items()):
                if state.backup_id == provider_id:
                    del self._active_failovers[primary_id]
                    self._pending_failbacks.pop(primary_id, None)
                    logger.info(f"Failover {primary_id} -> {provider_id} ended")

        return commands

    def _execute_failback(self, primary_id: str) -> list[Command]:
        """Execute failback to primary provider."""
        commands = []

        state = self._active_failovers.get(primary_id)
        config = self._failover_configs.get(primary_id)

        if not state or not config:
            return []

        logger.info(f"Executing failback: {state.backup_id} -> {primary_id}")

        # Stop the backup (primary is already running)
        commands.append(StopProviderCommand(provider_id=state.backup_id, reason="failback"))

        # Clean up failover state
        del self._active_failovers[primary_id]
        self._active_backups.discard(state.backup_id)
        self._pending_failbacks.pop(primary_id, None)

        return commands

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
        return self._execute_failback(primary_id)

    def cancel_failover(self, primary_id: str) -> bool:
        """Cancel an active failover (keeps backup running)."""
        if primary_id in self._active_failovers:
            state = self._active_failovers[primary_id]
            self._active_backups.discard(state.backup_id)
            del self._active_failovers[primary_id]
            self._pending_failbacks.pop(primary_id, None)
            return True
        return False
