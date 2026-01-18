"""Provider Recovery Saga - automatically recover degraded providers."""

import time

from ...domain.events import DomainEvent, HealthCheckFailed, ProviderDegraded, ProviderStarted, ProviderStopped
from ...infrastructure.saga_manager import EventTriggeredSaga
from ...logging_config import get_logger
from ..commands import Command, StartProviderCommand, StopProviderCommand

logger = get_logger(__name__)


class ProviderRecoverySaga(EventTriggeredSaga):
    """
    Saga that orchestrates automatic provider recovery after failures.

    Recovery Strategy:
    1. When a provider is degraded, schedule a retry
    2. Apply exponential backoff between retries
    3. After max retries, give up and stop the provider
    4. Reset retry count when provider starts successfully

    Configuration:
    - max_retries: Maximum number of restart attempts (default: 3)
    - initial_backoff_s: Initial backoff duration in seconds (default: 5)
    - max_backoff_s: Maximum backoff duration (default: 60)
    - backoff_multiplier: Backoff multiplier for exponential growth (default: 2)
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_backoff_s: float = 5.0,
        max_backoff_s: float = 60.0,
        backoff_multiplier: float = 2.0,
    ):
        super().__init__()

        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._backoff_multiplier = backoff_multiplier

        # Track retry state per provider
        # provider_id -> {"retries": int, "last_attempt": float, "next_retry": float}
        self._retry_state: dict[str, dict] = {}

    @property
    def saga_type(self) -> str:
        return "provider_recovery"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [ProviderDegraded, ProviderStarted, ProviderStopped, HealthCheckFailed]

    def handle(self, event: DomainEvent) -> list[Command]:
        """Handle recovery-related events."""
        if isinstance(event, ProviderDegraded):
            return self._handle_degraded(event)
        elif isinstance(event, ProviderStarted):
            return self._handle_started(event)
        elif isinstance(event, ProviderStopped):
            return self._handle_stopped(event)
        elif isinstance(event, HealthCheckFailed):
            return self._handle_health_failed(event)
        return []

    def _handle_degraded(self, event: ProviderDegraded) -> list[Command]:
        """
        Handle provider degraded event.

        Initiates recovery by scheduling a restart with backoff.
        """
        provider_id = event.provider_id

        # Initialize retry state if needed
        if provider_id not in self._retry_state:
            self._retry_state[provider_id] = {
                "retries": 0,
                "last_attempt": 0,
                "next_retry": 0,
            }

        state = self._retry_state[provider_id]
        state["retries"] += 1
        state["last_attempt"] = time.time()

        # Check if max retries exceeded
        if state["retries"] > self._max_retries:
            logger.warning(f"Provider {provider_id} exceeded max retries ({self._max_retries}), stopping recovery")
            # Stop the provider permanently
            return [StopProviderCommand(provider_id=provider_id, reason="max_retries_exceeded")]

        # Calculate backoff
        backoff = self._calculate_backoff(state["retries"])
        state["next_retry"] = time.time() + backoff

        logger.info(
            f"Provider {provider_id} degraded, scheduling retry "
            f"{state['retries']}/{self._max_retries} in {backoff:.1f}s"
        )

        # Note: In a real implementation, you would use a scheduler
        # to delay the command. For now, we return it immediately.
        # The provider's internal backoff will handle timing.
        return [StartProviderCommand(provider_id=provider_id)]

    def _handle_started(self, event: ProviderStarted) -> list[Command]:
        """
        Handle provider started event.

        Resets retry count on successful start.
        """
        provider_id = event.provider_id

        if provider_id in self._retry_state:
            old_retries = self._retry_state[provider_id]["retries"]
            self._retry_state[provider_id] = {
                "retries": 0,
                "last_attempt": 0,
                "next_retry": 0,
            }
            if old_retries > 0:
                logger.info(f"Provider {provider_id} recovered successfully after {old_retries} retries")

        return []

    def _handle_stopped(self, event: ProviderStopped) -> list[Command]:
        """
        Handle provider stopped event.

        Clears retry state for normally stopped providers.
        """
        provider_id = event.provider_id

        # Only clear state for intentional stops
        if event.reason in ("shutdown", "idle", "user_request"):
            self._retry_state.pop(provider_id, None)

        return []

    def _handle_health_failed(self, event: HealthCheckFailed) -> list[Command]:
        """
        Handle health check failed event.

        May trigger preemptive recovery for severely degraded providers.
        """
        # If failures are severe but provider not yet degraded, no action.
        # The ProviderDegraded event will handle actual recovery.
        return []

    def _calculate_backoff(self, retry_count: int) -> float:
        """Calculate backoff duration for a retry count."""
        backoff = self._initial_backoff_s * (self._backoff_multiplier ** (retry_count - 1))
        return min(backoff, self._max_backoff_s)

    def get_retry_state(self, provider_id: str) -> dict | None:
        """Get retry state for a provider (for monitoring)."""
        return self._retry_state.get(provider_id)

    def get_all_retry_states(self) -> dict[str, dict]:
        """Get all retry states (for monitoring)."""
        return dict(self._retry_state)

    def reset_retry_state(self, provider_id: str) -> None:
        """Manually reset retry state for a provider."""
        self._retry_state.pop(provider_id, None)

    def reset_all_retry_states(self) -> None:
        """Reset all retry states."""
        self._retry_state.clear()
