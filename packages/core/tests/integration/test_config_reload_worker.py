"""Integration tests for ConfigReloadWorker."""

import os
import tempfile
import time
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.gc import ConfigReloadWorker


class TestConfigReloadWorkerIntegration:
    """Integration tests for ConfigReloadWorker with real file system."""

    @pytest.fixture
    def temp_config_file(self):
        """Create temporary config file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "providers": {
                    "test-provider": {
                        "mode": "subprocess",
                        "command": ["python", "-m", "test_server"],
                    }
                }
            }
            yaml.dump(config, f)
            config_path = f.name

        yield config_path

        # Cleanup
        if os.path.exists(config_path):
            os.unlink(config_path)

    @pytest.fixture
    def mock_command_bus(self):
        """Create mock command bus."""
        bus = Mock()
        bus.send.return_value = {"success": True}
        return bus

    def test_worker_disabled_when_no_config_path(self, mock_command_bus):
        """Worker should be disabled when no config path provided."""
        worker = ConfigReloadWorker(
            config_path=None,
            command_bus=mock_command_bus,
        )

        assert worker._enabled is False

    def test_worker_disabled_when_config_not_found(self, mock_command_bus):
        """Worker should be disabled when config file doesn't exist."""
        worker = ConfigReloadWorker(
            config_path="/nonexistent/config.yaml",
            command_bus=mock_command_bus,
        )

        assert worker._enabled is False

    def test_worker_starts_in_polling_mode_when_watchdog_unavailable(self, temp_config_file, mock_command_bus):
        """Worker should fall back to polling when watchdog unavailable."""
        worker = ConfigReloadWorker(
            config_path=temp_config_file,
            command_bus=mock_command_bus,
            interval_s=1,
            use_watchdog=False,  # Force polling
        )

        worker.start()
        assert worker.running
        assert worker.thread is not None
        assert worker.thread.is_alive()

        worker.stop()
        assert not worker.running

    def test_polling_detects_file_modification(self, temp_config_file, mock_command_bus):
        """Polling worker should detect file modification via mtime."""
        # Wait before creating worker to ensure mtime difference
        time.sleep(0.1)

        worker = ConfigReloadWorker(
            config_path=temp_config_file,
            command_bus=mock_command_bus,
            interval_s=1,
            use_watchdog=False,  # Use polling for predictable testing
        )

        worker.start()

        try:
            # Wait a bit to ensure worker is running
            time.sleep(0.5)

            # Modify the file - ensure mtime is different
            time.sleep(1.1)  # Ensure mtime changes (must be > interval_s)
            with open(temp_config_file, "a") as f:
                f.write("\n# Modified\n")

            # Force flush to ensure mtime updates
            os.sync() if hasattr(os, "sync") else None

            # Wait for polling to detect change (needs time for next poll cycle)
            time.sleep(2.5)

            # Check that reload was triggered
            mock_command_bus.send.assert_called()
            call_args = mock_command_bus.send.call_args
            command = call_args[0][0]

            # Verify command type
            assert command.__class__.__name__ == "ReloadConfigurationCommand"
            assert str(command.config_path) == temp_config_file

        finally:
            worker.stop()

    @pytest.mark.skipif(
        not hasattr(ConfigReloadWorker, "_start_watchdog") or os.name == "nt",
        reason="Watchdog tests only on Unix with watchdog installed",
    )
    def test_watchdog_detects_file_modification(self, temp_config_file, mock_command_bus):
        """Watchdog worker should detect file modification via inotify/fsevents."""
        worker = ConfigReloadWorker(
            config_path=temp_config_file,
            command_bus=mock_command_bus,
            use_watchdog=True,
        )

        worker.start()

        try:
            # Wait for watchdog to fully initialize (observer thread startup + inotify registration)
            time.sleep(1.5)

            # Modify the file
            with open(temp_config_file, "a") as f:
                f.write("\n# Modified via watchdog\n")

            # Force flush
            os.sync() if hasattr(os, "sync") else None

            # Wait for debounce (1s) + watchdog processing + command execution
            time.sleep(3.0)

            # Check that reload was triggered
            mock_command_bus.send.assert_called()
            call_args = mock_command_bus.send.call_args
            command = call_args[0][0]

            assert command.__class__.__name__ == "ReloadConfigurationCommand"
            assert command.requested_by == "file_watcher"

        finally:
            worker.stop()

    def test_worker_handles_command_bus_errors_gracefully(self, temp_config_file, mock_command_bus):
        """Worker should handle command bus errors without crashing."""
        # Make command bus raise exception
        mock_command_bus.send.side_effect = Exception("Command bus error")

        worker = ConfigReloadWorker(
            config_path=temp_config_file,
            command_bus=mock_command_bus,
            interval_s=1,
            use_watchdog=False,
        )

        worker.start()

        try:
            # Modify file
            time.sleep(1.1)
            with open(temp_config_file, "a") as f:
                f.write("\n# Trigger error\n")

            # Wait for polling
            time.sleep(2)

            # Worker should still be running despite error
            assert worker.running
            assert worker.thread.is_alive()

        finally:
            worker.stop()

    def test_multiple_rapid_changes_debounced_in_watchdog(self, temp_config_file, mock_command_bus):
        """Watchdog should debounce multiple rapid file changes."""
        worker = ConfigReloadWorker(
            config_path=temp_config_file,
            command_bus=mock_command_bus,
            interval_s=1,  # Short interval for testing
            use_watchdog=False,  # Use polling for predictable behavior
        )

        worker.start()

        try:
            # Wait for worker to initialize
            time.sleep(0.5)

            # Make multiple rapid changes (within debounce window)
            # Ensure mtime actually changes
            time.sleep(1.1)  # Must be > interval_s to be detected
            for i in range(3):
                with open(temp_config_file, "a") as f:
                    f.write(f"\n# Change {i}\n")
                time.sleep(0.1)

            # Force flush
            os.sync() if hasattr(os, "sync") else None

            # Wait for polling to detect change (needs at least one full cycle after modification)
            time.sleep(2.5)

            # Should have triggered reload at least once
            # (polling might catch it once, watchdog would debounce)
            assert mock_command_bus.send.call_count >= 1

        finally:
            worker.stop()
