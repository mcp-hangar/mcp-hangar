"""Tests for server/bootstrap.py module.

Tests cover application bootstrapping and dependency injection.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.server.bootstrap import (
    _auto_add_volumes,
    _create_discovery_source,
    _ensure_data_dir,
    ApplicationContext,
    bootstrap,
    GC_WORKER_INTERVAL_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
)


class TestConstants:
    """Tests for module constants."""

    def test_gc_worker_interval(self):
        """GC worker interval should be reasonable."""
        assert GC_WORKER_INTERVAL_SECONDS > 0
        assert GC_WORKER_INTERVAL_SECONDS == 30

    def test_health_check_interval(self):
        """Health check interval should be reasonable."""
        assert HEALTH_CHECK_INTERVAL_SECONDS > 0
        assert HEALTH_CHECK_INTERVAL_SECONDS == 60


class TestApplicationContext:
    """Tests for ApplicationContext dataclass."""

    def test_application_context_creation(self):
        """ApplicationContext should be creatable with minimal args."""
        mock_runtime = MagicMock()
        mock_mcp = MagicMock()

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
        )

        assert ctx.runtime == mock_runtime
        assert ctx.mcp_server == mock_mcp
        assert ctx.background_workers == []
        assert ctx.discovery_orchestrator is None
        assert ctx.config == {}

    def test_application_context_with_workers(self):
        """ApplicationContext should accept background workers."""
        mock_runtime = MagicMock()
        mock_mcp = MagicMock()
        mock_worker = MagicMock()

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
            background_workers=[mock_worker],
        )

        assert len(ctx.background_workers) == 1
        assert ctx.background_workers[0] == mock_worker

    def test_application_context_shutdown(self):
        """ApplicationContext.shutdown() should stop all components."""
        mock_runtime = MagicMock()
        mock_mcp = MagicMock()
        mock_worker = MagicMock()
        mock_orchestrator = MagicMock()

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
            background_workers=[mock_worker],
            discovery_orchestrator=mock_orchestrator,
        )

        # Use sys.modules to get the actual module
        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        mock_providers = MagicMock()
        mock_providers.items.return_value = []

        original_providers = bootstrap_mod.PROVIDERS
        bootstrap_mod.PROVIDERS = mock_providers
        try:
            ctx.shutdown()
        finally:
            bootstrap_mod.PROVIDERS = original_providers

        mock_worker.stop.assert_called_once()

    def test_application_context_shutdown_handles_worker_errors(self):
        """ApplicationContext.shutdown() should handle worker errors gracefully."""
        mock_runtime = MagicMock()
        mock_mcp = MagicMock()
        mock_worker = MagicMock()
        mock_worker.stop.side_effect = Exception("Worker error")
        mock_worker.task = "gc"

        ctx = ApplicationContext(
            runtime=mock_runtime,
            mcp_server=mock_mcp,
            background_workers=[mock_worker],
        )

        # Use sys.modules to get the actual module
        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        mock_providers = MagicMock()
        mock_providers.items.return_value = []

        original_providers = bootstrap_mod.PROVIDERS
        bootstrap_mod.PROVIDERS = mock_providers
        try:
            ctx.shutdown()
        finally:
            bootstrap_mod.PROVIDERS = original_providers


class TestEnsureDataDir:
    """Tests for _ensure_data_dir function."""

    def test_creates_data_dir_when_missing(self, tmp_path, monkeypatch):
        """Should create data directory when it doesn't exist."""
        monkeypatch.chdir(tmp_path)

        _ensure_data_dir()

        data_dir = tmp_path / "data"
        assert data_dir.exists()
        assert data_dir.is_dir()

    def test_does_nothing_when_dir_exists(self, tmp_path, monkeypatch):
        """Should not fail when data directory already exists."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Should not raise
        _ensure_data_dir()

        assert data_dir.exists()


class TestCreateBackgroundWorkers:
    """Tests for _create_background_workers function."""

    def test_creates_two_workers(self):
        """Should create GC and health check workers."""
        # Use sys.modules to get the actual module (not the re-exported function)
        import mcp_hangar.server.bootstrap  # noqa: F401 - ensure module is loaded

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        original_providers = bootstrap_mod.PROVIDERS
        original_worker = bootstrap_mod.BackgroundWorker

        mock_worker_class = MagicMock()
        bootstrap_mod.PROVIDERS = {}
        bootstrap_mod.BackgroundWorker = mock_worker_class

        try:
            # Call through the module to pick up patches
            workers = bootstrap_mod._create_background_workers()
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker

        assert mock_worker_class.call_count == 2
        assert len(workers) == 2

    def test_workers_not_started(self):
        """Workers should be created but not started."""
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
            # Call through the module to pick up patches
            _workers = bootstrap_mod._create_background_workers()  # noqa: F841
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker

        # Workers should not have start() called
        mock_worker.start.assert_not_called()

    def test_gc_worker_interval(self):
        """GC worker should use correct interval."""
        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        original_providers = bootstrap_mod.PROVIDERS
        original_worker = bootstrap_mod.BackgroundWorker

        mock_worker_class = MagicMock()
        bootstrap_mod.PROVIDERS = {}
        bootstrap_mod.BackgroundWorker = mock_worker_class

        try:
            # Call through the module to pick up patches
            bootstrap_mod._create_background_workers()
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker

        # Find the GC worker call
        gc_call = None
        for call in mock_worker_class.call_args_list:
            if call.kwargs.get("task") == "gc":
                gc_call = call
                break

        assert gc_call is not None
        assert gc_call.kwargs["interval_s"] == GC_WORKER_INTERVAL_SECONDS

    def test_health_worker_interval(self):
        """Health worker should use correct interval."""
        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        original_providers = bootstrap_mod.PROVIDERS
        original_worker = bootstrap_mod.BackgroundWorker

        mock_worker_class = MagicMock()
        bootstrap_mod.PROVIDERS = {}
        bootstrap_mod.BackgroundWorker = mock_worker_class

        try:
            # Call through the module to pick up patches
            bootstrap_mod._create_background_workers()
        finally:
            bootstrap_mod.PROVIDERS = original_providers
            bootstrap_mod.BackgroundWorker = original_worker

        # Find the health worker call
        health_call = None
        for call in mock_worker_class.call_args_list:
            if call.kwargs.get("task") == "health_check":
                health_call = call
                break

        assert health_call is not None
        assert health_call.kwargs["interval_s"] == HEALTH_CHECK_INTERVAL_SECONDS


class TestAutoAddVolumes:
    """Tests for _auto_add_volumes function."""

    def test_memory_provider_gets_volume(self, tmp_path, monkeypatch):
        """Memory providers should get auto-added volume."""
        monkeypatch.chdir(tmp_path)

        volumes = _auto_add_volumes("memory-provider")

        assert len(volumes) == 1
        assert "memory" in volumes[0]
        assert "/app/data:rw" in volumes[0]

    def test_filesystem_provider_gets_volume(self, tmp_path, monkeypatch):
        """Filesystem providers should get auto-added volume."""
        monkeypatch.chdir(tmp_path)

        volumes = _auto_add_volumes("filesystem-provider")

        assert len(volumes) == 1
        assert "filesystem" in volumes[0]
        assert "/data:rw" in volumes[0]

    def test_other_provider_no_volume(self, tmp_path, monkeypatch):
        """Other providers should not get auto-added volumes."""
        monkeypatch.chdir(tmp_path)

        volumes = _auto_add_volumes("math-provider")

        assert len(volumes) == 0

    def test_case_insensitive_matching(self, tmp_path, monkeypatch):
        """Volume matching should be case-insensitive."""
        monkeypatch.chdir(tmp_path)

        volumes = _auto_add_volumes("MEMORY-PROVIDER")

        assert len(volumes) == 1


class TestCreateDiscoverySource:
    """Tests for _create_discovery_source function."""

    def test_docker_source(self):
        """Should create Docker discovery source."""
        with patch("mcp_hangar.infrastructure.discovery.DockerDiscoverySource") as MockSource:
            source = _create_discovery_source("docker", {"mode": "additive"})

        MockSource.assert_called_once()
        assert source == MockSource.return_value

    def test_filesystem_source(self, tmp_path):
        """Should create filesystem discovery source."""
        with patch("mcp_hangar.infrastructure.discovery.FilesystemDiscoverySource") as MockSource:
            config = {
                "mode": "additive",
                "path": str(tmp_path),
                "pattern": "*.yaml",
            }
            source = _create_discovery_source("filesystem", config)

        MockSource.assert_called_once()
        assert source == MockSource.return_value

    def test_entrypoint_source(self):
        """Should create entrypoint discovery source."""
        with patch("mcp_hangar.infrastructure.discovery.EntrypointDiscoverySource") as MockSource:
            source = _create_discovery_source("entrypoint", {"mode": "additive"})

        MockSource.assert_called_once()
        assert source == MockSource.return_value

    def test_unknown_source_returns_none(self):
        """Unknown source type should return None."""
        source = _create_discovery_source("unknown", {"mode": "additive"})

        assert source is None

    def test_authoritative_mode(self):
        """Should handle authoritative mode correctly."""
        with patch("mcp_hangar.infrastructure.discovery.DockerDiscoverySource") as MockSource:
            _create_discovery_source("docker", {"mode": "authoritative"})

        # Check that mode was passed correctly
        call_kwargs = MockSource.call_args.kwargs
        from mcp_hangar.domain.discovery import DiscoveryMode

        assert call_kwargs["mode"] == DiscoveryMode.AUTHORITATIVE


class TestBootstrap:
    """Tests for bootstrap function."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for bootstrap."""
        import mcp_hangar.server.bootstrap  # noqa: F401

        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        # Store originals
        originals = {}
        attrs_to_mock = [
            "_ensure_data_dir",
            "get_runtime",
            "init_context",
            "_init_event_handlers",
            "_init_cqrs",
            "_init_saga",
            "load_configuration",
            "_init_retry_config",
            "_init_knowledge_base",
            "FastMCP",
            "_register_all_tools",
            "_create_background_workers",
            "PROVIDERS",
            "GROUPS",
        ]
        for attr in attrs_to_mock:
            originals[attr] = getattr(bootstrap_mod, attr, None)

        # Create mocks
        mock_data_dir = MagicMock()
        mock_get_runtime = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.rate_limit_config.requests_per_second = 10
        mock_runtime.rate_limit_config.burst_size = 100
        mock_get_runtime.return_value = mock_runtime
        mock_init_context = MagicMock()
        mock_init_eh = MagicMock()
        mock_init_cqrs = MagicMock()
        mock_init_saga = MagicMock()
        mock_load_config = MagicMock(return_value={"discovery": {"enabled": False}})
        mock_init_retry = MagicMock()
        mock_init_kb = MagicMock()
        mock_fastmcp = MagicMock()
        mock_reg_tools = MagicMock()
        mock_create_workers = MagicMock(return_value=[])
        mock_providers = MagicMock()
        mock_providers.keys.return_value = []

        # Apply mocks
        bootstrap_mod._ensure_data_dir = mock_data_dir
        bootstrap_mod.get_runtime = mock_get_runtime
        bootstrap_mod.init_context = mock_init_context
        bootstrap_mod._init_event_handlers = mock_init_eh
        bootstrap_mod._init_cqrs = mock_init_cqrs
        bootstrap_mod._init_saga = mock_init_saga
        bootstrap_mod.load_configuration = mock_load_config
        bootstrap_mod._init_retry_config = mock_init_retry
        bootstrap_mod._init_knowledge_base = mock_init_kb
        bootstrap_mod.FastMCP = mock_fastmcp
        bootstrap_mod._register_all_tools = mock_reg_tools
        bootstrap_mod._create_background_workers = mock_create_workers
        bootstrap_mod.PROVIDERS = mock_providers
        bootstrap_mod.GROUPS = {}

        yield {
            "data_dir": mock_data_dir,
            "get_runtime": mock_get_runtime,
            "init_context": mock_init_context,
            "init_eh": mock_init_eh,
            "init_cqrs": mock_init_cqrs,
            "init_saga": mock_init_saga,
            "load_config": mock_load_config,
            "init_retry": mock_init_retry,
            "init_kb": mock_init_kb,
            "fastmcp": mock_fastmcp,
            "reg_tools": mock_reg_tools,
            "create_workers": mock_create_workers,
        }

        # Restore originals
        for attr, original in originals.items():
            if original is not None:
                setattr(bootstrap_mod, attr, original)

    def test_bootstrap_returns_application_context(self, mock_dependencies):
        """Bootstrap should return ApplicationContext."""
        ctx = bootstrap()

        assert isinstance(ctx, ApplicationContext)

    def test_bootstrap_calls_init_sequence(self, mock_dependencies):
        """Bootstrap should call init functions in order."""
        bootstrap()

        mock_dependencies["data_dir"].assert_called_once()
        mock_dependencies["get_runtime"].assert_called_once()
        mock_dependencies["init_context"].assert_called_once()
        mock_dependencies["init_eh"].assert_called_once()
        mock_dependencies["init_cqrs"].assert_called_once()
        mock_dependencies["init_saga"].assert_called_once()

    def test_bootstrap_with_config_path(self, mock_dependencies):
        """Bootstrap should pass config path to load_configuration."""
        bootstrap(config_path="/path/to/config.yaml")

        mock_dependencies["load_config"].assert_called_once_with("/path/to/config.yaml")

    def test_bootstrap_with_discovery_disabled(self, mock_dependencies):
        """Bootstrap without discovery should have None orchestrator."""
        ctx = bootstrap()

        assert ctx.discovery_orchestrator is None

    def test_bootstrap_creates_mcp_server(self, mock_dependencies):
        """Bootstrap should create FastMCP server."""
        bootstrap()

        mock_dependencies["fastmcp"].assert_called_once_with("mcp-registry")

    def test_bootstrap_registers_tools(self, mock_dependencies):
        """Bootstrap should register all MCP tools."""
        bootstrap()

        mock_dependencies["reg_tools"].assert_called_once()

    def test_bootstrap_creates_workers(self, mock_dependencies):
        """Bootstrap should create background workers."""
        bootstrap()

        mock_dependencies["create_workers"].assert_called_once()
