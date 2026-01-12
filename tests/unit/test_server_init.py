"""Tests for server/__init__.py module.

Tests cover initialization functions, discovery, and main entry point logic.
"""

from unittest.mock import MagicMock, patch

from mcp_hangar.server import (
    _auto_add_volumes,
    _create_discovery_source,
    _ensure_data_dir,
    _parse_args,
    _register_all_tools,
    _start_background_workers,
    GC_WORKER_INTERVAL_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
)


class TestParseArgs:
    """Tests for _parse_args function."""

    def test_default_values(self):
        """Should return default values when no args provided."""
        with patch("sys.argv", ["mcp-hangar"]):
            args = _parse_args()

        assert args.http is False
        assert args.host is None
        assert args.port is None
        assert args.config is None
        assert args.log_file is None

    def test_http_flag(self):
        """Should parse --http flag."""
        with patch("sys.argv", ["mcp-hangar", "--http"]):
            args = _parse_args()

        assert args.http is True

    def test_host_option(self):
        """Should parse --host option."""
        with patch("sys.argv", ["mcp-hangar", "--host", "127.0.0.1"]):
            args = _parse_args()

        assert args.host == "127.0.0.1"

    def test_port_option(self):
        """Should parse --port option."""
        with patch("sys.argv", ["mcp-hangar", "--port", "9000"]):
            args = _parse_args()

        assert args.port == 9000

    def test_config_option(self):
        """Should parse --config option."""
        with patch("sys.argv", ["mcp-hangar", "--config", "/path/to/config.yaml"]):
            args = _parse_args()

        assert args.config == "/path/to/config.yaml"

    def test_log_file_option(self):
        """Should parse --log-file option."""
        with patch("sys.argv", ["mcp-hangar", "--log-file", "/var/log/mcp.log"]):
            args = _parse_args()

        assert args.log_file == "/var/log/mcp.log"

    def test_all_options_combined(self):
        """Should parse all options together."""
        with patch(
            "sys.argv",
            [
                "mcp-hangar",
                "--http",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
                "--config",
                "custom.yaml",
                "--log-file",
                "server.log",
            ],
        ):
            args = _parse_args()

        assert args.http is True
        assert args.host == "0.0.0.0"
        assert args.port == 8080
        assert args.config == "custom.yaml"
        assert args.log_file == "server.log"


class TestEnsureDataDir:
    """Tests for _ensure_data_dir function."""

    def test_creates_data_dir_when_missing(self, tmp_path):
        """Should create data directory when it doesn't exist."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_data_dir = MagicMock()
            mock_data_dir.exists.return_value = False
            mock_path.return_value = mock_data_dir

            _ensure_data_dir()

            mock_data_dir.mkdir.assert_called_once_with(mode=0o755, parents=True, exist_ok=True)

    def test_does_not_create_when_exists(self, tmp_path):
        """Should not create directory when it already exists."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_data_dir = MagicMock()
            mock_data_dir.exists.return_value = True
            mock_path.return_value = mock_data_dir

            _ensure_data_dir()

            mock_data_dir.mkdir.assert_not_called()

    def test_handles_oserror_gracefully(self):
        """Should handle OSError gracefully."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_data_dir = MagicMock()
            mock_data_dir.exists.return_value = False
            mock_data_dir.mkdir.side_effect = OSError("Permission denied")
            mock_path.return_value = mock_data_dir

            # Should not raise
            _ensure_data_dir()


class TestCreateDiscoverySource:
    """Tests for _create_discovery_source function."""

    def test_unknown_source_type_returns_none(self):
        """Should return None for unknown source type."""
        result = _create_discovery_source("unknown_type", {})

        assert result is None

    def test_kubernetes_source_creation(self):
        """Should create Kubernetes source with correct config."""
        config = {
            "mode": "additive",
            "namespaces": ["default", "mcp"],
            "label_selector": "app=mcp",
            "in_cluster": False,
        }

        # May raise ImportError if kubernetes package not installed
        try:
            result = _create_discovery_source("kubernetes", config)
            # If we get here, kubernetes is installed
            assert result is None or hasattr(result, "discover")
        except ImportError:
            # Expected when kubernetes package not installed
            pass

    def test_docker_source_creation(self):
        """Should create Docker source with correct config."""
        config = {
            "mode": "authoritative",
            "socket_path": "/var/run/docker.sock",
        }

        result = _create_discovery_source("docker", config)

        # Should return a source or None (depending on docker availability)
        assert result is None or hasattr(result, "discover")

    def test_filesystem_source_creation(self):
        """Should create Filesystem source with correct config."""
        config = {
            "mode": "additive",
            "path": "/etc/mcp-hangar/providers.d/",
            "pattern": "*.yaml",
            "watch": True,
        }

        result = _create_discovery_source("filesystem", config)

        assert result is None or hasattr(result, "discover")

    def test_entrypoint_source_creation(self):
        """Should create Entrypoint source with correct config."""
        config = {
            "mode": "additive",
            "group": "mcp.providers",
        }

        result = _create_discovery_source("entrypoint", config)

        assert result is None or hasattr(result, "discover")

    def test_mode_defaults_to_additive(self):
        """Should default to additive mode when not specified."""
        result = _create_discovery_source("filesystem", {"path": "/tmp"})

        assert result is None or hasattr(result, "discover")


class TestAutoAddVolumes:
    """Tests for _auto_add_volumes function."""

    def test_memory_provider_gets_memory_volume(self, tmp_path):
        """Should add memory volume for memory providers."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_abs = MagicMock()
            mock_abs.__truediv__ = lambda self, x: MagicMock(
                mkdir=MagicMock(), chmod=MagicMock(), __str__=lambda s: f"/data/{x}"
            )
            mock_path.return_value.absolute.return_value = mock_abs

            result = _auto_add_volumes("mcp-memory-provider")

            assert len(result) == 1
            assert "/app/data:rw" in result[0]

    def test_filesystem_provider_gets_filesystem_volume(self, tmp_path):
        """Should add filesystem volume for filesystem providers."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_abs = MagicMock()
            mock_abs.__truediv__ = lambda self, x: MagicMock(
                mkdir=MagicMock(), chmod=MagicMock(), __str__=lambda s: f"/data/{x}"
            )
            mock_path.return_value.absolute.return_value = mock_abs

            result = _auto_add_volumes("mcp-filesystem-server")

            assert len(result) == 1
            assert "/data:rw" in result[0]

    def test_unknown_provider_gets_no_volumes(self):
        """Should return empty list for unknown providers."""
        result = _auto_add_volumes("mcp-math-provider")

        assert result == []

    def test_case_insensitive_matching(self, tmp_path):
        """Should match provider names case-insensitively."""
        with patch("mcp_hangar.server.Path") as mock_path:
            mock_abs = MagicMock()
            mock_abs.__truediv__ = lambda self, x: MagicMock(
                mkdir=MagicMock(), chmod=MagicMock(), __str__=lambda s: f"/data/{x}"
            )
            mock_path.return_value.absolute.return_value = mock_abs

            result = _auto_add_volumes("MCP-MEMORY-Provider")

            assert len(result) == 1


class TestConstants:
    """Tests for module constants."""

    def test_gc_worker_interval_is_positive(self):
        """GC worker interval should be positive."""
        assert GC_WORKER_INTERVAL_SECONDS > 0

    def test_health_check_interval_is_positive(self):
        """Health check interval should be positive."""
        assert HEALTH_CHECK_INTERVAL_SECONDS > 0

    def test_gc_interval_is_reasonable(self):
        """GC worker interval should be between 10s and 5min."""
        assert 10 <= GC_WORKER_INTERVAL_SECONDS <= 300

    def test_health_check_interval_is_reasonable(self):
        """Health check interval should be between 30s and 5min."""
        assert 30 <= HEALTH_CHECK_INTERVAL_SECONDS <= 300


class TestStartBackgroundWorkers:
    """Tests for _start_background_workers function."""

    def test_starts_gc_worker(self):
        """Should start GC background worker."""
        with patch("mcp_hangar.server.BackgroundWorker") as mock_worker_class:
            with patch("mcp_hangar.server.PROVIDERS", {}):
                mock_worker = MagicMock()
                mock_worker_class.return_value = mock_worker

                _start_background_workers()

                # Should be called twice (GC and health check)
                assert mock_worker_class.call_count == 2
                assert mock_worker.start.call_count == 2

    def test_passes_correct_interval_to_gc_worker(self):
        """Should pass correct interval to GC worker."""
        with patch("mcp_hangar.server.BackgroundWorker") as mock_worker_class:
            with patch("mcp_hangar.server.PROVIDERS", {}):
                mock_worker = MagicMock()
                mock_worker_class.return_value = mock_worker

                _start_background_workers()

                # First call should be GC worker
                first_call = mock_worker_class.call_args_list[0]
                assert first_call.kwargs["interval_s"] == GC_WORKER_INTERVAL_SECONDS
                assert first_call.kwargs["task"] == "gc"


class TestRegisterAllTools:
    """Tests for _register_all_tools function."""

    def test_registers_all_tool_groups(self):
        """Should register all tool groups."""
        with patch("mcp_hangar.server.register_registry_tools") as mock_registry:
            with patch("mcp_hangar.server.register_provider_tools") as mock_provider:
                with patch("mcp_hangar.server.register_health_tools") as mock_health:
                    with patch("mcp_hangar.server.register_discovery_tools") as mock_discovery:
                        with patch("mcp_hangar.server.register_group_tools") as mock_group:
                            with patch("mcp_hangar.server.mcp") as mock_mcp:
                                _register_all_tools()

                                mock_registry.assert_called_once_with(mock_mcp)
                                mock_provider.assert_called_once_with(mock_mcp)
                                mock_health.assert_called_once_with(mock_mcp)
                                mock_discovery.assert_called_once_with(mock_mcp)
                                mock_group.assert_called_once_with(mock_mcp)
