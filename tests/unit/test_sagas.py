"""Tests for Provider Recovery and Failover Sagas."""

from mcp_hangar.application.commands import StartProviderCommand, StopProviderCommand
from mcp_hangar.application.sagas.provider_failover_saga import ProviderFailoverSaga
from mcp_hangar.application.sagas.provider_recovery_saga import ProviderRecoverySaga
from mcp_hangar.domain.events import HealthCheckFailed, ProviderDegraded, ProviderStarted, ProviderStopped


class TestProviderRecoverySaga:
    """Test ProviderRecoverySaga."""

    def test_saga_type(self):
        """Test saga type identifier."""
        saga = ProviderRecoverySaga()

        assert saga.saga_type == "provider_recovery"

    def test_handled_events(self):
        """Test that saga handles correct events."""
        saga = ProviderRecoverySaga()

        assert ProviderDegraded in saga.handled_events
        assert ProviderStarted in saga.handled_events
        assert ProviderStopped in saga.handled_events
        assert HealthCheckFailed in saga.handled_events

    def test_handle_degraded_first_time(self):
        """Test handling first degradation."""
        saga = ProviderRecoverySaga(max_retries=3)

        event = ProviderDegraded("p1", 3, 5, "timeout")
        commands = saga.handle(event)

        assert len(commands) == 1
        assert isinstance(commands[0], StartProviderCommand)
        assert commands[0].provider_id == "p1"

        # Check retry state
        state = saga.get_retry_state("p1")
        assert state["retries"] == 1

    def test_handle_degraded_multiple_times(self):
        """Test handling multiple degradations."""
        saga = ProviderRecoverySaga(max_retries=3)

        # First degradation
        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        assert saga.get_retry_state("p1")["retries"] == 1

        # Second degradation
        saga.handle(ProviderDegraded("p1", 2, 2, "error"))
        assert saga.get_retry_state("p1")["retries"] == 2

        # Third degradation
        saga.handle(ProviderDegraded("p1", 3, 3, "error"))
        assert saga.get_retry_state("p1")["retries"] == 3

    def test_handle_degraded_max_retries_exceeded(self):
        """Test that max retries triggers stop command."""
        saga = ProviderRecoverySaga(max_retries=2)

        # First two retries
        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        saga.handle(ProviderDegraded("p1", 2, 2, "error"))

        # Third attempt - should exceed max
        commands = saga.handle(ProviderDegraded("p1", 3, 3, "error"))

        assert len(commands) == 1
        assert isinstance(commands[0], StopProviderCommand)
        assert commands[0].reason == "max_retries_exceeded"

    def test_handle_started_resets_retry_count(self):
        """Test that successful start resets retry count."""
        saga = ProviderRecoverySaga(max_retries=3)

        # Build up retries
        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        saga.handle(ProviderDegraded("p1", 2, 2, "error"))
        assert saga.get_retry_state("p1")["retries"] == 2

        # Successful start
        saga.handle(ProviderStarted("p1", "subprocess", 5, 100.0))

        assert saga.get_retry_state("p1")["retries"] == 0

    def test_handle_stopped_clears_state_for_normal_stop(self):
        """Test that normal stops clear retry state."""
        saga = ProviderRecoverySaga()

        # Build up state
        saga.handle(ProviderDegraded("p1", 1, 1, "error"))

        # Normal shutdown
        saga.handle(ProviderStopped("p1", "shutdown"))

        assert saga.get_retry_state("p1") is None

    def test_handle_stopped_keeps_state_for_error_stop(self):
        """Test that error stops keep retry state."""
        saga = ProviderRecoverySaga()

        saga.handle(ProviderDegraded("p1", 1, 1, "error"))

        # Error stop
        saga.handle(ProviderStopped("p1", "error"))

        # State should still exist
        assert saga.get_retry_state("p1") is not None

    def test_backoff_calculation(self):
        """Test exponential backoff calculation."""
        saga = ProviderRecoverySaga(initial_backoff_s=5.0, max_backoff_s=60.0, backoff_multiplier=2.0)

        assert saga._calculate_backoff(1) == 5.0
        assert saga._calculate_backoff(2) == 10.0
        assert saga._calculate_backoff(3) == 20.0
        assert saga._calculate_backoff(4) == 40.0
        assert saga._calculate_backoff(5) == 60.0  # Capped at max
        assert saga._calculate_backoff(6) == 60.0  # Still capped

    def test_get_all_retry_states(self):
        """Test getting all retry states."""
        saga = ProviderRecoverySaga()

        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        saga.handle(ProviderDegraded("p2", 2, 2, "error"))

        states = saga.get_all_retry_states()

        assert "p1" in states
        assert "p2" in states

    def test_reset_retry_state(self):
        """Test manually resetting retry state."""
        saga = ProviderRecoverySaga()

        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        saga.reset_retry_state("p1")

        assert saga.get_retry_state("p1") is None

    def test_reset_all_retry_states(self):
        """Test resetting all retry states."""
        saga = ProviderRecoverySaga()

        saga.handle(ProviderDegraded("p1", 1, 1, "error"))
        saga.handle(ProviderDegraded("p2", 1, 1, "error"))

        saga.reset_all_retry_states()

        assert len(saga.get_all_retry_states()) == 0


class TestProviderFailoverSaga:
    """Test ProviderFailoverSaga."""

    def test_saga_type(self):
        """Test saga type identifier."""
        saga = ProviderFailoverSaga()

        assert saga.saga_type == "provider_failover"

    def test_handled_events(self):
        """Test that saga handles correct events."""
        saga = ProviderFailoverSaga()

        assert ProviderDegraded in saga.handled_events
        assert ProviderStarted in saga.handled_events
        assert ProviderStopped in saga.handled_events

    def test_configure_failover(self):
        """Test configuring a failover pair."""
        saga = ProviderFailoverSaga()

        saga.configure_failover("primary", "backup")

        config = saga.get_failover_config("primary")
        assert config is not None
        assert config.backup_id == "backup"
        assert config.auto_failback is True

    def test_configure_failover_custom_options(self):
        """Test configuring failover with custom options."""
        saga = ProviderFailoverSaga()

        saga.configure_failover("primary", "backup", auto_failback=False, failback_delay_s=60.0)

        config = saga.get_failover_config("primary")
        assert config.auto_failback is False
        assert config.failback_delay_s == 60.0

    def test_remove_failover(self):
        """Test removing a failover configuration."""
        saga = ProviderFailoverSaga()

        saga.configure_failover("primary", "backup")
        result = saga.remove_failover("primary")

        assert result is True
        assert saga.get_failover_config("primary") is None

    def test_handle_degraded_initiates_failover(self):
        """Test that degradation initiates failover."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        commands = saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        assert len(commands) == 1
        assert isinstance(commands[0], StartProviderCommand)
        assert commands[0].provider_id == "backup"

        # Check failover state
        failovers = saga.get_active_failovers()
        assert "primary" in failovers
        assert failovers["primary"].backup_id == "backup"

    def test_handle_degraded_no_failover_configured(self):
        """Test that degradation without config does nothing."""
        saga = ProviderFailoverSaga()

        commands = saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        assert len(commands) == 0

    def test_handle_degraded_failover_already_active(self):
        """Test that redundant degradation doesn't trigger new failover."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        # First degradation
        commands1 = saga.handle(ProviderDegraded("primary", 3, 5, "error"))
        assert len(commands1) == 1

        # Second degradation (should not create another failover)
        commands2 = saga.handle(ProviderDegraded("primary", 4, 6, "error"))
        assert len(commands2) == 0

    def test_handle_degraded_backup_is_active(self):
        """Test that backup degradation doesn't cascade."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Backup degradation (should not cascade)
        commands = saga.handle(ProviderDegraded("backup", 1, 1, "error"))

        assert len(commands) == 0

    def test_handle_started_backup(self):
        """Test that backup start completes failover."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Backup started
        saga.handle(ProviderStarted("backup", "subprocess", 5, 100.0))

        failovers = saga.get_active_failovers()
        assert failovers["primary"].backup_started_at is not None

    def test_handle_started_primary_triggers_failback(self):
        """Test that primary recovery triggers failback."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup", auto_failback=True)

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))
        saga.handle(ProviderStarted("backup", "subprocess", 5, 100.0))

        # Primary recovers
        commands = saga.handle(ProviderStarted("primary", "subprocess", 5, 100.0))

        # Should stop backup
        assert any(isinstance(c, StopProviderCommand) and c.provider_id == "backup" for c in commands)

        # Failover should be cleared
        assert len(saga.get_active_failovers()) == 0

    def test_handle_started_primary_no_failback(self):
        """Test that auto_failback=False prevents failback."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup", auto_failback=False)

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Primary recovers
        commands = saga.handle(ProviderStarted("primary", "subprocess", 5, 100.0))

        # Should not stop backup
        assert len(commands) == 0

    def test_handle_stopped_backup_clears_failover(self):
        """Test that stopping backup clears failover."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Backup stopped
        saga.handle(ProviderStopped("backup", "shutdown"))

        assert len(saga.get_active_failovers()) == 0
        assert not saga.is_backup_active("backup")

    def test_is_backup_active(self):
        """Test checking if provider is active backup."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        assert not saga.is_backup_active("backup")

        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        assert saga.is_backup_active("backup")

    def test_force_failback(self):
        """Test forcing failback."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup", auto_failback=False)

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Force failback
        commands = saga.force_failback("primary")

        assert any(isinstance(c, StopProviderCommand) and c.provider_id == "backup" for c in commands)

    def test_cancel_failover(self):
        """Test canceling failover (keeps backup running)."""
        saga = ProviderFailoverSaga()
        saga.configure_failover("primary", "backup")

        # Initiate failover
        saga.handle(ProviderDegraded("primary", 3, 5, "error"))

        # Cancel
        result = saga.cancel_failover("primary")

        assert result is True
        assert len(saga.get_active_failovers()) == 0

    def test_get_all_configs(self):
        """Test getting all failover configurations."""
        saga = ProviderFailoverSaga()

        saga.configure_failover("p1", "b1")
        saga.configure_failover("p2", "b2")

        configs = saga.get_all_configs()

        assert len(configs) == 2
        assert "p1" in configs
        assert "p2" in configs
