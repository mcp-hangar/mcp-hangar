"""Saga Manager for orchestrating complex workflows.

Sagas coordinate long-running business processes that span multiple aggregates
or services. They react to domain events and emit commands.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Optional, TYPE_CHECKING
import uuid

from ..domain.events import DomainEvent
from ..logging_config import get_logger
from .command_bus import CommandBus, get_command_bus
from .event_bus import EventBus, get_event_bus
from .lock_hierarchy import LockLevel, TrackedLock

if TYPE_CHECKING:
    from ..application.commands import Command

logger = get_logger(__name__)


class SagaState(Enum):
    """Saga lifecycle states."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"


@dataclass
class SagaStep:
    """A single step in a saga."""

    name: str
    command: Optional["Command"] = None
    compensation_command: Optional["Command"] = None
    completed: bool = False
    compensated: bool = False
    error: str | None = None


@dataclass
class SagaContext:
    """Context for saga execution with correlation data."""

    saga_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    state: SagaState = SagaState.NOT_STARTED
    current_step: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "saga_id": self.saga_id,
            "correlation_id": self.correlation_id,
            "started_at": self.started_at,
            "data": self.data,
            "state": self.state.value,
            "current_step": self.current_step,
            "error": self.error,
        }


class Saga(ABC):
    """
    Base class for sagas.

    A saga is a sequence of local transactions where each step has
    a compensating action that can undo its effects if a later step fails.
    """

    def __init__(self):
        self._steps: list[SagaStep] = []
        self._context: SagaContext | None = None

    @property
    @abstractmethod
    def saga_type(self) -> str:
        """Unique identifier for this saga type."""
        pass

    @abstractmethod
    def configure(self, context: SagaContext) -> None:
        """
        Configure saga steps based on context.

        Override this to define the saga's steps and their compensations.
        """
        pass

    def add_step(
        self,
        name: str,
        command: Optional["Command"] = None,
        compensation_command: Optional["Command"] = None,
    ) -> None:
        """Add a step to the saga."""
        self._steps.append(
            SagaStep(
                name=name,
                command=command,
                compensation_command=compensation_command,
            )
        )

    @property
    def steps(self) -> list[SagaStep]:
        """Get saga steps."""
        return list(self._steps)

    @property
    def context(self) -> SagaContext | None:
        """Get saga context."""
        return self._context

    def on_step_completed(self, step: SagaStep, result: Any) -> None:
        """Called when a step completes successfully. Override to handle results."""
        pass

    def on_step_failed(self, step: SagaStep, error: Exception) -> None:
        """Called when a step fails. Override to handle errors."""
        pass

    def on_saga_completed(self) -> None:
        """Called when the entire saga completes. Override to add finalization logic."""
        pass

    def on_saga_compensated(self) -> None:
        """Called after saga compensation completes. Override to add cleanup logic."""
        pass


class EventTriggeredSaga(ABC):
    """
    Saga that is triggered by domain events.

    Unlike step-based sagas, event-triggered sagas react to events
    and decide what commands to send based on their current state.
    """

    def __init__(self):
        self._state: dict[str, Any] = {}
        self._saga_id = str(uuid.uuid4())

    @property
    @abstractmethod
    def saga_type(self) -> str:
        """Unique identifier for this saga type."""
        pass

    @property
    @abstractmethod
    def handled_events(self) -> list[type[DomainEvent]]:
        """List of event types this saga handles."""
        pass

    @abstractmethod
    def handle(self, event: DomainEvent) -> list["Command"]:
        """
        Handle a domain event and return commands to execute.

        Args:
            event: The domain event to handle

        Returns:
            List of commands to send (can be empty)
        """
        pass

    def should_handle(self, event: DomainEvent) -> bool:
        """Check if this saga should handle the given event."""
        return type(event) in self.handled_events


class SagaManager:
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
    ):
        self._command_bus = command_bus or get_command_bus()
        self._event_bus = event_bus or get_event_bus()

        # Active sagas being orchestrated
        self._active_sagas: dict[str, Saga] = {}

        # Event-triggered sagas (persistent)
        self._event_sagas: dict[str, EventTriggeredSaga] = {}

        # Completed saga history (for debugging)
        self._saga_history: list[SagaContext] = []
        self._max_history = 100

        # Lock hierarchy level: SAGA_MANAGER (40)
        # Safe to acquire after: PROVIDER, EVENT_BUS, EVENT_STORE
        # Safe to acquire before: STDIO_CLIENT
        # Note: Command execution happens OUTSIDE this lock
        self._lock = TrackedLock(LockLevel.SAGA_MANAGER, "SagaManager")

        # Subscribe to all events for event-triggered sagas
        self._event_bus.subscribe_to_all(self._handle_event)

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
                return True
            return False

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
                    except Exception as e:
                        step.error = str(e)
                        saga.on_step_failed(step, e)
                        logger.error(f"Saga {saga_id} step '{step.name}' failed: {e}")

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

        except Exception as e:
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
                except Exception as e:
                    logger.error(f"Saga {saga_id} compensation for '{step.name}' failed: {e}")
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

        for saga in sagas:
            if saga.should_handle(event):
                try:
                    commands = saga.handle(event)
                    for command in commands:
                        try:
                            self._command_bus.send(command)
                            logger.debug(f"Saga {saga.saga_type} sent command {type(command).__name__}")
                        except Exception as e:
                            logger.error(f"Saga {saga.saga_type} command failed: {e}")
                except Exception as e:
                    logger.error(f"Saga {saga.saga_type} failed to handle event: {e}")

    def get_active_sagas(self) -> list[SagaContext]:
        """Get all active saga contexts."""
        with self._lock:
            return [saga.context for saga in self._active_sagas.values() if saga.context]

    def get_saga_history(self, limit: int = 20) -> list[SagaContext]:
        """Get recent saga history."""
        with self._lock:
            return list(reversed(self._saga_history[-limit:]))

    def get_saga(self, saga_id: str) -> Saga | None:
        """Get an active saga by ID."""
        with self._lock:
            return self._active_sagas.get(saga_id)


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
