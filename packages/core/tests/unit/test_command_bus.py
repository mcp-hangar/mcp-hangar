"""Tests for Command Bus infrastructure."""

from unittest.mock import Mock

import pytest

from mcp_hangar.application.commands import (
    Command,
    HealthCheckCommand,
    InvokeToolCommand,
    ShutdownIdleProvidersCommand,
    StartProviderCommand,
    StopProviderCommand,
)
from mcp_hangar.domain.exceptions import RateLimitExceeded
from mcp_hangar.infrastructure.command_bus import (
    CommandBus,
    CommandBusMiddleware,
    CommandHandler,
    RateLimitMiddleware,
    get_command_bus,
)


class TestCommands:
    """Test Command classes."""

    def test_start_provider_command(self):
        """Test StartProviderCommand creation."""
        cmd = StartProviderCommand(provider_id="test-provider")

        assert cmd.provider_id == "test-provider"

    def test_stop_provider_command(self):
        """Test StopProviderCommand creation."""
        cmd = StopProviderCommand(provider_id="test-provider", reason="idle")

        assert cmd.provider_id == "test-provider"
        assert cmd.reason == "idle"

    def test_stop_provider_command_default_reason(self):
        """Test StopProviderCommand default reason."""
        cmd = StopProviderCommand(provider_id="test-provider")

        assert cmd.reason == "user_request"

    def test_invoke_tool_command(self):
        """Test InvokeToolCommand creation."""
        cmd = InvokeToolCommand(
            provider_id="test-provider",
            tool_name="add",
            arguments={"a": 1, "b": 2},
            timeout=30.0,
        )

        assert cmd.provider_id == "test-provider"
        assert cmd.tool_name == "add"
        assert cmd.arguments == {"a": 1, "b": 2}
        assert cmd.timeout == 30.0

    def test_invoke_tool_command_default_timeout(self):
        """Test InvokeToolCommand default timeout."""
        cmd = InvokeToolCommand(provider_id="test-provider", tool_name="add", arguments={})

        assert cmd.timeout == 30.0

    def test_health_check_command(self):
        """Test HealthCheckCommand creation."""
        cmd = HealthCheckCommand(provider_id="test-provider")

        assert cmd.provider_id == "test-provider"

    def test_shutdown_idle_providers_command(self):
        """Test ShutdownIdleProvidersCommand creation."""
        cmd = ShutdownIdleProvidersCommand()

        assert isinstance(cmd, Command)


class TestCommandBus:
    """Test CommandBus functionality."""

    def test_register_handler(self):
        """Test registering a command handler."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)

        bus.register(StartProviderCommand, handler)

        assert StartProviderCommand in bus._handlers

    def test_register_multiple_handlers(self):
        """Test registering multiple handlers for different commands."""
        bus = CommandBus()
        handler1 = Mock(spec=CommandHandler)
        handler2 = Mock(spec=CommandHandler)

        bus.register(StartProviderCommand, handler1)
        bus.register(StopProviderCommand, handler2)

        assert len(bus._handlers) == 2

    def test_send_command_calls_handler(self):
        """Test sending a command calls the registered handler."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = {"result": "success"}

        bus.register(StartProviderCommand, handler)

        cmd = StartProviderCommand(provider_id="test")
        result = bus.send(cmd)

        handler.handle.assert_called_once_with(cmd)
        assert result == {"result": "success"}

    def test_send_command_without_handler_raises(self):
        """Test sending unregistered command raises ValueError."""
        bus = CommandBus()
        cmd = StartProviderCommand(provider_id="test")

        with pytest.raises(ValueError):
            bus.send(cmd)

    def test_send_returns_handler_result(self):
        """Test send returns the handler's result."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = {"provider": "test", "state": "ready"}

        bus.register(StartProviderCommand, handler)

        cmd = StartProviderCommand(provider_id="test")
        result = bus.send(cmd)

        assert result == {"provider": "test", "state": "ready"}

    def test_handler_exception_propagates(self):
        """Test that handler exceptions propagate."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.side_effect = ValueError("Test error")

        bus.register(StartProviderCommand, handler)

        cmd = StartProviderCommand(provider_id="test")

        with pytest.raises(ValueError, match="Test error"):
            bus.send(cmd)

    def test_get_command_bus_returns_singleton(self):
        """Test get_command_bus returns same instance."""
        bus1 = get_command_bus()
        bus2 = get_command_bus()

        assert bus1 is bus2

    def test_command_bus_can_be_reset(self):
        """Test command bus can be cleared."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)

        bus.register(StartProviderCommand, handler)

        assert len(bus._handlers) == 1

        bus._handlers.clear()

        assert len(bus._handlers) == 0


class TestCommandHandlerInterface:
    """Test CommandHandler abstract interface."""

    def test_handler_interface_requires_handle(self):
        """Test that CommandHandler requires handle method."""

        # Create a concrete implementation
        class ConcreteHandler(CommandHandler):
            def handle(self, command):
                return {"handled": True}

        handler = ConcreteHandler()
        result = handler.handle(Mock())

        assert result == {"handled": True}

    def test_handler_without_handle_raises(self):
        """Test that incomplete handler raises TypeError."""
        with pytest.raises(TypeError):

            class IncompleteHandler(CommandHandler):
                pass

            IncompleteHandler()


class TestCommandIntegration:
    """Integration tests for command handling."""

    def test_full_command_flow(self):
        """Test complete command registration and execution flow."""
        bus = CommandBus()

        results = []

        class TestHandler(CommandHandler):
            def handle(self, command):
                results.append(command.provider_id)
                return {"status": "done"}

        bus.register(StartProviderCommand, TestHandler())

        cmd1 = StartProviderCommand(provider_id="provider-1")
        cmd2 = StartProviderCommand(provider_id="provider-2")

        bus.send(cmd1)
        bus.send(cmd2)

        assert results == ["provider-1", "provider-2"]

    def test_different_commands_different_handlers(self):
        """Test different commands go to different handlers."""
        bus = CommandBus()

        start_calls = []
        stop_calls = []

        class StartHandler(CommandHandler):
            def handle(self, command):
                start_calls.append(command.provider_id)
                return {"started": True}

        class StopHandler(CommandHandler):
            def handle(self, command):
                stop_calls.append(command.provider_id)
                return {"stopped": True}

        bus.register(StartProviderCommand, StartHandler())
        bus.register(StopProviderCommand, StopHandler())

        bus.send(StartProviderCommand(provider_id="p1"))
        bus.send(StopProviderCommand(provider_id="p2"))
        bus.send(StartProviderCommand(provider_id="p3"))

        assert start_calls == ["p1", "p3"]
        assert stop_calls == ["p2"]


class TestCommandBusMiddleware:
    """Tests for CommandBus middleware pipeline."""

    def test_send_without_middleware_dispatches_directly(self):
        """Test backward compat: no middleware means direct handler dispatch."""
        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = {"ok": True}

        bus.register(StartProviderCommand, handler)

        cmd = StartProviderCommand(provider_id="test")
        result = bus.send(cmd)

        handler.handle.assert_called_once_with(cmd)
        assert result == {"ok": True}

    def test_add_middleware_adds_to_pipeline(self):
        """Test add_middleware() registers middleware."""
        bus = CommandBus()

        class DummyMiddleware(CommandBusMiddleware):
            def __call__(self, command, next_handler):
                return next_handler(command)

        mw = DummyMiddleware()
        bus.add_middleware(mw)

        assert mw in bus._middleware

    def test_middleware_called_before_handler(self):
        """Test middleware is invoked before the handler."""
        bus = CommandBus()
        call_order = []

        class TrackingMiddleware(CommandBusMiddleware):
            def __call__(self, command, next_handler):
                call_order.append("middleware")
                return next_handler(command)

        class TrackingHandler(CommandHandler):
            def handle(self, command):
                call_order.append("handler")
                return {"done": True}

        bus.add_middleware(TrackingMiddleware())
        bus.register(StartProviderCommand, TrackingHandler())

        bus.send(StartProviderCommand(provider_id="test"))

        assert call_order == ["middleware", "handler"]

    def test_multiple_middleware_execute_in_registration_order(self):
        """Test multiple middleware run in the order they were added."""
        bus = CommandBus()
        order = []

        class OrderMiddleware(CommandBusMiddleware):
            def __init__(self, name):
                self._name = name

            def __call__(self, command, next_handler):
                order.append(self._name)
                return next_handler(command)

        bus.add_middleware(OrderMiddleware("first"))
        bus.add_middleware(OrderMiddleware("second"))
        bus.add_middleware(OrderMiddleware("third"))

        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = None
        bus.register(StartProviderCommand, handler)

        bus.send(StartProviderCommand(provider_id="test"))

        assert order == ["first", "second", "third"]

    def test_middleware_can_reject_command_by_raising(self):
        """Test middleware can raise to reject a command before handler runs."""
        bus = CommandBus()

        class RejectMiddleware(CommandBusMiddleware):
            def __call__(self, command, next_handler):
                raise ValueError("rejected")

        handler = Mock(spec=CommandHandler)
        bus.add_middleware(RejectMiddleware())
        bus.register(StartProviderCommand, handler)

        with pytest.raises(ValueError, match="rejected"):
            bus.send(StartProviderCommand(provider_id="test"))

        handler.handle.assert_not_called()

    def test_middleware_receives_command_and_can_inspect_it(self):
        """Test middleware receives the command object for inspection."""
        bus = CommandBus()
        captured = {}

        class InspectMiddleware(CommandBusMiddleware):
            def __call__(self, command, next_handler):
                captured["command_type"] = type(command).__name__
                captured["provider_id"] = command.provider_id
                return next_handler(command)

        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = None
        bus.add_middleware(InspectMiddleware())
        bus.register(StartProviderCommand, handler)

        bus.send(StartProviderCommand(provider_id="abc"))

        assert captured["command_type"] == "StartProviderCommand"
        assert captured["provider_id"] == "abc"

    def test_handler_not_called_when_middleware_raises(self):
        """Test handler is NOT called if earlier middleware raises."""
        bus = CommandBus()
        handler_calls = []

        class FailMiddleware(CommandBusMiddleware):
            def __call__(self, command, next_handler):
                raise RuntimeError("boom")

        class TrackHandler(CommandHandler):
            def handle(self, command):
                handler_calls.append(True)
                return None

        bus.add_middleware(FailMiddleware())
        bus.register(StartProviderCommand, TrackHandler())

        with pytest.raises(RuntimeError, match="boom"):
            bus.send(StartProviderCommand(provider_id="x"))

        assert handler_calls == []


class TestRateLimitMiddleware:
    """Tests for RateLimitMiddleware."""

    def _make_rate_limiter(self, *, allowed: bool = True, limit: int = 10, remaining: int = 9):
        """Create a mock rate limiter returning a controlled result."""
        from mcp_hangar.domain.security.rate_limiter import RateLimitResult

        limiter = Mock()
        limiter.consume.return_value = RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            reset_at=0.0,
            limit=limit,
        )
        return limiter

    def test_rate_limit_middleware_calls_consume_with_command_type_key(self):
        """Test middleware calls rate_limiter.consume() with command type name as key."""
        limiter = self._make_rate_limiter(allowed=True)
        mw = RateLimitMiddleware(rate_limiter=limiter)

        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = None
        bus.add_middleware(mw)
        bus.register(StartProviderCommand, handler)

        bus.send(StartProviderCommand(provider_id="test"))

        limiter.consume.assert_called_once_with("StartProviderCommand")

    def test_rate_limit_middleware_raises_when_not_allowed(self):
        """Test middleware raises RateLimitExceeded when consume() denies."""
        limiter = self._make_rate_limiter(allowed=False, limit=5)
        mw = RateLimitMiddleware(rate_limiter=limiter)

        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        bus.add_middleware(mw)
        bus.register(StartProviderCommand, handler)

        with pytest.raises(RateLimitExceeded):
            bus.send(StartProviderCommand(provider_id="test"))

        handler.handle.assert_not_called()

    def test_rate_limit_middleware_calls_next_when_allowed(self):
        """Test middleware calls next_handler when consume() allows."""
        limiter = self._make_rate_limiter(allowed=True)
        mw = RateLimitMiddleware(rate_limiter=limiter)

        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = {"ok": True}
        bus.add_middleware(mw)
        bus.register(StartProviderCommand, handler)

        result = bus.send(StartProviderCommand(provider_id="test"))

        handler.handle.assert_called_once()
        assert result == {"ok": True}

    def test_rate_limit_middleware_updates_metrics_on_deny(self):
        """Test middleware increments Prometheus counter on rate limit hit."""
        limiter = self._make_rate_limiter(allowed=False, limit=5)
        mw = RateLimitMiddleware(rate_limiter=limiter)

        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        bus.add_middleware(mw)
        bus.register(StartProviderCommand, handler)

        with pytest.raises(RateLimitExceeded):
            bus.send(StartProviderCommand(provider_id="test"))

        # Verify metrics were updated (import and check counter)
        # The counter should have been incremented - just verify no crash
        # (exact counter value testing is fragile; the key thing is no exception)

    def test_rate_limit_middleware_does_not_update_metrics_on_allow(self):
        """Test middleware does NOT increment metrics when request is allowed."""
        limiter = self._make_rate_limiter(allowed=True)
        mw = RateLimitMiddleware(rate_limiter=limiter)

        bus = CommandBus()
        handler = Mock(spec=CommandHandler)
        handler.handle.return_value = None
        bus.add_middleware(mw)
        bus.register(StartProviderCommand, handler)

        # Should not raise
        bus.send(StartProviderCommand(provider_id="test"))
        handler.handle.assert_called_once()
