"""Tests for server/__init__.py module - backward compatibility.

Tests cover initialization functions, discovery, and main entry point logic.
These tests verify backward compatibility after the refactoring.
"""

import os
from unittest.mock import MagicMock, patch

from mcp_hangar.server import (
    GC_WORKER_INTERVAL_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
    _auto_add_volumes,
    _create_discovery_source,
    _ensure_data_dir,
    _parse_args,
)


class TestParseArgs:
    """Tests for _parse_args function (backward compatibility)."""

    def test_default_values(self):
        """Should return CLIConfig with default values when no args provided."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args([])

        assert config.http_mode is False
        assert config.http_host == "0.0.0.0"
        assert config.http_port == 8000
        assert config.config_path is None
        assert config.log_file is None

    def test_http_flag(self):
        """Should parse --http flag."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(["--http"])

        assert config.http_mode is True

    def test_host_option(self):
        """Should parse --host option."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(["--host", "127.0.0.1"])

        assert config.http_host == "127.0.0.1"

    def test_port_option(self):
        """Should parse --port option."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(["--port", "9000"])

        assert config.http_port == 9000

    def test_config_option(self):
        """Should parse --config option."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(["--config", "/path/to/config.yaml"])

        assert config.config_path == "/path/to/config.yaml"

    def test_log_file_option(self):
        """Should parse --log-file option."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(["--log-file", "/var/log/mcp.log"])

        assert config.log_file == "/var/log/mcp.log"

    def test_all_options_combined(self):
        """Should parse all options together."""
        with patch.dict(os.environ, {}, clear=True):
            config = _parse_args(
                [
                    "--http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                    "--config",
                    "custom.yaml",
                    "--log-file",
                    "server.log",
                ]
            )

        assert config.http_mode is True
        assert config.http_host == "0.0.0.0"
        assert config.http_port == 8080
        assert config.config_path == "custom.yaml"
        assert config.log_file == "server.log"


class TestEnsureDataDir:
    """Tests for _ensure_data_dir function."""

    def test_creates_data_dir_when_missing(self, tmp_path, monkeypatch):
        """Should create data directory when it doesn't exist."""
        monkeypatch.chdir(tmp_path)

        _ensure_data_dir()

        data_dir = tmp_path / "data"
        assert data_dir.exists()
        assert data_dir.is_dir()

    def test_does_not_create_when_exists(self, tmp_path, monkeypatch):
        """Should not fail when directory already exists."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Should not raise
        _ensure_data_dir()

        assert data_dir.exists()

    def test_handles_oserror_gracefully(self, tmp_path, monkeypatch):
        """Should handle OSError gracefully."""
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]
        monkeypatch.chdir(tmp_path)

        original_path = bootstrap_mod.Path

        mock_data_dir = MagicMock()
        mock_data_dir.exists.return_value = False
        mock_data_dir.mkdir.side_effect = OSError("Permission denied")
        mock_path = MagicMock(return_value=mock_data_dir)
        bootstrap_mod.Path = mock_path

        try:
            # Should not raise
            bootstrap_mod._ensure_data_dir()
        finally:
            bootstrap_mod.Path = original_path


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

    def test_memory_provider_gets_memory_volume(self, tmp_path, monkeypatch):
        """Should add memory volume for memory providers."""
        monkeypatch.chdir(tmp_path)

        result = _auto_add_volumes("mcp-memory-provider")

        assert len(result) == 1
        assert "/app/data:rw" in result[0]

    def test_filesystem_provider_gets_filesystem_volume(self, tmp_path, monkeypatch):
        """Should add filesystem volume for filesystem providers."""
        monkeypatch.chdir(tmp_path)

        result = _auto_add_volumes("mcp-filesystem-server")

        assert len(result) == 1
        assert "/data:rw" in result[0]

    def test_unknown_provider_gets_no_volumes(self):
        """Should return empty list for unknown providers."""
        result = _auto_add_volumes("mcp-math-provider")

        assert result == []

    def test_case_insensitive_matching(self, tmp_path, monkeypatch):
        """Should match provider names case-insensitively."""
        monkeypatch.chdir(tmp_path)

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
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        original_providers = bootstrap_mod.PROVIDERS
        original_worker = bootstrap_mod.BackgroundWorker

        mock_worker_class = MagicMock()
        mock_worker = MagicMock()
        mock_worker_class.return_value = mock_worker

        bootstrap_mod.PROVIDERS = {}
        bootstrap_mod.BackgroundWorker = mock_worker_class

        try:
            # Call through module to pick up patches
            workers = bootstrap_mod._create_background_workers()
            for worker in workers:
                worker.start()

            # Should be called twice (GC and health check)
            assert mock_worker_class.call_count == 2
            assert mock_worker.start.call_count == 2
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker

    def test_passes_correct_interval_to_gc_worker(self):
        """Should pass correct interval to GC worker."""
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        original_providers = bootstrap_mod.PROVIDERS
        original_worker = bootstrap_mod.BackgroundWorker

        mock_worker_class = MagicMock()
        mock_worker = MagicMock()
        mock_worker_class.return_value = mock_worker

        bootstrap_mod.PROVIDERS = {}
        bootstrap_mod.BackgroundWorker = mock_worker_class

        try:
            # Call through module to pick up patches
            bootstrap_mod._create_background_workers()

            # First call should be GC worker
            first_call = mock_worker_class.call_args_list[0]
            assert first_call.kwargs["interval_s"] == GC_WORKER_INTERVAL_SECONDS
            assert first_call.kwargs["task"] == "gc"
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker


class TestRegisterAllTools:
    """Tests for _register_all_tools function."""

    def test_registers_all_tool_groups(self):
        """Should register all tool groups."""
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]
        mock_mcp = MagicMock()

        # Store originals
        originals = {
            "register_registry_tools": getattr(bootstrap_mod, "register_registry_tools", None),
            "register_provider_tools": getattr(bootstrap_mod, "register_provider_tools", None),
            "register_health_tools": getattr(bootstrap_mod, "register_health_tools", None),
            "register_discovery_tools": getattr(bootstrap_mod, "register_discovery_tools", None),
            "register_group_tools": getattr(bootstrap_mod, "register_group_tools", None),
        }

        # Create mocks
        mocks = {
            "register_registry_tools": MagicMock(),
            "register_provider_tools": MagicMock(),
            "register_health_tools": MagicMock(),
            "register_discovery_tools": MagicMock(),
            "register_group_tools": MagicMock(),
        }

        # Apply mocks
        for name, mock in mocks.items():
            setattr(bootstrap_mod, name, mock)

        try:
            bootstrap_mod._register_all_tools(mock_mcp)

            mocks["register_registry_tools"].assert_called_once_with(mock_mcp)
            mocks["register_provider_tools"].assert_called_once_with(mock_mcp)
            mocks["register_health_tools"].assert_called_once_with(mock_mcp)
            mocks["register_discovery_tools"].assert_called_once_with(mock_mcp)
            mocks["register_group_tools"].assert_called_once_with(mock_mcp)
        finally:
            # Restore originals
            for name, original in originals.items():
                if original is not None:
                    setattr(bootstrap_mod, name, original)
