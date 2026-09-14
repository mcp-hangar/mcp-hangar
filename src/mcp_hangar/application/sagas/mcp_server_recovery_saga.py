"""McpServer Recovery Saga - automatically recover degraded mcp_servers."""

# pyright: reportUnannotatedClassAttribute=false, reportMissingTypeArgument=false, reportImplicitOverride=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportImplicitStringConcatenation=false, reportUnusedCallResult=false, reportUnusedParameter=false, reportUnknownParameterType=false, reportExplicitAny=false

from dataclasses import dataclass
import math
import threading
import time

from typing import Any

from ...domain.events import DomainEvent, HealthCheckFailed, McpServerDegraded, McpServerStarted, McpServerStopped
from ...domain.exceptions import CannotStartMcpServerError
from ...application.ports.saga import EventTriggeredSaga, ISagaManager
from ...logging_config import get_logger
from ..commands import Command, GiveUpOnMcpServerCommand, StartMcpServerCommand

logger = get_logger(__name__)

#: Added to the retry time a server reports when it refuses a restart. The
#: server's backoff draws fresh jitter on every read, so its answer is a
#: moment's estimate, not a promise.
RESCHEDULE_MARGIN_S = 0.25
#: The soonest a refused restart is tried again. A server can refuse and, on
#: the next read, report 0 -- see above -- and must not be asked in a tight loop.
MIN_RESCHEDULE_DELAY_S = 1.0


@dataclass(eq=False)
class _Restart:
    """One scheduled restart, and how many refusals came before it in this attempt.

    Compared by identity: a restart is live while it is in the saga's list for
    its server. Starting, stopping or giving up empties that list, so a restart
    refused after that is not rescheduled.
    """

    refusals: int = 0
    timer_id: str | None = None


class McpServerRecoverySaga(EventTriggeredSaga):
    """
    Saga that orchestrates automatic mcp_server recovery after failures.

    Recovery Strategy:
    1. When a mcp_server is degraded, schedule a retry
    2. Apply exponential backoff between retries
    3. A restart the server refuses because its own backoff has not run out is
       scheduled again at the retry time it reports (#1401). The refusal is not
       an attempt: the start never ran. The server's backoff stays the only
       clock that decides when a start may run; the saga does not copy it.
    4. After max retries, give up: the mcp_server goes DEAD (#1361). Restarts
       still pending are cancelled, as they are when it starts or stops, so a
       stale one never starts it again.
    5. Reset retry count when mcp_server starts successfully

    Configuration:
    - max_retries: Maximum number of restart attempts (default: 3)
    - initial_backoff_s: Initial backoff duration in seconds (default: 5)
    - max_backoff_s: Maximum backoff duration (default: 60); also the longest a
      refused restart waits, whatever the server reports
    - backoff_multiplier: Backoff multiplier for exponential growth (default: 2)
    - max_refusals: Refusals one attempt may meet before they count as a failed
      attempt (default: 20). The loop guard: a server that keeps refusing, say
      with a retry time of 0, still spends the retry budget and is given up on.
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_backoff_s: float = 5.0,
        max_backoff_s: float = 60.0,
        backoff_multiplier: float = 2.0,
        *,
        saga_manager: ISagaManager,
        max_refusals: int = 20,
    ):
        super().__init__()

        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._backoff_multiplier = backoff_multiplier
        self._max_refusals = max_refusals
        self._saga_manager = saga_manager

        # Track retry state per mcp_server
        # mcp_server_id -> {"retries": int, "last_attempt": float, "next_retry": float}
        self._retry_state: dict[str, dict] = {}

        # Restarts scheduled per mcp_server, so giving up can cancel them. Not
        # persisted: a timer does not survive the process that armed it.
        self._pending_restarts: dict[str, list[_Restart]] = {}

        # Events reach the saga on whichever thread published them, and a
        # refused restart on its timer's thread. Never held while calling the
        # saga manager, so it nests inside the manager's lock and not around it.
        self._lock = threading.Lock()

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

        return self._attempt_failed(event.mcp_server_id)

    def _attempt_failed(self, mcp_server_id: str) -> list[Command]:
        """Count one failed attempt: schedule the next restart, or give up once the budget is spent."""
        with self._lock:
            state = self._retry_state.setdefault(mcp_server_id, {"retries": 0, "last_attempt": 0, "next_retry": 0})
            state["retries"] += 1
            state["last_attempt"] = time.time()
            retries = state["retries"]
            backoff = self._calculate_backoff(retries)
            if retries <= self._max_retries:
                state["next_retry"] = time.time() + backoff

        if retries > self._max_retries:
            logger.warning(f"McpServer {mcp_server_id} exceeded max retries ({self._max_retries}), giving up")
            # A restart still waiting to fire would start the server again after
            # this, and giving up means only a deliberate start or a call does.
            self._cancel_pending_restarts(mcp_server_id)
            return [GiveUpOnMcpServerCommand(mcp_server_id=mcp_server_id, reason="max_retries_exceeded")]

        logger.info(
            f"McpServer {mcp_server_id} degraded, scheduling retry {retries}/{self._max_retries} in {backoff:.1f}s"
        )

        # One restart at a time. A refused restart that is waiting to be tried
        # again is superseded by this attempt's, not run beside it.
        self._cancel_pending_restarts(mcp_server_id)
        self._arm_restart(mcp_server_id, backoff, refusals=0)
        return []

    def _arm_restart(self, mcp_server_id: str, delay_s: float, refusals: int) -> None:
        """Schedule a restart, registered before it is armed so a cancel can never miss it."""
        restart = _Restart(refusals)
        with self._lock:
            self._pending_restarts.setdefault(mcp_server_id, []).append(restart)

        timer_id = self._saga_manager.schedule_command(
            StartMcpServerCommand(mcp_server_id=mcp_server_id),
            delay_s=delay_s,
            on_failure=lambda error: self._restart_failed(mcp_server_id, restart, error),
        )

        with self._lock:
            restart.timer_id = timer_id
            live = restart in self._pending_restarts.get(mcp_server_id, ())
        if not live:
            # Cancelled while it was being armed, before it had a timer to cancel.
            self._saga_manager.cancel_scheduled_command(timer_id)

    def _restart_failed(self, mcp_server_id: str, restart: _Restart, error: Exception) -> list[Command]:
        """A scheduled restart raised. Reschedule it if the server refused it (#1401).

        ``CannotStartMcpServerError`` means no start ran: the server refused,
        because its backoff has not run out, or another start was already under
        way. Neither is an attempt of the saga's. Any other error comes from a
        start that ran and failed, and that start recorded ``McpServerDegraded``,
        which ``_handle_degraded`` has already counted.
        """
        if not isinstance(error, CannotStartMcpServerError):
            return []

        with self._lock:
            chain = self._pending_restarts.get(mcp_server_id, [])
            if restart not in chain:
                return []  # started, stopped or given up on since, or superseded
            chain.remove(restart)

        refusals = restart.refusals + 1
        if refusals > self._max_refusals:
            logger.warning(
                "mcp_server_recovery_restart_refused_too_often",
                mcp_server_id=mcp_server_id,
                refusals=refusals,
            )
            return self._attempt_failed(mcp_server_id)

        delay_s = self._refusal_delay(error.time_until_retry)
        with self._lock:
            state = self._retry_state.get(mcp_server_id)
            if state is not None:
                state["next_retry"] = time.time() + delay_s
        logger.info(
            "mcp_server_recovery_restart_rescheduled",
            mcp_server_id=mcp_server_id,
            server_retry_in_s=error.time_until_retry,
            delay_s=delay_s,
            refusals=refusals,
        )
        self._arm_restart(mcp_server_id, delay_s, refusals)
        return []

    def _refusal_delay(self, retry_in_s: float) -> float:
        """When to try a refused restart again: when the server said, plus a margin.

        No sooner than ``MIN_RESCHEDULE_DELAY_S`` and no later than the saga's
        own ``max_backoff_s``. A reported time that is not a number, or is
        negative, is read as 0.
        """
        retry_in_s = float(retry_in_s)
        if math.isnan(retry_in_s) or retry_in_s < 0:
            retry_in_s = 0.0
        return min(max(retry_in_s + RESCHEDULE_MARGIN_S, MIN_RESCHEDULE_DELAY_S), self._max_backoff_s)

    def _cancel_pending_restarts(self, mcp_server_id: str) -> None:
        """Cancel every restart scheduled for the server; one that already fired is a no-op."""
        with self._lock:
            restarts = self._pending_restarts.pop(mcp_server_id, [])
        for restart in restarts:
            # One still being armed has no timer yet; `_arm_restart` cancels it.
            if restart.timer_id is not None:
                self._saga_manager.cancel_scheduled_command(restart.timer_id)

    def _handle_started(self, event: McpServerStarted) -> list[Command]:
        """
        Handle mcp_server started event.

        Resets retry count on successful start.
        """
        mcp_server_id = event.mcp_server_id
        # Recovered. A restart still waiting would fire on a server that may by
        # then have gone cold or dead, and start it again.
        self._cancel_pending_restarts(mcp_server_id)

        with self._lock:
            old_retries = self._retry_state.get(mcp_server_id, {}).get("retries", 0)
            if mcp_server_id in self._retry_state:
                self._retry_state[mcp_server_id] = {"retries": 0, "last_attempt": 0, "next_retry": 0}
        if old_retries > 0:
            logger.info(f"McpServer {mcp_server_id} recovered successfully after {old_retries} retries")

        return []

    def _handle_stopped(self, event: McpServerStopped) -> list[Command]:
        """
        Handle mcp_server stopped event.

        Clears retry state for normally stopped mcp_servers.
        """
        mcp_server_id = event.mcp_server_id
        # A restart scheduled before the stop would undo it.
        self._cancel_pending_restarts(mcp_server_id)

        # Only clear state for intentional stops
        if event.reason in ("shutdown", "idle", "user_request", "detection_enforcement:block"):
            with self._lock:
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
        with self._lock:
            self._retry_state.pop(mcp_server_id, None)

    def reset_all_retry_states(self) -> None:
        """Reset all retry states."""
        with self._lock:
            self._retry_state.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize retry state for persistence."""
        return {"retry_state": dict(self._retry_state)}

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restore retry state from persistence."""
        self._retry_state = data.get("retry_state", {})


ProviderRecoverySaga = McpServerRecoverySaga
