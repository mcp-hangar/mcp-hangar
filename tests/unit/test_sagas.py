"""Tests for Provider Recovery and Failover Sagas."""

from unittest.mock import MagicMock

from mcp_hangar.application.commands import StartMcpServerCommand, StopMcpServerCommand
from mcp_hangar.application.sagas.mcp_server_failover_saga import McpServerFailoverEventSaga, McpServerFailoverSaga
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from mcp_hangar.domain.events import HealthCheckFailed, McpServerDegraded, McpServerStarted, McpServerStopped


class TestMcpServerRecoverySaga:
    """Test McpServerRecoverySaga."""

    def test_saga_type(self):
        """Test saga type identifier."""
        saga = McpServerRecoverySaga()

        assert saga.saga_type == "mcp_server_recovery"

    def test_handled_events(self):
        """Test that saga handles correct events."""
        saga = McpServerRecoverySaga()

        assert McpServerDegraded in saga.handled_events
        assert McpServerStarted in saga.handled_events
        assert McpServerStopped in saga.handled_events
        assert HealthCheckFailed in saga.handled_events

    def test_handle_degraded_first_time(self):
        """Test handling first degradation schedules a restart via SagaManager."""
        mock_saga_manager = MagicMock()
        saga = McpServerRecoverySaga(max_retries=3, saga_manager=mock_saga_manager)

        event = McpServerDegraded("p1", 3, 5, "timeout")
        commands = saga.handle(event)

        # No immediate commands; restart is scheduled via SagaManager
        assert len(commands) == 0
        mock_saga_manager.schedule_command.assert_called_once()
        call_args = mock_saga_manager.schedule_command.call_args
        assert isinstance(call_args[0][0], StartMcpServerCommand)
        assert call_args[0][0].mcp_server_id == "p1"

        # Check retry state
        state = saga.get_retry_state("p1")
        assert state["retries"] == 1

    def test_handle_degraded_multiple_times(self):
        """Test handling multiple degradations."""
        saga = McpServerRecoverySaga(max_retries=3)

        # First degradation
        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        assert saga.get_retry_state("p1")["retries"] == 1

        # Second degradation
        saga.handle(McpServerDegraded("p1", 2, 2, "error"))
        assert saga.get_retry_state("p1")["retries"] == 2

        # Third degradation
        saga.handle(McpServerDegraded("p1", 3, 3, "error"))
        assert saga.get_retry_state("p1")["retries"] == 3

    def test_handle_degraded_max_retries_exceeded(self):
        """Test that max retries triggers stop command."""
        saga = McpServerRecoverySaga(max_retries=2)

        # First two retries
        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        saga.handle(McpServerDegraded("p1", 2, 2, "error"))

        # Third attempt - should exceed max
        commands = saga.handle(McpServerDegraded("p1", 3, 3, "error"))

        assert len(commands) == 1
        assert isinstance(commands[0], StopMcpServerCommand)
        assert commands[0].reason == "max_retries_exceeded"

    def test_handle_started_resets_retry_count(self):
        """Test that successful start resets retry count."""
        saga = McpServerRecoverySaga(max_retries=3)

        # Build up retries
        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        saga.handle(McpServerDegraded("p1", 2, 2, "error"))
        assert saga.get_retry_state("p1")["retries"] == 2

        # Successful start
        saga.handle(McpServerStarted("p1", "subprocess", 5, 100.0))

        assert saga.get_retry_state("p1")["retries"] == 0

    def test_handle_stopped_clears_state_for_normal_stop(self):
        """Test that normal stops clear retry state."""
        saga = McpServerRecoverySaga()

        # Build up state
        saga.handle(McpServerDegraded("p1", 1, 1, "error"))

        # Normal shutdown
        saga.handle(McpServerStopped("p1", "shutdown"))

        assert saga.get_retry_state("p1") is None

    def test_handle_stopped_keeps_state_for_error_stop(self):
        """Test that error stops keep retry state."""
        saga = McpServerRecoverySaga()

        saga.handle(McpServerDegraded("p1", 1, 1, "error"))

        # Error stop
        saga.handle(McpServerStopped("p1", "error"))

        # State should still exist
        assert saga.get_retry_state("p1") is not None

    def test_backoff_calculation(self):
        """Test exponential backoff calculation."""
        saga = McpServerRecoverySaga(initial_backoff_s=5.0, max_backoff_s=60.0, backoff_multiplier=2.0)

        assert saga._calculate_backoff(1) == 5.0
        assert saga._calculate_backoff(2) == 10.0
        assert saga._calculate_backoff(3) == 20.0
        assert saga._calculate_backoff(4) == 40.0
        assert saga._calculate_backoff(5) == 60.0  # Capped at max
        assert saga._calculate_backoff(6) == 60.0  # Still capped

    def test_get_all_retry_states(self):
        """Test getting all retry states."""
        saga = McpServerRecoverySaga()

        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        saga.handle(McpServerDegraded("p2", 2, 2, "error"))

        states = saga.get_all_retry_states()

        assert "p1" in states
        assert "p2" in states

    def test_reset_retry_state(self):
        """Test manually resetting retry state."""
        saga = McpServerRecoverySaga()

        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        saga.reset_retry_state("p1")

        assert saga.get_retry_state("p1") is None

    def test_reset_all_retry_states(self):
        """Test resetting all retry states."""
        saga = McpServerRecoverySaga()

        saga.handle(McpServerDegraded("p1", 1, 1, "error"))
        saga.handle(McpServerDegraded("p2", 1, 1, "error"))

        saga.reset_all_retry_states()

        assert len(saga.get_all_retry_states()) == 0


class TestMcpServerFailoverSaga:
    """Test McpServerFailoverSaga (step-based) and McpServerFailoverEventSaga (event-driven)."""

    def test_failover_saga_type(self):
        """Test step-based saga type identifier."""
        saga = McpServerFailoverSaga()

        assert saga.saga_type == "mcp_server_failover"

    def test_failover_saga_configure_defines_three_steps(self):
        """Test that configure() defines exactly 3 steps with compensation commands."""
        from mcp_hangar.infrastructure.saga_manager import SagaContext, SagaState

        saga = McpServerFailoverSaga()
        context = SagaContext(
            data={"primary_id": "primary", "backup_id": "backup"},
            state=SagaState.RUNNING,
        )
        saga.configure(context)

        assert len(saga.steps) == 3
        assert saga.steps[0].name == "start_backup"
        assert isinstance(saga.steps[0].command, StartMcpServerCommand)
        assert isinstance(saga.steps[0].compensation_command, StopMcpServerCommand)
        assert saga.steps[1].name == "await_primary"
        assert saga.steps[1].command is None
        assert saga.steps[2].name == "failback"
        assert isinstance(saga.steps[2].command, StopMcpServerCommand)
        assert isinstance(saga.steps[2].compensation_command, StartMcpServerCommand)

    def test_failover_event_saga_type(self):
        """Test event saga type identifier."""
        saga = McpServerFailoverEventSaga()

        assert saga.saga_type == "mcp_server_failover_event"

    def test_event_saga_handled_events(self):
        """Test that event saga handles correct events."""
        saga = McpServerFailoverEventSaga()

        assert McpServerDegraded in saga.handled_events
        assert McpServerStarted in saga.handled_events
        assert McpServerStopped in saga.handled_events

    def test_configure_failover(self):
        """Test configuring a failover pair."""
        saga = McpServerFailoverEventSaga()

        saga.configure_failover("primary", "backup")

        config = saga.get_failover_config("primary")
        assert config is not None
        assert config.backup_id == "backup"
        assert config.auto_failback is True

    def test_configure_failover_custom_options(self):
        """Test configuring failover with custom options."""
        saga = McpServerFailoverEventSaga()

        saga.configure_failover("primary", "backup", auto_failback=False, failback_delay_s=60.0)

        config = saga.get_failover_config("primary")
        assert config.auto_failback is False
        assert config.failback_delay_s == 60.0

    def test_remove_failover(self):
        """Test removing a failover configuration."""
        saga = McpServerFailoverEventSaga()

        saga.configure_failover("primary", "backup")
        result = saga.remove_failover("primary")

        assert result is True
        assert saga.get_failover_config("primary") is None

    def test_handle_degraded_initiates_failover(self):
        """Test that degradation starts a McpServerFailoverSaga via SagaManager."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        commands = saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Commands are dispatched via SagaManager, not returned directly
        assert len(commands) == 0
        mock_saga_manager.start_saga.assert_called_once()
        start_args = mock_saga_manager.start_saga.call_args
        assert isinstance(start_args[0][0], McpServerFailoverSaga)
        assert start_args[1]["initial_data"]["primary_id"] == "primary"
        assert start_args[1]["initial_data"]["backup_id"] == "backup"

        # Check failover state
        failovers = saga.get_active_failovers()
        assert "primary" in failovers
        assert failovers["primary"].backup_id == "backup"

    def test_handle_degraded_no_failover_configured(self):
        """Test that degradation without config does nothing."""
        saga = McpServerFailoverEventSaga()

        commands = saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        assert len(commands) == 0

    def test_handle_degraded_failover_already_active(self):
        """Test that redundant degradation doesn't trigger new failover."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        # First degradation
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))
        assert mock_saga_manager.start_saga.call_count == 1

        # Second degradation (should not create another failover)
        commands2 = saga.handle(McpServerDegraded("primary", 4, 6, "error"))
        assert len(commands2) == 0
        assert mock_saga_manager.start_saga.call_count == 1  # Not called again

    def test_handle_degraded_backup_is_active(self):
        """Test that backup degradation doesn't cascade."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Backup degradation (should not cascade)
        commands = saga.handle(McpServerDegraded("backup", 1, 1, "error"))

        assert len(commands) == 0

    def test_handle_started_backup(self):
        """Test that backup start completes failover."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Backup started
        saga.handle(McpServerStarted("backup", "subprocess", 5, 100.0))

        failovers = saga.get_active_failovers()
        assert failovers["primary"].backup_started_at is not None

    def test_handle_started_primary_triggers_failback(self):
        """Test that primary recovery schedules failback via SagaManager."""
        mock_saga_manager = MagicMock()
        mock_saga_manager.schedule_command.return_value = "timer-id-xyz"
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup", auto_failback=True, failback_delay_s=30.0)

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))
        saga.handle(McpServerStarted("backup", "subprocess", 5, 100.0))

        # Primary recovers
        commands = saga.handle(McpServerStarted("primary", "subprocess", 5, 100.0))

        # Failback is scheduled via SagaManager, not returned as command
        assert len(commands) == 0
        mock_saga_manager.schedule_command.assert_called_once()
        call_args = mock_saga_manager.schedule_command.call_args
        stop_cmd = call_args[0][0]
        assert isinstance(stop_cmd, StopMcpServerCommand)
        assert stop_cmd.mcp_server_id == "backup"
        assert stop_cmd.reason == "failback"

        # Failover should be cleared
        assert len(saga.get_active_failovers()) == 0

    def test_handle_started_primary_no_failback(self):
        """Test that auto_failback=False prevents failback."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup", auto_failback=False)

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Primary recovers
        commands = saga.handle(McpServerStarted("primary", "subprocess", 5, 100.0))

        # Should not schedule failback
        assert len(commands) == 0
        mock_saga_manager.schedule_command.assert_not_called()

    def test_handle_stopped_backup_clears_failover(self):
        """Test that stopping backup clears failover."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Backup stopped
        saga.handle(McpServerStopped("backup", "shutdown"))

        assert len(saga.get_active_failovers()) == 0
        assert not saga.is_backup_active("backup")

    def test_is_backup_active(self):
        """Test checking if provider is active backup."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        assert not saga.is_backup_active("backup")

        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        assert saga.is_backup_active("backup")

    def test_force_failback(self):
        """Test forcing failback."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup", auto_failback=False)

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Force failback
        commands = saga.force_failback("primary")

        assert any(isinstance(c, StopMcpServerCommand) and c.mcp_server_id == "backup" for c in commands)

    def test_cancel_failover(self):
        """Test canceling failover (keeps backup running)."""
        mock_saga_manager = MagicMock()
        saga = McpServerFailoverEventSaga(saga_manager=mock_saga_manager)
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(McpServerDegraded("primary", 3, 5, "error"))

        # Cancel
        result = saga.cancel_failover("primary")

        assert result is True
        assert len(saga.get_active_failovers()) == 0

    def test_get_all_configs(self):
        """Test getting all failover configurations."""
        saga = McpServerFailoverEventSaga()

        saga.configure_failover("p1", "b1")
        saga.configure_failover("p2", "b2")

        configs = saga.get_all_configs()

        assert len(configs) == 2
        assert "p1" in configs
        assert "p2" in configs
