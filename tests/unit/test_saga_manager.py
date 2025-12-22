"""Tests for Saga Manager infrastructure."""

from typing import List

from mcp_hangar.domain.events import DomainEvent, ProviderDegraded, ProviderStarted
from mcp_hangar.infrastructure.command_bus import (
    Command,
    CommandBus,
    CommandHandler,
    StartProviderCommand,
    StopProviderCommand,
)
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.saga_manager import (
    EventTriggeredSaga,
    Saga,
    SagaContext,
    SagaManager,
    SagaState,
    SagaStep,
)


class TestSagaContext:
    """Test SagaContext dataclass."""

    def test_context_creation(self):
        """Test creating saga context."""
        context = SagaContext()

        assert context.saga_id is not None
        assert context.correlation_id is not None
        assert context.state == SagaState.NOT_STARTED
        assert context.current_step == 0
        assert context.error is None

    def test_context_with_data(self):
        """Test creating context with data."""
        context = SagaContext(data={"provider_id": "p1"})

        assert context.data["provider_id"] == "p1"

    def test_context_to_dict(self):
        """Test context to dictionary conversion."""
        context = SagaContext(data={"key": "value"})

        d = context.to_dict()

        assert "saga_id" in d
        assert d["state"] == "not_started"
        assert d["data"] == {"key": "value"}


class TestSagaStep:
    """Test SagaStep dataclass."""

    def test_step_creation(self):
        """Test creating saga step."""
        step = SagaStep(name="start_provider", command=StartProviderCommand(provider_id="p1"))

        assert step.name == "start_provider"
        assert not step.completed
        assert not step.compensated

    def test_step_with_compensation(self):
        """Test step with compensation command."""
        step = SagaStep(
            name="start_provider",
            command=StartProviderCommand(provider_id="p1"),
            compensation_command=StopProviderCommand(provider_id="p1", reason="rollback"),
        )

        assert step.compensation_command is not None


class SimpleSaga(Saga):
    """Simple saga for testing."""

    @property
    def saga_type(self) -> str:
        return "simple_saga"

    def configure(self, context: SagaContext) -> None:
        provider_id = context.data.get("provider_id", "default")
        self.add_step(
            name="start",
            command=StartProviderCommand(provider_id=provider_id),
            compensation_command=StopProviderCommand(provider_id=provider_id, reason="rollback"),
        )


class FailingSaga(Saga):
    """Saga that fails on second step for testing compensation."""

    @property
    def saga_type(self) -> str:
        return "failing_saga"

    def configure(self, context: SagaContext) -> None:
        self.add_step(
            name="step1",
            command=StartProviderCommand(provider_id="p1"),
            compensation_command=StopProviderCommand(provider_id="p1", reason="rollback"),
        )
        self.add_step(
            name="step2",
            command=StartProviderCommand(provider_id="fail"),  # Will fail
            compensation_command=StopProviderCommand(provider_id="fail", reason="rollback"),
        )


class SimpleEventSaga(EventTriggeredSaga):
    """Simple event-triggered saga for testing."""

    def __init__(self):
        super().__init__()
        self.handled_events_list = []

    @property
    def saga_type(self) -> str:
        return "simple_event_saga"

    @property
    def handled_events(self) -> list:
        return [ProviderDegraded]

    def handle(self, event: DomainEvent) -> List[Command]:
        self.handled_events_list.append(event)
        if isinstance(event, ProviderDegraded):
            return [StartProviderCommand(provider_id=event.provider_id)]
        return []


class TestSaga:
    """Test Saga base class."""

    def test_add_step(self):
        """Test adding steps to saga."""
        saga = SimpleSaga()
        context = SagaContext()

        saga.configure(context)

        assert len(saga.steps) == 1
        assert saga.steps[0].name == "start"

    def test_saga_context(self):
        """Test saga context is set."""
        saga = SimpleSaga()
        context = SagaContext(data={"provider_id": "test"})
        saga._context = context

        assert saga.context is not None
        assert saga.context.data["provider_id"] == "test"


class TestEventTriggeredSaga:
    """Test EventTriggeredSaga."""

    def test_should_handle(self):
        """Test should_handle method."""
        saga = SimpleEventSaga()

        degraded = ProviderDegraded("p1", 3, 5, "error")
        started = ProviderStarted("p1", "subprocess", 5, 100.0)

        assert saga.should_handle(degraded)
        assert not saga.should_handle(started)

    def test_handle_returns_commands(self):
        """Test handle returns commands."""
        saga = SimpleEventSaga()

        event = ProviderDegraded("p1", 3, 5, "error")
        commands = saga.handle(event)

        assert len(commands) == 1
        assert isinstance(commands[0], StartProviderCommand)


class TestSagaManager:
    """Test SagaManager."""

    def test_create_manager(self):
        """Test creating saga manager."""
        command_bus = CommandBus()
        event_bus = EventBus()

        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        assert manager is not None

    def test_register_event_saga(self):
        """Test registering event-triggered saga."""
        command_bus = CommandBus()
        event_bus = EventBus()
        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        saga = SimpleEventSaga()
        manager.register_event_saga(saga)

        assert "simple_event_saga" in manager._event_sagas

    def test_unregister_event_saga(self):
        """Test unregistering event-triggered saga."""
        command_bus = CommandBus()
        event_bus = EventBus()
        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        saga = SimpleEventSaga()
        manager.register_event_saga(saga)

        result = manager.unregister_event_saga("simple_event_saga")

        assert result is True
        assert "simple_event_saga" not in manager._event_sagas

    def test_start_saga(self):
        """Test starting a saga."""
        command_bus = CommandBus()
        event_bus = EventBus()

        # Register handler that succeeds
        class SuccessHandler(CommandHandler):
            def handle(self, command):
                return {"status": "ok"}

        command_bus.register(StartProviderCommand, SuccessHandler())
        command_bus.register(StopProviderCommand, SuccessHandler())

        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        saga = SimpleSaga()
        context = manager.start_saga(saga, {"provider_id": "test"})

        assert context.state == SagaState.COMPLETED

    def test_saga_with_failing_step(self):
        """Test saga compensation on failure."""
        command_bus = CommandBus()
        event_bus = EventBus()

        call_log = []

        class TrackingHandler(CommandHandler):
            def handle(self, command):
                call_log.append(command)
                if command.provider_id == "fail":
                    raise ValueError("Intentional failure")
                return {"status": "ok"}

        command_bus.register(StartProviderCommand, TrackingHandler())
        command_bus.register(StopProviderCommand, TrackingHandler())

        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        saga = FailingSaga()
        context = manager.start_saga(saga)

        # Should be compensated
        assert context.state == SagaState.COMPENSATED
        assert context.error is not None

        # First step should have been compensated
        assert any(isinstance(c, StopProviderCommand) and c.provider_id == "p1" for c in call_log)

    def test_event_triggers_saga(self):
        """Test that events trigger saga handlers."""
        command_bus = CommandBus()
        event_bus = EventBus()

        commands_sent = []

        class CaptureHandler(CommandHandler):
            def handle(self, command):
                commands_sent.append(command)
                return {"status": "ok"}

        command_bus.register(StartProviderCommand, CaptureHandler())

        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        saga = SimpleEventSaga()
        manager.register_event_saga(saga)

        # Publish event
        event = ProviderDegraded("p1", 3, 5, "error")
        event_bus.publish(event)

        assert len(commands_sent) == 1
        assert commands_sent[0].provider_id == "p1"

    def test_get_active_sagas(self):
        """Test getting active sagas."""
        command_bus = CommandBus()
        event_bus = EventBus()
        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        # Initially no active sagas
        assert len(manager.get_active_sagas()) == 0

    def test_get_saga_history(self):
        """Test getting saga history."""
        command_bus = CommandBus()
        event_bus = EventBus()

        class SuccessHandler(CommandHandler):
            def handle(self, command):
                return {"status": "ok"}

        command_bus.register(StartProviderCommand, SuccessHandler())
        command_bus.register(StopProviderCommand, SuccessHandler())

        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        # Run a saga
        saga = SimpleSaga()
        manager.start_saga(saga, {"provider_id": "test"})

        history = manager.get_saga_history()

        assert len(history) >= 1
        assert history[0].state == SagaState.COMPLETED

    def test_get_saga_by_id(self):
        """Test getting saga by ID (while active)."""
        command_bus = CommandBus()
        event_bus = EventBus()
        manager = SagaManager(command_bus=command_bus, event_bus=event_bus)

        # Completed sagas won't be found (only active)
        result = manager.get_saga("nonexistent")
        assert result is None
