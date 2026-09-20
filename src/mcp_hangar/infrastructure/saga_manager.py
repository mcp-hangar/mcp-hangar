"""Saga Manager for orchestrating complex workflows.

Sagas coordinate long-running business processes that span multiple aggregates
or services. They react to domain events and emit commands.
"""

import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp_hangar.domain.contracts.event_bus import HandlerKind

from ..application.ports.saga import (  # noqa: F401 -- re-exported for backward compat
    EventTriggeredSaga,
    ISagaManager,
    Saga,
    SagaContext,
    SagaState,
    SagaStep,
)
from ..domain.events import DomainEvent
from ..errors import bounded_error_type
from ..lock_hierarchy import LockLevel, TrackedLock
from ..logging_config import get_logger
from .command_bus import CommandBus, get_command_bus
from .event_bus import EventBus, get_event_bus

if TYPE_CHECKING:
    from ..application.commands import Command
    from .persistence.saga_state_store import NullSagaStateStore, SagaStateStore

logger = get_logger(__name__)


class SagaManager(ISagaManager):
    """
    Manages saga lifecycle and execution.

    Responsibilities:
    - Start and track sagas
    - Execute saga steps
    - Handle compensation on failure
    - Route events to event-triggered sagas
    """

    def __init__(
        self,
        command_bus: CommandBus | None = None,
        event_bus: EventBus | None = None,
        saga_state_store: "SagaStateStore | NullSagaStateStore | None" = None,
    ):
        self._command_bus = command_bus or get_command_bus()
        self._event_bus = event_bus or get_event_bus()
        self._saga_state_store: SagaStateStore | NullSagaStateStore | None = saga_state_store

        # Active sagas being orchestrated
        self._active_sagas: dict[str, Saga] = {}

        # Event-triggered sagas (persistent)
        self._event_sagas: dict[str, EventTriggeredSaga] = {}

        # Completed saga history (for debugging)
        self._saga_history: list[SagaContext] = []
        self._max_history = 100

        # Pending scheduled commands: timer_id -> threading.Timer
        self._pending_timers: dict[str, threading.Timer] = {}
        # Bumped by cancel_all_scheduled_commands; see `_follow_up`.
        self._cancel_epoch = 0

        # Lock hierarchy level: SAGA_MANAGER (40)
        # Safe to acquire after: PROVIDER, EVENT_BUS, EVENT_STORE
        # Safe to acquire before: STDIO_CLIENT
        # Note: Command execution happens OUTSIDE this lock
        self._lock = TrackedLock(LockLevel.SAGA_MANAGER, "SagaManager")

        # Subscribe to all events for event-triggered sagas
        # An effect: a saga drives a workflow and issues commands. Three
        # replicas each driving the same saga would take every step three
        # times.
        self._event_bus.subscribe_to_all(self._handle_event, kind=HandlerKind.EFFECT)

    def register_event_saga(self, saga: EventTriggeredSaga) -> None:
        """Register an event-triggered saga."""
        with self._lock:
            self._event_sagas[saga.saga_type] = saga
            logger.info("event_saga_registered", saga_type=saga.saga_type)

    def unregister_event_saga(self, saga_type: str) -> bool:
        """Unregister an event-triggered saga."""
        with self._lock:
            if saga_type in self._event_sagas:
                del self._event_sagas[saga_type]
                found = True
            else:
                found = False
        return found

    def schedule_command(
        self,
        command: "Command",
        delay_s: float,
        *,
        on_failure: "Callable[[Exception], list[Command]] | None" = None,
    ) -> str:
        """
        Schedule a command to be sent after a delay.

        The command is dispatched via the command bus after ``delay_s`` seconds.
        The returned timer ID can be used to cancel the scheduled command before
        it fires.

        Thread-safety: the timer registry is protected by ``self._lock``.  The
        command is dispatched **outside** the lock so that command handlers can
        themselves call ``schedule_command`` without deadlocking.

        Args:
            command: The command to dispatch after the delay.
            delay_s: Delay in seconds before the command is sent.
            on_failure: Called with the exception if sending the command
                raises; see ``_follow_up``. Without it a failure is only
                logged, and whoever scheduled the command never hears of it.

        Returns:
            A unique timer ID that can be passed to ``cancel_scheduled_command``.
        """
        timer_id = str(uuid.uuid4())
        epoch = 0

        def _fire() -> None:
            with self._lock:
                self._pending_timers.pop(timer_id, None)
            try:
                self._command_bus.send(command)
                logger.debug(
                    "scheduled_command_dispatched",
                    timer_id=timer_id,
                    command=type(command).__name__,
                )
            except Exception as e:  # noqa: BLE001 -- fault-barrier: scheduled command failure must not crash timer thread
                logger.error(
                    "scheduled_command_failed",
                    timer_id=timer_id,
                    command=type(command).__name__,
                    # The type only: a failed start's text can carry what the upstream printed.
                    error_type=bounded_error_type(type(e).__qualname__),
                )
                if on_failure is not None:
                    self._follow_up(timer_id, command, on_failure, e, epoch)

        timer = threading.Timer(delay_s, _fire)
        timer.daemon = True

        with self._lock:
            self._pending_timers[timer_id] = timer
            epoch = self._cancel_epoch

        timer.start()
        logger.debug(
            "command_scheduled",
            timer_id=timer_id,
            command=type(command).__name__,
            delay_s=delay_s,
        )
        return timer_id

    def _follow_up(
        self,
        timer_id: str,
        command: "Command",
        on_failure: "Callable[[Exception], list[Command]]",
        error: Exception,
        epoch: int,
    ) -> None:
        """Run a failed scheduled command's ``on_failure``, then send what it returns.

        The hook runs under the lock, and ``cancel_all_scheduled_commands``
        takes the same lock. So a cancel either comes first, and the hook is
        skipped because ``epoch`` is stale, or it waits until whatever the hook
        scheduled is registered, and cancels that too. Shutdown's cancel
        therefore also stops the follow-up of a command that was firing when it
        ran (#1389). The hook must only decide and schedule; the commands it
        returns are sent after the lock is released.
        """
        with self._lock:
            if epoch != self._cancel_epoch:
                logger.info("scheduled_command_follow_up_cancelled", timer_id=timer_id, command=type(command).__name__)
                return
            try:
                follow_ups = on_failure(error)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: a failing hook must not crash the timer thread
                logger.error(
                    "scheduled_command_follow_up_failed",
                    timer_id=timer_id,
                    command=type(command).__name__,
                    error=str(e),
                )
                return
        for follow_up in follow_ups:
            try:
                self._command_bus.send(follow_up)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: one failed follow-up must not stop the next
                logger.error(
                    "scheduled_command_follow_up_failed",
                    command=type(follow_up).__name__,
                    error_type=bounded_error_type(type(e).__qualname__),
                )

    def cancel_scheduled_command(self, timer_id: str) -> bool:
        """
        Cancel a previously scheduled command.

        Args:
            timer_id: The timer ID returned by ``schedule_command``.

        Returns:
            True if the timer was found and cancelled, False otherwise.
        """
        with self._lock:
            timer = self._pending_timers.pop(timer_id, None)
        if timer is not None:
            timer.cancel()
            logger.debug("scheduled_command_cancelled", timer_id=timer_id)
            return True
        return False

    def cancel_all_scheduled_commands(self) -> int:
        """
        Cancel all pending scheduled commands.

        Returns:
            The number of timers cancelled.
        """
        with self._lock:
            timers = list(self._pending_timers.values())
            self._pending_timers.clear()
            # A timer already firing is no longer in the registry; this stops
            # what its failure would schedule next (see `_follow_up`).
            self._cancel_epoch += 1
        for timer in timers:
            timer.cancel()
        logger.debug("all_scheduled_commands_cancelled", count=len(timers))
        return len(timers)

    def start_saga(self, saga: Saga, initial_data: dict[str, Any] | None = None) -> SagaContext:
        """
        Start a new saga instance.

        Args:
            saga: The saga to start
            initial_data: Initial context data for the saga

        Returns:
            SagaContext for tracking the saga
        """
        with self._lock:
            # Create context
            context = SagaContext(
                data=initial_data or {},
                state=SagaState.RUNNING,
            )
            saga._context = context

            # Configure saga steps
            saga.configure(context)

            if not saga.steps:
                logger.warning(f"Saga {saga.saga_type} has no steps")
                context.state = SagaState.COMPLETED
                return context

            # Store active saga
            self._active_sagas[context.saga_id] = saga

            logger.info(f"Started saga {saga.saga_type} with ID {context.saga_id}")

        # Execute saga (outside lock to avoid deadlocks)
        self._execute_saga(context.saga_id)

        return context

    def _execute_saga(self, saga_id: str) -> None:
        """Execute saga steps sequentially."""
        with self._lock:
            saga = self._active_sagas.get(saga_id)
            if not saga or not saga.context:
                return
            context = saga.context

        try:
            while context.current_step < len(saga.steps):
                step = saga.steps[context.current_step]

                if step.command:
                    try:
                        result = self._command_bus.send(step.command)
                        step.completed = True
                        saga.on_step_completed(step, result)
                        logger.debug(f"Saga {saga_id} step '{step.name}' completed")
                    except (  # fault-barrier: step failure triggers compensation, must not crash saga executor
                        Exception  # noqa: BLE001
                    ) as e:
                        step.error = str(e)
                        saga.on_step_failed(step, e)
                        logger.error(
                            "saga_step_failed",
                            saga_id=saga_id,
                            step=step.name,
                            error_type=bounded_error_type(type(e).__qualname__),
                        )

                        # Start compensation
                        context.state = SagaState.COMPENSATING
                        context.error = str(e)
                        self._compensate_saga(saga_id)
                        return
                else:
                    # No command, just mark as completed
                    step.completed = True

                context.current_step += 1

            # All steps completed
            context.state = SagaState.COMPLETED
            saga.on_saga_completed()
            logger.info(f"Saga {saga_id} completed successfully")

        except Exception as e:  # noqa: BLE001 -- fault-barrier: unexpected saga failure must be recorded, not crash manager
            context.state = SagaState.FAILED
            context.error = str(e)
            logger.error(f"Saga {saga_id} failed unexpectedly: {e}")

        finally:
            self._finish_saga(saga_id)

    def _compensate_saga(self, saga_id: str) -> None:
        """Run compensation for a failed saga."""
        with self._lock:
            saga = self._active_sagas.get(saga_id)
            if not saga or not saga.context:
                return
            context = saga.context

        # Compensate completed steps in reverse order
        for i in range(context.current_step - 1, -1, -1):
            step = saga.steps[i]

            if step.completed and step.compensation_command:
                try:
                    self._command_bus.send(step.compensation_command)
                    step.compensated = True
                    logger.debug(f"Saga {saga_id} step '{step.name}' compensated")
                except Exception as e:  # noqa: BLE001 -- fault-barrier: compensation failure must not prevent other compensations
                    logger.error(
                        "saga_compensation_failed",
                        saga_id=saga_id,
                        step=step.name,
                        error_type=bounded_error_type(type(e).__qualname__),
                    )
                    # Continue compensating other steps

        context.state = SagaState.COMPENSATED
        saga.on_saga_compensated()
        logger.info(f"Saga {saga_id} compensated")

    def _finish_saga(self, saga_id: str) -> None:
        """Clean up finished saga."""
        with self._lock:
            saga = self._active_sagas.pop(saga_id, None)
            if saga and saga.context:
                # Add to history
                self._saga_history.append(saga.context)
                if len(self._saga_history) > self._max_history:
                    self._saga_history = self._saga_history[-self._max_history :]

    def _handle_event(self, event: DomainEvent) -> None:
        """Handle domain event for event-triggered sagas."""
        with self._lock:
            sagas = list(self._event_sagas.values())

        event_position = getattr(event, "global_position", None)

        for saga in sagas:
            if saga.should_handle(event):
                # Idempotency check: skip already-processed events
                if self._saga_state_store is not None and event_position is not None:
                    if self._saga_state_store.is_processed(saga.saga_type, event_position):
                        logger.debug(
                            "saga_event_already_processed",
                            saga_type=saga.saga_type,
                            event_position=event_position,
                        )
                        continue

                try:
                    commands = saga.handle(event)

                    # Checkpoint after successful handling (outside lock)
                    if self._saga_state_store is not None:
                        try:
                            self._saga_state_store.checkpoint(
                                saga_type=saga.saga_type,
                                saga_id=saga._saga_id,
                                state_data=saga.to_dict(),
                                last_event_position=event_position or 0,
                            )
                            if event_position is not None:
                                self._saga_state_store.mark_processed(
                                    saga.saga_type,
                                    event_position,
                                )
                        except Exception as e:  # noqa: BLE001 -- fault-barrier: checkpoint/mark failure must not break event handling
                            logger.error(
                                "saga_checkpoint_failed",
                                saga_type=saga.saga_type,
                                error=str(e),
                            )

                    for command in commands:
                        try:
                            self._command_bus.send(command)
                            logger.debug(f"Saga {saga.saga_type} sent command {type(command).__name__}")
                        except Exception as e:  # noqa: BLE001 -- fault-barrier: command failure must not crash event handler
                            logger.error(
                                "saga_command_failed",
                                saga_type=saga.saga_type,
                                command=type(command).__name__,
                                error_type=bounded_error_type(type(e).__qualname__),
                            )
                except Exception as e:  # noqa: BLE001 -- fault-barrier: saga handler failure must not crash event bus
                    logger.error(f"Saga {saga.saga_type} failed to handle event: {e}")

    def get_active_sagas(self) -> list[SagaContext]:
        """Get all active saga contexts."""
        with self._lock:
            result = [saga.context for saga in self._active_sagas.values() if saga.context]
        return result

    def get_saga_history(self, limit: int = 20) -> list[SagaContext]:
        """Get recent saga history."""
        with self._lock:
            result = list(reversed(self._saga_history[-limit:]))
        return result

    def get_saga(self, saga_id: str) -> Saga | None:
        """Get an active saga by ID."""
        with self._lock:
            result = self._active_sagas.get(saga_id)
        return result


# Singleton instance
_saga_manager: SagaManager | None = None


def get_saga_manager() -> SagaManager:
    """Get the global saga manager instance."""
    global _saga_manager
    if _saga_manager is None:
        _saga_manager = SagaManager()
    return _saga_manager


def set_saga_manager(manager: SagaManager) -> None:
    """Set the global saga manager instance."""
    global _saga_manager
    _saga_manager = manager
