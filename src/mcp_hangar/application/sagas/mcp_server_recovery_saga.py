"""McpServer Recovery Saga - automatically recover degraded mcp_servers."""

# pyright: reportUnannotatedClassAttribute=false, reportMissingTypeArgument=false, reportImplicitOverride=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportImplicitStringConcatenation=false, reportUnusedCallResult=false, reportUnusedParameter=false, reportUnknownParameterType=false, reportExplicitAny=false

import time

from typing import Any

from ...domain.events import DomainEvent, HealthCheckFailed, McpServerDegraded, McpServerStarted, McpServerStopped
from ...application.ports.saga import EventTriggeredSaga, ISagaManager
from ...logging_config import get_logger
from ..commands import Command, StartMcpServerCommand, StopMcpServerCommand

logger = get_logger(__name__)


class McpServerRecoverySaga(EventTriggeredSaga):
    """
    Saga that orchestrates automatic mcp_server recovery after failures.

    Recovery Strategy:
    1. When a mcp_server is degraded, schedule a retry
    2. Apply exponential backoff between retries
    3. After max retries, give up and stop the mcp_server
    4. Reset retry count when mcp_server starts successfully

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
        saga_manager: ISagaManager | None = None,
    ):
        super().__init__()

        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._backoff_multiplier = backoff_multiplier
        self._saga_manager = saga_manager

        # Track retry state per mcp_server
        # mcp_server_id -> {"retries": int, "last_attempt": float, "next_retry": float}
        self._retry_state: dict[str, dict] = {}

    @property
    def saga_type(self) -> str:
        return "mcp_server_recovery"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [McpServerDegraded, McpServerStarted, McpServerStopped, HealthCheckFailed]

    def handle(self, event: DomainEvent) -> list[Command]:
        """Handle recovery-related events."""
        if isinstance(event, McpServerDegraded):
            return self._handle_degraded(event)
        elif isinstance(event, McpServerStarted):
            return self._handle_started(event)
        elif isinstance(event, McpServerStopped):
            return self._handle_stopped(event)
        elif isinstance(event, HealthCheckFailed):
            return self._handle_health_failed(event)
        return []

    def _handle_degraded(self, event: McpServerDegraded) -> list[Command]:
        """
        Handle mcp_server degraded event.

        Initiates recovery by scheduling a restart with backoff.
        """
        # Skip auto-recovery for capability violations (block mode kills mcp_server)
        if hasattr(event, "reason") and event.reason.startswith("capability_violation:"):
            logger.info(
                "mcp_server_degraded_capability_violation",
                mcp_server_id=event.mcp_server_id,
                reason=event.reason,
            )
            return []

        if hasattr(event, "reason") and event.reason.startswith("detection_enforcement:"):
            logger.info(
                "mcp_server_degraded_detection_enforcement",
                mcp_server_id=event.mcp_server_id,
                reason=event.reason,
            )
            return []

        mcp_server_id = event.mcp_server_id

        # Initialize retry state if needed
        if mcp_server_id not in self._retry_state:
            self._retry_state[mcp_server_id] = {
                "retries": 0,
                "last_attempt": 0,
                "next_retry": 0,
            }

        state = self._retry_state[mcp_server_id]
        state["retries"] += 1
        state["last_attempt"] = time.time()

        # Check if max retries exceeded
        if state["retries"] > self._max_retries:
            logger.warning(f"McpServer {mcp_server_id} exceeded max retries ({self._max_retries}), stopping recovery")
            # Stop the mcp_server permanently
            return [StopMcpServerCommand(mcp_server_id=mcp_server_id, reason="max_retries_exceeded")]

        # Calculate backoff
        backoff = self._calculate_backoff(state["retries"])
        state["next_retry"] = time.time() + backoff

        logger.info(
            f"McpServer {mcp_server_id} degraded, scheduling retry "
            f"{state['retries']}/{self._max_retries} in {backoff:.1f}s"
        )

        # Schedule the restart command to fire after the computed backoff delay.
        sm = self._saga_manager
        if sm is None:
            from ...infrastructure.saga_manager import get_saga_manager

            sm = get_saga_manager()
        sm.schedule_command(
            StartMcpServerCommand(mcp_server_id=mcp_server_id),
            delay_s=backoff,
        )
        return []

    def _handle_started(self, event: McpServerStarted) -> list[Command]:
        """
        Handle mcp_server started event.

        Resets retry count on successful start.
        """
        mcp_server_id = event.mcp_server_id

        if mcp_server_id in self._retry_state:
            old_retries = self._retry_state[mcp_server_id]["retries"]
            self._retry_state[mcp_server_id] = {
                "retries": 0,
                "last_attempt": 0,
                "next_retry": 0,
            }
            if old_retries > 0:
                logger.info(f"McpServer {mcp_server_id} recovered successfully after {old_retries} retries")

        return []

    def _handle_stopped(self, event: McpServerStopped) -> list[Command]:
        """
        Handle mcp_server stopped event.

        Clears retry state for normally stopped mcp_servers.
        """
        mcp_server_id = event.mcp_server_id

        # Only clear state for intentional stops
        if event.reason in ("shutdown", "idle", "user_request", "detection_enforcement:block"):
            self._retry_state.pop(mcp_server_id, None)

        return []

    def _handle_health_failed(self, event: HealthCheckFailed) -> list[Command]:
        """
        Handle health check failed event.

        May trigger preemptive recovery for severely degraded mcp_servers.
        """
        # If failures are severe but mcp_server not yet degraded, no action.
        # The McpServerDegraded event will handle actual recovery.
        return []

    def _calculate_backoff(self, retry_count: int) -> float:
        """Calculate backoff duration for a retry count."""
        backoff = self._initial_backoff_s * (self._backoff_multiplier ** (retry_count - 1))
        return min(backoff, self._max_backoff_s)

    def get_retry_state(self, mcp_server_id: str) -> dict | None:
        """Get retry state for a mcp_server (for monitoring)."""
        return self._retry_state.get(mcp_server_id)

    def get_all_retry_states(self) -> dict[str, dict]:
        """Get all retry states (for monitoring)."""
        return dict(self._retry_state)

    def reset_retry_state(self, mcp_server_id: str) -> None:
        """Manually reset retry state for a mcp_server."""
        self._retry_state.pop(mcp_server_id, None)

    def reset_all_retry_states(self) -> None:
        """Reset all retry states."""
        self._retry_state.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize retry state for persistence."""
        return {"retry_state": dict(self._retry_state)}

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restore retry state from persistence."""
        self._retry_state = data.get("retry_state", {})


ProviderRecoverySaga = McpServerRecoverySaga
