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
from mcp_hangar.infrastructure.command_bus import CommandBus, CommandHandler, get_command_bus


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
