"""Saga ports - pure saga abstractions for application layer.

Moving these out of infrastructure/saga_manager.py so the application layer
can import them without depending on infrastructure.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ...domain.events import DomainEvent

if TYPE_CHECKING:
    from ..commands import Command


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
    command: Command | None = None
    compensation_command: Command | None = None
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
    """Base class for step-based sagas.

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

    @abstractmethod
    def configure(self, context: SagaContext) -> None:
        """Configure saga steps based on context.

        Override this to define the saga's steps and their compensations.
        """

    def add_step(
        self,
        name: str,
        command: Command | None = None,
        compensation_command: Command | None = None,
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

    def on_step_failed(self, step: SagaStep, error: Exception) -> None:
        """Called when a step fails. Override to handle errors."""

    def on_saga_completed(self) -> None:
        """Called when the entire saga completes. Override to add finalization logic."""

    def on_saga_compensated(self) -> None:
        """Called after saga compensation completes. Override to add cleanup logic."""


class EventTriggeredSaga(ABC):
    """Saga triggered by domain events.

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

    @property
    @abstractmethod
    def handled_events(self) -> list[type[DomainEvent]]:
        """List of event types this saga handles."""

    @abstractmethod
    def handle(self, event: DomainEvent) -> list[Command]:
        """Handle a domain event and return commands to execute.

        Args:
            event: The domain event to handle.

        Returns:
            List of commands to send (can be empty).
        """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize saga state for persistence."""
        ...

    @abstractmethod
    def from_dict(self, data: dict[str, Any]) -> None:
        """Restore saga state from persistence."""
        ...

    def should_handle(self, event: DomainEvent) -> bool:
        """Check if this saga should handle the given event."""
        return type(event) in self.handled_events


class ISagaManager(ABC):
    """Interface for the saga manager.

    Application layer depends on this interface; infrastructure provides
    the concrete SagaManager.
    """

    @abstractmethod
    def register_event_saga(self, saga: EventTriggeredSaga) -> None:
        """Register an event-triggered saga.

        Args:
            saga: The saga to register.
        """

    @abstractmethod
    def start_saga(self, saga: Saga, initial_data: dict[str, Any] | None = None) -> SagaContext:
        """Start a new saga instance.

        Args:
            saga: The saga to start.
            initial_data: Initial context data for the saga.

        Returns:
            SagaContext for tracking the saga.
        """

    @abstractmethod
    def schedule_command(self, command: Command, delay_s: float) -> str:
        """Schedule a command to be sent after a delay.

        Args:
            command: The command to dispatch after the delay.
            delay_s: Delay in seconds before the command is sent.

        Returns:
            A unique timer ID that can be passed to cancel_scheduled_command.
        """

    @abstractmethod
    def cancel_scheduled_command(self, timer_id: str) -> bool:
        """Cancel a previously scheduled command.

        Args:
            timer_id: The timer ID returned by schedule_command.

        Returns:
            True if the timer was found and cancelled, False otherwise.
        """
