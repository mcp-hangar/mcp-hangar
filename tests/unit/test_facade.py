"""Tests for Hangar facade and HangarConfig builder."""

import asyncio
from unittest.mock import Mock

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError, McpServerNotFoundError
from mcp_hangar.domain.value_objects import McpServerMode, McpServerState
from mcp_hangar.facade import (
    FACADE_DEFAULT_CONCURRENCY,
    FACADE_MAX_CONCURRENCY,
    Hangar,
    HangarConfig,
    HealthSummary,
    ProviderInfo,
    SyncHangar,
)

# --- HangarConfig Builder Tests ---


class TestHangarConfig:
    """Tests for HangarConfig builder."""

    def test_add_subprocess_provider(self):
        """Should add subprocess provider with command."""
        config = HangarConfig().add_mcp_server("math", command=["python", "-m", "math_server"]).build()

        assert "math" in config.mcp_servers
        provider = config.mcp_servers["math"]
        assert provider["mode"] == "subprocess"
        assert provider["command"] == ["python", "-m", "math_server"]

    def test_add_docker_provider(self):
        """Should add docker provider with image."""
        config = HangarConfig().add_mcp_server("fetch", mode="docker", image="mcp/fetch:latest").build()

        assert "fetch" in config.mcp_servers
        provider = config.mcp_servers["fetch"]
        assert provider["mode"] == "docker"
        assert provider["image"] == "mcp/fetch:latest"

    def test_add_remote_provider(self):
        """Should add remote provider with URL."""
        config = HangarConfig().add_mcp_server("api", mode="remote", url="http://localhost:8080").build()

        assert "api" in config.mcp_servers
        provider = config.mcp_servers["api"]
        assert provider["mode"] == "remote"
        assert provider["url"] == "http://localhost:8080"

    def test_add_provider_with_env(self):
        """Should add provider with environment variables."""
        config = (
            HangarConfig()
            .add_mcp_server(
                "math",
                command=["python", "-m", "math_server"],
                env={"DEBUG": "true", "LOG_LEVEL": "debug"},
            )
            .build()
        )

        provider = config.mcp_servers["math"]
        assert provider["env"] == {"DEBUG": "true", "LOG_LEVEL": "debug"}

    def test_add_provider_with_custom_idle_ttl(self):
        """Should add provider with custom idle TTL."""
        config = HangarConfig().add_mcp_server("math", command=["python"], idle_ttl_s=600).build()

        provider = config.mcp_servers["math"]
        assert provider["idle_ttl_s"] == 600

    def test_add_multiple_providers(self):
        """Should add multiple providers."""
        config = (
            HangarConfig()
            .add_mcp_server("math", command=["python", "-m", "math"])
            .add_mcp_server("fetch", mode="docker", image="mcp/fetch")
            .add_mcp_server("api", mode="remote", url="http://api.local")
            .build()
        )

        assert len(config.mcp_servers) == 3
        assert "math" in config.mcp_servers
        assert "fetch" in config.mcp_servers
        assert "api" in config.mcp_servers

    def test_mode_normalization(self):
        """Should accept container as valid mode (treated as docker-like)."""
        config = HangarConfig().add_mcp_server("fetch", mode="container", image="mcp/fetch").build()

        # container is accepted and stored as-is (alias for docker behavior)
        assert config.mcp_servers["fetch"]["mode"] == "container"

    def test_empty_name_raises_error(self):
        """Should raise ConfigurationError for empty provider name."""
        with pytest.raises(ConfigurationError, match="cannot be empty"):
            HangarConfig().add_mcp_server("", command=["python"])

    def test_subprocess_without_command_raises_error(self):
        """Should raise ConfigurationError for subprocess without command."""
        with pytest.raises(ConfigurationError, match="command is required"):
            HangarConfig().add_mcp_server("math", mode="subprocess")

    def test_docker_without_image_raises_error(self):
        """Should raise ConfigurationError for docker without image."""
        with pytest.raises(ConfigurationError, match="image is required"):
            HangarConfig().add_mcp_server("fetch", mode="docker")

    def test_remote_without_url_raises_error(self):
        """Should raise ConfigurationError for remote without URL."""
        with pytest.raises(ConfigurationError, match="url is required"):
            HangarConfig().add_mcp_server("api", mode="remote")

    def test_cannot_modify_after_build(self):
        """Should raise ConfigurationError when modifying after build."""
        config = HangarConfig()
        config.add_mcp_server("math", command=["python"])
        config.build()

        with pytest.raises(ConfigurationError, match="already built"):
            config.add_mcp_server("another", command=["python"])


class TestHangarConfigMaxConcurrency:
    """Tests for HangarConfig max_concurrency setting."""

    def test_default_max_concurrency(self):
        """Default max_concurrency should be FACADE_DEFAULT_CONCURRENCY (20)."""
        config = HangarConfig().add_mcp_server("math", command=["python"]).build()

        assert config.max_concurrency == FACADE_DEFAULT_CONCURRENCY
        assert config.max_concurrency == 20

    def test_set_max_concurrency(self):
        """Should set max_concurrency via builder method."""
        config = HangarConfig().max_concurrency(50).add_mcp_server("math", command=["python"]).build()

        assert config.max_concurrency == 50

    def test_max_concurrency_minimum_valid(self):
        """Should accept max_concurrency of 1."""
        config = HangarConfig().max_concurrency(1).build()

        assert config.max_concurrency == 1

    def test_max_concurrency_maximum_valid(self):
        """Should accept max_concurrency at upper bound."""
        config = HangarConfig().max_concurrency(FACADE_MAX_CONCURRENCY).build()

        assert config.max_concurrency == FACADE_MAX_CONCURRENCY

    def test_max_concurrency_zero_raises_error(self):
        """Should raise ValueError for max_concurrency of 0."""
        with pytest.raises(ValueError, match="max_concurrency must be between 1 and"):
            HangarConfig().max_concurrency(0)

    def test_max_concurrency_negative_raises_error(self):
        """Should raise ValueError for negative max_concurrency."""
        with pytest.raises(ValueError, match="max_concurrency must be between 1 and"):
            HangarConfig().max_concurrency(-1)

    def test_max_concurrency_exceeds_upper_bound_raises_error(self):
        """Should raise ValueError for max_concurrency above upper bound."""
        with pytest.raises(ValueError, match="max_concurrency must be between 1 and"):
            HangarConfig().max_concurrency(FACADE_MAX_CONCURRENCY + 1)

    def test_max_concurrency_cannot_set_after_build(self):
        """Should raise ConfigurationError when setting max_concurrency after build."""
        config = HangarConfig()
        config.build()

        with pytest.raises(ConfigurationError, match="already built"):
            config.max_concurrency(10)

    def test_max_concurrency_chaining(self):
        """max_concurrency should return self for fluent chaining."""
        config = (
            HangarConfig()
            .add_mcp_server("math", command=["python"])
            .max_concurrency(30)
            .set_intervals(gc_interval_s=60)
            .build()
        )

        assert config.max_concurrency == 30
        assert config.gc_interval_s == 60
        assert "math" in config.mcp_servers


class TestHangarConfigDiscovery:
    """Tests for HangarConfig discovery settings."""

    def test_enable_docker_discovery(self):
        """Should enable Docker discovery."""
        config = HangarConfig().enable_discovery(docker=True).build()

        assert config.discovery.docker is True
        assert config.discovery.kubernetes is False

    def test_enable_kubernetes_discovery(self):
        """Should enable Kubernetes discovery."""
        config = HangarConfig().enable_discovery(kubernetes=True).build()

        assert config.discovery.kubernetes is True

    def test_enable_filesystem_discovery(self):
        """Should enable filesystem discovery with paths."""
        config = HangarConfig().enable_discovery(filesystem=["./providers", "/etc/mcp"]).build()

        assert config.discovery.filesystem == ["./providers", "/etc/mcp"]

    def test_enable_multiple_discovery_sources(self):
        """Should enable multiple discovery sources."""
        config = HangarConfig().enable_discovery(docker=True, kubernetes=True, filesystem=["./providers"]).build()

        assert config.discovery.docker is True
        assert config.discovery.kubernetes is True
        assert config.discovery.filesystem == ["./providers"]


class TestHangarConfigIntervals:
    """Tests for HangarConfig interval settings."""

    def test_set_gc_interval(self):
        """Should set GC interval."""
        config = HangarConfig().set_intervals(gc_interval_s=60).build()

        assert config.gc_interval_s == 60

    def test_set_health_check_interval(self):
        """Should set health check interval."""
        config = HangarConfig().set_intervals(health_check_interval_s=30).build()

        assert config.health_check_interval_s == 30


class TestHangarConfigToDict:
    """Tests for HangarConfig.to_dict() method."""

    def test_to_dict_basic_provider(self):
        """Should convert basic provider config to dict."""
        builder = HangarConfig()
        builder.add_mcp_server("math", command=["python", "-m", "math"])
        result = builder.to_dict()

        assert "mcp_servers" in result
        assert "math" in result["mcp_servers"]
        assert result["mcp_servers"]["math"]["mode"] == "subprocess"
        assert result["mcp_servers"]["math"]["command"] == ["python", "-m", "math"]

    def test_to_dict_with_discovery(self):
        """Should include discovery in dict when enabled."""
        builder = HangarConfig()
        builder.enable_discovery(docker=True, filesystem=["./providers"])
        result = builder.to_dict()

        assert "discovery" in result
        assert result["discovery"]["docker"] == {"enabled": True}
        assert result["discovery"]["filesystem"]["paths"] == ["./providers"]

    def test_to_dict_includes_max_concurrency(self):
        """Should include max_concurrency in dict output."""
        builder = HangarConfig()
        builder.add_mcp_server("math", command=["python"])
        builder.max_concurrency(30)
        result = builder.to_dict()

        assert result["max_concurrency"] == 30

    def test_to_dict_includes_default_max_concurrency(self):
        """Should include default max_concurrency in dict output."""
        builder = HangarConfig()
        builder.add_mcp_server("math", command=["python"])
        result = builder.to_dict()

        assert result["max_concurrency"] == FACADE_DEFAULT_CONCURRENCY


# --- ProviderInfo Tests ---


class TestProviderInfo:
    """Tests for ProviderInfo dataclass."""

    def test_is_ready(self):
        """Should return True when state is ready."""
        info = ProviderInfo(name="math", state="ready", mode="subprocess", tools=["add"])
        assert info.is_ready is True

    def test_is_not_ready(self):
        """Should return False when state is not ready."""
        info = ProviderInfo(name="math", state="cold", mode="subprocess", tools=[])
        assert info.is_ready is False

    def test_is_cold(self):
        """Should return True when state is cold."""
        info = ProviderInfo(name="math", state="cold", mode="subprocess", tools=[])
        assert info.is_cold is True

    def test_is_not_cold(self):
        """Should return False when state is not cold."""
        info = ProviderInfo(name="math", state="ready", mode="subprocess", tools=["add"])
        assert info.is_cold is False


# --- HealthSummary Tests ---


class TestHealthSummary:
    """Tests for HealthSummary dataclass."""

    def test_all_ready(self):
        """Should return True when all providers are ready."""
        summary = HealthSummary(mcp_servers={"math": "ready", "fetch": "ready"}, ready_count=2,
        total_count=2,)
        assert summary.all_ready is True

    def test_not_all_ready(self):
        """Should return False when not all providers are ready."""
        summary = HealthSummary(mcp_servers={"math": "ready", "fetch": "cold"}, ready_count=1,
        total_count=2,)
        assert summary.all_ready is False

    def test_any_ready(self):
        """Should return True when at least one provider is ready."""
        summary = HealthSummary(mcp_servers={"math": "ready", "fetch": "cold"}, ready_count=1,
        total_count=2,)
        assert summary.any_ready is True

    def test_none_ready(self):
        """Should return False when no providers are ready."""
        summary = HealthSummary(mcp_servers={"math": "cold", "fetch": "cold"}, ready_count=0,
        total_count=2,)
        assert summary.any_ready is False


# --- Hangar Facade Tests ---


class TestHangarInitialization:
    """Tests for Hangar initialization."""

    def test_from_config_creates_instance(self):
        """Should create Hangar instance from config path."""
        hangar = Hangar.from_config("config.yaml")
        assert hangar._config_path == "config.yaml"
        assert hangar._started is False

    def test_from_builder_creates_instance(self):
        """Should create Hangar instance from builder config."""
        config = HangarConfig().add_mcp_server("math", command=["python"]).build()
        hangar = Hangar.from_builder(config)
        assert hangar._config is config
        assert hangar._started is False

    def test_from_builder_uses_configured_max_concurrency(self):
        """Executor should use max_concurrency from builder config."""
        config = HangarConfig().add_mcp_server("math", command=["python"]).max_concurrency(42).build()
        hangar = Hangar.from_builder(config)

        assert hangar._executor._max_workers == 42

    def test_from_builder_uses_default_max_concurrency(self):
        """Executor should default to FACADE_DEFAULT_CONCURRENCY when not explicitly set."""
        config = HangarConfig().add_mcp_server("math", command=["python"]).build()
        hangar = Hangar.from_builder(config)

        assert hangar._executor._max_workers == FACADE_DEFAULT_CONCURRENCY

    def test_from_config_uses_default_max_concurrency(self):
        """Executor should default to FACADE_DEFAULT_CONCURRENCY for file-based config."""
        hangar = Hangar.from_config("config.yaml")

        assert hangar._executor._max_workers == FACADE_DEFAULT_CONCURRENCY

    def test_executor_not_hardcoded_to_four(self):
        """Executor must not be hardcoded to 4 workers (the original bug)."""
        hangar = Hangar.from_config("config.yaml")
        assert hangar._executor._max_workers != 4

        config = HangarConfig().add_mcp_server("math", command=["python"]).build()
        hangar2 = Hangar.from_builder(config)
        assert hangar2._executor._max_workers != 4


class TestHangarNotStarted:
    """Tests for Hangar methods when not started."""

    def test_invoke_raises_when_not_started(self):
        """Should raise ConfigurationError when invoke called before start."""
        hangar = Hangar.from_config("config.yaml")

        with pytest.raises(ConfigurationError, match="not started"):
            asyncio.run(hangar.invoke("math", "add", {"a": 1}))

    def test_list_providers_raises_when_not_started(self):
        """Should raise ConfigurationError when list_providers called before start."""
        hangar = Hangar.from_config("config.yaml")

        with pytest.raises(ConfigurationError, match="not started"):
            asyncio.run(hangar.list_mcp_servers())


class TestHangarWithMockedContext:
    """Tests for Hangar with mocked ApplicationContext."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider."""
        provider = Mock()
        provider.state = McpServerState.READY
        provider.mode = McpServerMode.SUBPROCESS
        provider.tools = {"add": Mock(), "subtract": Mock()}
        provider.invoke_tool.return_value = {"result": 42}
        provider.health_check.return_value = True
        return provider

    @pytest.fixture
    def mock_context(self, mock_provider):
        """Create a mock ApplicationContext."""
        context = Mock()
        context.mcp_servers = {"math": mock_provider}
        context.shutdown = Mock()
        return context

    @pytest.fixture
    def hangar_with_context(self, mock_context):
        """Create Hangar with pre-initialized context."""
        hangar = Hangar(config_path="config.yaml", _context=mock_context)
        hangar._started = True
        return hangar

    @pytest.mark.asyncio
    async def test_invoke_calls_provider(self, hangar_with_context, mock_provider):
        """Should invoke tool on provider."""
        result = await hangar_with_context.invoke("math", "add", {"a": 1, "b": 2})

        mock_provider.invoke_tool.assert_called_once_with("add", {"a": 1, "b": 2})
        assert result == {"result": 42}

    @pytest.mark.asyncio
    async def test_invoke_with_empty_args(self, hangar_with_context, mock_provider):
        """Should invoke tool with empty args when not provided."""
        await hangar_with_context.invoke("math", "list")

        mock_provider.invoke_tool.assert_called_once_with("list", {})

    @pytest.mark.asyncio
    async def test_invoke_unknown_provider_raises_error(self, hangar_with_context):
        """Should raise ProviderNotFoundError for unknown provider."""
        with pytest.raises(McpServerNotFoundError):
            await hangar_with_context.invoke("unknown", "tool")

    @pytest.mark.asyncio
    async def test_get_provider_returns_info(self, hangar_with_context):
        """Should return ProviderInfo for existing provider."""
        info = await hangar_with_context.get_mcp_server("math")

        assert info.name == "math"
        assert info.state == "ready"
        assert info.mode == "subprocess"
        assert set(info.tools) == {"add", "subtract"}

    @pytest.mark.asyncio
    async def test_list_providers_returns_all(self, hangar_with_context):
        """Should return list of all providers."""
        providers = await hangar_with_context.list_mcp_servers()

        assert len(providers) == 1
        assert providers[0].name == "math"

    @pytest.mark.asyncio
    async def test_health_returns_summary(self, hangar_with_context):
        """Should return health summary."""
        health = await hangar_with_context.health()

        assert health.total_count == 1
        assert health.ready_count == 1
        assert health.mcp_servers == {"math": "ready"}

    @pytest.mark.asyncio
    async def test_health_check_calls_provider(self, hangar_with_context, mock_provider):
        """Should call health_check on provider."""
        result = await hangar_with_context.health_check("math")

        mock_provider.health_check.assert_called_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_start_mcp_server(self, hangar_with_context, mock_provider):
        """Should start provider."""
        await hangar_with_context.start_mcp_server("math")

        mock_provider.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_mcp_server(self, hangar_with_context, mock_provider):
        """Should stop provider."""
        await hangar_with_context.stop_mcp_server("math")

        mock_provider.stop.assert_called_once()


# --- SyncHangar Tests ---


class TestSyncHangar:
    """Tests for SyncHangar wrapper."""

    def test_from_config_creates_instance(self):
        """Should create SyncHangar from config path."""
        hangar = SyncHangar.from_config("config.yaml")
        assert hangar._hangar._config_path == "config.yaml"

    def test_from_builder_creates_instance(self):
        """Should create SyncHangar from builder config."""
        config = HangarConfig().add_mcp_server("math", command=["python"]).build()
        hangar = SyncHangar.from_builder(config)
        assert hangar._hangar._config is config


class TestSyncHangarWithMockedContext:
    """Tests for SyncHangar with mocked context."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider."""
        provider = Mock()
        provider.state = McpServerState.READY
        provider.mode = McpServerMode.SUBPROCESS
        provider.tools = {"add": Mock()}
        provider.invoke_tool.return_value = {"result": 42}
        return provider

    @pytest.fixture
    def sync_hangar_with_context(self, mock_provider):
        """Create SyncHangar with pre-initialized context."""
        context = Mock()
        context.mcp_servers = {"math": mock_provider}
        context.shutdown = Mock()

        hangar = Hangar(config_path="config.yaml", _context=context)
        hangar._started = True
        return SyncHangar(hangar)

    def test_invoke_returns_result(self, sync_hangar_with_context, mock_provider):
        """Should invoke tool synchronously."""
        result = sync_hangar_with_context.invoke("math", "add", {"a": 1})

        mock_provider.invoke_tool.assert_called_once()
        assert result == {"result": 42}

    def test_list_mcp_servers(self, sync_hangar_with_context):
        """Should list providers synchronously."""
        providers = sync_hangar_with_context.list_mcp_servers()

        assert len(providers) == 1
        assert providers[0].name == "math"

    def test_health(self, sync_hangar_with_context):
        """Should get health synchronously."""
        health = sync_hangar_with_context.health()

        assert health.total_count == 1
