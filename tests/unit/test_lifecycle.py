"""Tests for server/lifecycle.py module.

Tests cover server lifecycle management: start, run, shutdown.
"""

import signal
import sys
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.cli import CLIConfig
from mcp_hangar.server.lifecycle import _setup_signal_handlers, run_server, ServerLifecycle


class TestServerLifecycle:
    """Tests for ServerLifecycle class."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock ApplicationContext."""
        mock_runtime = MagicMock()
        mock_runtime.repository.get_all_ids.return_value = []

        mock_mcp = MagicMock()
        mock_worker1 = MagicMock()
        mock_worker1.task = "gc"
        mock_worker2 = MagicMock()
        mock_worker2.task = "health_check"

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
            background_workers=[mock_worker1, mock_worker2],
            discovery_orchestrator=None,
            config={},
        )
        return ctx

    def test_lifecycle_init(self, mock_context):
        """ServerLifecycle should initialize correctly."""
        lifecycle = ServerLifecycle(mock_context)

        assert lifecycle._context == mock_context
        assert lifecycle._running is False
        assert lifecycle._shutdown_requested is False

    def test_lifecycle_is_running(self, mock_context):
        """is_running property should reflect state."""
        lifecycle = ServerLifecycle(mock_context)

        assert lifecycle.is_running is False

        lifecycle._running = True
        assert lifecycle.is_running is True

    def test_lifecycle_start(self, mock_context):
        """start() should start background workers."""
        lifecycle = ServerLifecycle(mock_context)
        lifecycle.start()

        assert lifecycle._running is True
        for worker in mock_context.background_workers:
            worker.start.assert_called_once()

    def test_lifecycle_start_idempotent(self, mock_context):
        """start() should be idempotent."""
        lifecycle = ServerLifecycle(mock_context)
        lifecycle.start()
        lifecycle.start()  # Second call should be no-op

        # Each worker's start should only be called once
        for worker in mock_context.background_workers:
            assert worker.start.call_count == 1

    def test_lifecycle_start_with_discovery(self):
        """start() should start discovery orchestrator if present."""
        mock_runtime = MagicMock()
        mock_mcp = MagicMock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_stats.return_value = {"sources_count": 2}

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
            background_workers=[],
            discovery_orchestrator=mock_orchestrator,
            config={},
        )

        lifecycle = ServerLifecycle(ctx)

        with patch("mcp_hangar.server.lifecycle.asyncio") as mock_asyncio:
            lifecycle.start()

        # Discovery orchestrator start should be called via asyncio.run
        mock_asyncio.run.assert_called_once()

    def test_lifecycle_shutdown(self, mock_context):
        """shutdown() should stop all components."""
        lifecycle = ServerLifecycle(mock_context)
        lifecycle._running = True

        # Mock the context's shutdown behavior
        with patch.object(mock_context, "shutdown", MagicMock()) as mock_shutdown:
            lifecycle.shutdown()

            assert lifecycle._shutdown_requested is True
            assert lifecycle._running is False
            mock_shutdown.assert_called_once()

    def test_lifecycle_shutdown_idempotent(self, mock_context):
        """shutdown() should be idempotent."""
        lifecycle = ServerLifecycle(mock_context)

        with patch.object(mock_context, "shutdown", MagicMock()) as mock_shutdown:
            lifecycle.shutdown()
            lifecycle.shutdown()  # Second call should be no-op

            # shutdown on context should only be called once
            assert mock_shutdown.call_count == 1

    def test_run_stdio(self, mock_context):
        """run_stdio() should call mcp_server.run()."""
        lifecycle = ServerLifecycle(mock_context)
        lifecycle.run_stdio()

        mock_context.mcp_server.run.assert_called_once()

    def test_run_stdio_handles_keyboard_interrupt(self, mock_context):
        """run_stdio() should handle KeyboardInterrupt gracefully."""
        mock_context.mcp_server.run.side_effect = KeyboardInterrupt()

        lifecycle = ServerLifecycle(mock_context)
        # Should not raise
        lifecycle.run_stdio()

    def test_run_stdio_exits_on_fatal_error(self, mock_context):
        """run_stdio() should exit on fatal error."""
        mock_context.mcp_server.run.side_effect = RuntimeError("Fatal error")

        lifecycle = ServerLifecycle(mock_context)

        with pytest.raises(SystemExit) as exc_info:
            lifecycle.run_stdio()

        assert exc_info.value.code == 1

    def test_run_http(self, mock_context):
        """run_http() should configure and run uvicorn."""
        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn

        try:
            with patch("asyncio.run") as mock_asyncio_run:
                mock_asyncio_run.return_value = None

                lifecycle = ServerLifecycle(mock_context)
                lifecycle.run_http("127.0.0.1", 9000)

            # Verify MCP server settings were updated
            assert mock_context.mcp_server.settings.host == "127.0.0.1"
            assert mock_context.mcp_server.settings.port == 9000
        finally:
            del sys.modules["uvicorn"]

    def test_run_http_handles_keyboard_interrupt(self, mock_context):
        """run_http() should handle KeyboardInterrupt gracefully."""
        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn

        try:
            with patch("asyncio.run") as mock_asyncio_run:
                mock_asyncio_run.side_effect = KeyboardInterrupt()

                lifecycle = ServerLifecycle(mock_context)
                # Should not raise
                lifecycle.run_http("localhost", 8000)
        finally:
            del sys.modules["uvicorn"]


class TestSetupSignalHandlers:
    """Tests for _setup_signal_handlers function."""

    def test_registers_sigterm_handler(self):
        """Should register SIGTERM handler."""
        mock_context = MagicMock()
        lifecycle = ServerLifecycle(mock_context)

        with patch("mcp_hangar.server.lifecycle.signal.signal") as mock_signal:
            _setup_signal_handlers(lifecycle)

        # Check SIGTERM was registered
        sigterm_call = None
        for call in mock_signal.call_args_list:
            if call[0][0] == signal.SIGTERM:
                sigterm_call = call
                break

        assert sigterm_call is not None

    def test_registers_sigint_handler(self):
        """Should register SIGINT handler."""
        mock_context = MagicMock()
        lifecycle = ServerLifecycle(mock_context)

        with patch("mcp_hangar.server.lifecycle.signal.signal") as mock_signal:
            _setup_signal_handlers(lifecycle)

        # Check SIGINT was registered
        sigint_call = None
        for call in mock_signal.call_args_list:
            if call[0][0] == signal.SIGINT:
                sigint_call = call
                break

        assert sigint_call is not None


class TestRunServer:
    """Tests for run_server function."""

    @pytest.fixture
    def mock_cli_config(self):
        """Create a mock CLIConfig."""
        return CLIConfig(
            http_mode=False,
            http_host="0.0.0.0",
            http_port=8000,
            config_path=None,
            log_file=None,
            log_level="INFO",
            json_logs=False,
        )

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for run_server."""
        with (
            patch("mcp_hangar.server.lifecycle.setup_logging") as mock_setup_log,
            patch("mcp_hangar.server.lifecycle.bootstrap") as mock_bootstrap,
            patch("mcp_hangar.server.lifecycle._setup_signal_handlers") as mock_signals,
            patch("mcp_hangar.server.lifecycle.get_discovery_orchestrator") as mock_get_disc,
            patch("mcp_hangar.server.lifecycle.ServerLifecycle") as MockLifecycle,
        ):
            mock_context = MagicMock()
            mock_context.runtime.repository.get_all_ids.return_value = ["provider1"]
            mock_bootstrap.return_value = mock_context
            mock_get_disc.return_value = None

            mock_lifecycle_instance = MagicMock()
            MockLifecycle.return_value = mock_lifecycle_instance

            yield {
                "setup_log": mock_setup_log,
                "bootstrap": mock_bootstrap,
                "signals": mock_signals,
                "get_disc": mock_get_disc,
                "Lifecycle": MockLifecycle,
                "lifecycle_instance": mock_lifecycle_instance,
                "context": mock_context,
            }

    def test_run_server_stdio_mode(self, mock_cli_config, mock_dependencies):
        """run_server() should run stdio mode by default."""
        run_server(mock_cli_config)

        mock_dependencies["lifecycle_instance"].start.assert_called_once()
        mock_dependencies["lifecycle_instance"].run_stdio.assert_called_once()
        mock_dependencies["lifecycle_instance"].run_http.assert_not_called()

    def test_run_server_http_mode(self, mock_dependencies):
        """run_server() should run HTTP mode when configured."""
        http_config = CLIConfig(
            http_mode=True,
            http_host="localhost",
            http_port=9000,
            config_path=None,
            log_file=None,
            log_level="INFO",
            json_logs=False,
        )

        run_server(http_config)

        mock_dependencies["lifecycle_instance"].start.assert_called_once()
        mock_dependencies["lifecycle_instance"].run_http.assert_called_once_with("localhost", 9000)
        mock_dependencies["lifecycle_instance"].run_stdio.assert_not_called()

    def test_run_server_setup_logging(self, mock_cli_config, mock_dependencies):
        """run_server() should setup logging."""
        run_server(mock_cli_config)

        mock_dependencies["setup_log"].assert_called()

    def test_run_server_calls_bootstrap(self, mock_cli_config, mock_dependencies):
        """run_server() should call bootstrap."""
        run_server(mock_cli_config)

        mock_dependencies["bootstrap"].assert_called_once_with(None)

    def test_run_server_with_config_path(self, mock_dependencies):
        """run_server() should pass config path to bootstrap."""
        config = CLIConfig(
            http_mode=False,
            http_host="0.0.0.0",
            http_port=8000,
            config_path="/path/to/config.yaml",
            log_file=None,
            log_level="INFO",
            json_logs=False,
        )

        run_server(config)

        mock_dependencies["bootstrap"].assert_called_once_with("/path/to/config.yaml")

    def test_run_server_setup_signal_handlers(self, mock_cli_config, mock_dependencies):
        """run_server() should setup signal handlers."""
        run_server(mock_cli_config)

        mock_dependencies["signals"].assert_called_once()

    def test_run_server_ensures_shutdown_on_exit(self, mock_cli_config, mock_dependencies):
        """run_server() should call shutdown in finally block."""
        run_server(mock_cli_config)

        mock_dependencies["lifecycle_instance"].shutdown.assert_called_once()

    def test_run_server_shutdown_on_exception(self, mock_dependencies):
        """run_server() should shutdown even on exception."""
        config = CLIConfig(
            http_mode=False,
            http_host="0.0.0.0",
            http_port=8000,
            config_path=None,
            log_file=None,
            log_level="INFO",
            json_logs=False,
        )

        # Make run_stdio raise an exception that's caught by finally
        mock_dependencies["lifecycle_instance"].run_stdio.side_effect = Exception("Test error")

        with pytest.raises(Exception, match="Test error"):
            run_server(config)

        # Shutdown should still be called
        mock_dependencies["lifecycle_instance"].shutdown.assert_called_once()
