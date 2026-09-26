"""Tests for Hangar facade and HangarConfig builder."""

import asyncio
import contextlib
import importlib
import inspect
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar import facade
from mcp_hangar.domain.exceptions import (
    ConfigurationError,
    McpServerNotFoundError,
    ToolCallFailedError,
    ToolInvocationError,
    ToolNotFoundError,
)
from mcp_hangar.domain.value_objects import McpServerMode, McpServerState, Principal, PrincipalId, PrincipalType
from mcp_hangar.facade import (
    FACADE_DEFAULT_CONCURRENCY,
    FACADE_MAX_CONCURRENCY,
    DiscoverySpec,
    Hangar,
    HangarConfig,
    HealthSummary,
    ProviderInfo,
    SyncHangar,
)
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import prepare_config
from mcp_hangar.server.lifecycle import ServerLifecycle

#: The package, not the `bootstrap` function `mcp_hangar.server` re-exports under the same name.
bootstrap_package = importlib.import_module("mcp_hangar.server.bootstrap")

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
        """A remote server's `url=` is written as `endpoint`, the key the gateway reads (#1423)."""
        config = HangarConfig().add_mcp_server("api", mode="remote", url="http://localhost:8080").build()

        assert "api" in config.mcp_servers
        provider = config.mcp_servers["api"]
        assert provider["mode"] == "remote"
        assert provider["endpoint"] == "http://localhost:8080"
        assert "url" not in provider

    def test_group_mode_is_refused(self):
        """The builder cannot declare a group's members, so it does not build a group."""
        with pytest.raises(ConfigurationError, match="does not declare a group's members"):
            HangarConfig().add_mcp_server("pool", mode="group", command=["python"])

    @pytest.mark.parametrize(
        ("mode", "options", "unread"),
        [
            ("subprocess", {"command": ["python"], "url": "http://127.0.0.1:9/mcp"}, "url"),
            ("subprocess", {"command": ["python"], "image": "img:1"}, "image"),
            ("remote", {"url": "http://127.0.0.1:9/mcp", "env": {"A": "1"}}, "env"),
            ("remote", {"url": "http://127.0.0.1:9/mcp", "command": ["python"]}, "command"),
            ("docker", {"image": "img:1", "url": "http://127.0.0.1:9/mcp"}, "url"),
        ],
    )
    def test_an_option_the_mode_does_not_read_is_refused(self, mode, options, unread):
        """An option the server's mode ignores used to be stored and never read."""
        with pytest.raises(ConfigurationError, match=f"{unread} has no effect on a {mode} server"):
            HangarConfig().add_mcp_server("s", mode=mode, **options)

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

    def test_container_without_image_is_refused_at_build(self):
        """The launcher refused a missing image only at start; the builder refuses it up front."""
        with pytest.raises(ConfigurationError, match="image is required for container mode"):
            HangarConfig().add_mcp_server("fetch", mode="container", command=["serve"])

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
        config = HangarConfig().add_mcp_server("math", command=["python"]).max_concurrency(30).build()

        assert config.max_concurrency == 30
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
        """Should enable filesystem discovery with one directory."""
        config = HangarConfig().enable_discovery(filesystem=["./providers"]).build()

        assert config.discovery.filesystem == ["./providers"]

    def test_a_second_filesystem_directory_is_refused(self):
        """The gateway keeps one source per type: a second path would replace the first."""
        with pytest.raises(ConfigurationError, match="one filesystem directory, got 2"):
            HangarConfig().enable_discovery(filesystem=["./providers", "/etc/mcp"])

    def test_a_second_filesystem_directory_is_refused_on_the_data_too(self):
        """`from_builder` takes the data, so the data refuses it without the builder."""
        with pytest.raises(ConfigurationError, match="one filesystem directory"):
            DiscoverySpec(filesystem=["./a", "./b"])

    def test_discovery_with_no_source_is_refused(self):
        """`enable_discovery()` with nothing requested enabled nothing."""
        with pytest.raises(ConfigurationError, match="at least one source"):
            HangarConfig().enable_discovery()

    def test_enable_multiple_discovery_sources(self):
        """Should enable multiple discovery sources."""
        config = HangarConfig().enable_discovery(docker=True, kubernetes=True, filesystem=["./providers"]).build()

        assert config.discovery.docker is True
        assert config.discovery.kubernetes is True
        assert config.discovery.filesystem == ["./providers"]


class TestHangarConfigIntervals:
    """`set_intervals` set two fields nothing read; it is refused."""

    @pytest.mark.parametrize("interval", [{"gc_interval_s": 60}, {"health_check_interval_s": 30}])
    def test_set_intervals_is_refused(self, interval):
        with pytest.raises(ConfigurationError, match="reads no GC or health-check interval"):
            HangarConfig().set_intervals(**interval)


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
        """Discovery is the gateway's `{enabled, sources}`, one additive source per type (#1423)."""
        builder = HangarConfig()
        builder.enable_discovery(docker=True, kubernetes=True, filesystem=["./providers"])
        result = builder.to_dict()

        assert result["discovery"] == {
            "enabled": True,
            "sources": [
                {"type": "docker", "mode": "additive"},
                {"type": "kubernetes", "mode": "additive"},
                {"type": "filesystem", "mode": "additive", "path": "./providers"},
            ],
        }

    def test_to_dict_without_discovery_has_no_discovery_section(self):
        builder = HangarConfig().add_mcp_server("math", command=["python"])

        assert "discovery" not in builder.to_dict()

    def test_to_dict_leaves_out_the_facade_max_concurrency(self):
        """`max_concurrency` sizes the facade's pool; the gateway has no such top-level key."""
        builder = HangarConfig()
        builder.add_mcp_server("math", command=["python"])
        builder.max_concurrency(30)
        result = builder.to_dict()

        assert "max_concurrency" not in result
        assert builder.build().max_concurrency == 30


class TestTheBuilderWritesOnlyKeysTheGatewayReads:
    """Every builder option produces a config the gateway accepts under strict mode (#1423).

    Two builder features wrote keys the gateway does not read -- a remote
    server's `url`, and `discovery.docker`/`.kubernetes`/`.filesystem` -- and
    nothing said so until a dict was schema-checked (#1415). `build()` now runs
    that check itself, so a key like those fails the build.
    """

    #: The options the test below builds. A new builder option fails
    #: `test_the_test_builds_every_option` until it is added here and there.
    SERVER_OPTIONS = frozenset({"mode", "command", "image", "url", "env", "idle_ttl_s"})
    DISCOVERY_OPTIONS = frozenset({"docker", "kubernetes", "filesystem"})
    METHODS = frozenset({"add_mcp_server", "enable_discovery", "max_concurrency", "set_intervals", "build", "to_dict"})

    @staticmethod
    def _every_option(directory: str) -> HangarConfig:
        return (
            HangarConfig()
            .add_mcp_server("local", mode="subprocess", command=["python", "-m", "s"], env={"A": "1"}, idle_ttl_s=120)
            .add_mcp_server("boxed", mode="docker", image="img:1", command=["serve"], env={"A": "1"})
            .add_mcp_server("podded", mode="container", image="img:1", command=["serve"], env={"A": "1"})
            .add_mcp_server("api", mode="remote", url="http://127.0.0.1:9/mcp", idle_ttl_s=60)
            .enable_discovery(docker=True, kubernetes=True, filesystem=[directory])
            .max_concurrency(7)
        )

    def test_the_test_builds_every_option(self):
        server = set(inspect.signature(HangarConfig.add_mcp_server).parameters) - {"self", "name"}
        discovery = set(inspect.signature(HangarConfig.enable_discovery).parameters) - {"self"}
        methods = {name for name, _ in inspect.getmembers(HangarConfig, inspect.isfunction) if not name.startswith("_")}

        assert server == self.SERVER_OPTIONS
        assert discovery == self.DISCOVERY_OPTIONS
        assert methods == self.METHODS

    def test_every_option_passes_the_schema_check_under_strict_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")
        builder = self._every_option(str(tmp_path))
        builder.build()

        # The gateway's own gate for a dict, strict: it raises on any unknown key.
        prepared = prepare_config(builder.to_dict(), source="HangarConfig")

        assert prepared["mcp_servers"]["api"] == {
            "mode": "remote",
            "idle_ttl_s": 60,
            "endpoint": "http://127.0.0.1:9/mcp",
        }
        assert prepared["discovery"]["enabled"] is True
        assert [source["type"] for source in prepared["discovery"]["sources"]] == ["docker", "kubernetes", "filesystem"]

    @pytest.mark.parametrize("mode", ["docker", "container"])
    def test_a_container_server_command_reaches_the_launcher(self, mode, monkeypatch):
        """`command` is in docker's and container's read set because the launcher runs it.

        The config value object sets `command=None` in docker mode, but that is
        not the path a spec takes: `_load_mcp_server_config` passes the spec's
        `command` as `container_command`, and `_create_client` hands it to the
        container launcher as `command`. Traced here from the builder's spec to
        the `launch()` call.
        """

        class Launched(Exception):
            pass

        builder = HangarConfig().add_mcp_server("boxed", mode=mode, image="img:1", command=["serve", "--stdio"])
        added: dict[str, Any] = {}
        repository = SimpleNamespace(
            add=lambda server_id, server: added.update({server_id: server}),
            # What `commit` reads to carry an L7 policy onto a rebuilt server (#1498):
            # here nothing is running under that id yet.
            get=lambda server_id: added.get(server_id),
        )
        monkeypatch.setattr(server_config, "_mcp_server_repository", lambda: repository)
        server_config._load_mcp_server_config("boxed", builder.to_dict()["mcp_servers"]["boxed"])

        launcher = MagicMock()
        launcher.launch.side_effect = Launched
        with patch("mcp_hangar.infrastructure.launchers.get_launcher", return_value=launcher), pytest.raises(Launched):
            added["boxed"]._create_client()

        launched = launcher.launch.call_args.kwargs
        assert launched["command"] == ["serve", "--stdio"]
        assert launched["image"] == "img:1"

    def test_the_old_remote_key_fails_the_build(self, monkeypatch):
        """With `url` no longer mapped to `endpoint`, the build names the key."""
        monkeypatch.setattr(facade, "_SPEC_KEY_FOR_OPTION", {})
        builder = HangarConfig().add_mcp_server("api", mode="remote", url="http://127.0.0.1:9/mcp")

        with pytest.raises(ConfigurationError, match=r"mcp_servers\.api has unknown key\(s\) \['url'\]"):
            builder.build()

    def test_the_old_discovery_shape_fails_the_build(self, monkeypatch):
        """The pre-#1423 `discovery.docker` shape, emitted again, fails the build."""
        emit = HangarConfig.to_dict

        def old_shape(self: HangarConfig) -> dict:
            return {**emit(self), "discovery": {"docker": {"enabled": True}}}

        monkeypatch.setattr(HangarConfig, "to_dict", old_shape)
        builder = HangarConfig().add_mcp_server("math", command=["python"])

        with pytest.raises(ConfigurationError, match=r"discovery has unknown key\(s\) \['docker'\]"):
            builder.build()
        # A refused build leaves the builder open, so the caller can fix it.
        builder.add_mcp_server("other", command=["python"])


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
        summary = HealthSummary(
            mcp_servers={"math": "ready", "fetch": "ready"},
            ready_count=2,
            total_count=2,
        )
        assert summary.all_ready is True

    def test_not_all_ready(self):
        """Should return False when not all providers are ready."""
        summary = HealthSummary(
            mcp_servers={"math": "ready", "fetch": "cold"},
            ready_count=1,
            total_count=2,
        )
        assert summary.all_ready is False

    def test_any_ready(self):
        """Should return True when at least one provider is ready."""
        summary = HealthSummary(
            mcp_servers={"math": "ready", "fetch": "cold"},
            ready_count=1,
            total_count=2,
        )
        assert summary.any_ready is True

    def test_none_ready(self):
        """Should return False when no providers are ready."""
        summary = HealthSummary(
            mcp_servers={"math": "cold", "fetch": "cold"},
            ready_count=0,
            total_count=2,
        )
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


class TestHangarRunsDiscovery:
    """`Hangar.start()` runs the discovery bootstrap built, and `stop()` stops it (#1423).

    `bootstrap()` builds the orchestrator and starts nothing; only `serve`'s
    `ServerLifecycle` started it, so under the facade a discovery section was
    built and never ran. The served boot of a real orchestrator is
    `tests/integration/test_a_builder_config_takes_effect.py`.
    """

    @staticmethod
    def _context(orchestrator):
        context = MagicMock()
        context.discovery_orchestrator = orchestrator
        return context

    async def test_start_and_stop_run_discovery_on_its_own_loop(self):
        loops = []

        async def record():
            loops.append(asyncio.get_running_loop())

        orchestrator = MagicMock()
        orchestrator.start.side_effect = record
        orchestrator.stop.side_effect = record
        orchestrator.get_stats.return_value = {"sources_count": 1}
        context = self._context(orchestrator)
        hangar = Hangar.from_builder(HangarConfig().enable_discovery(docker=True).build())

        with patch.object(bootstrap_package, "bootstrap", return_value=context):
            await hangar.start()
            assert hangar._discovery is not None
            await hangar.stop()
            # A second stop does not shut the context down again.
            await hangar.stop()

        assert len(loops) == 2
        assert loops[0] is loops[1]
        assert loops[0] is not asyncio.get_running_loop()
        assert hangar._discovery is None
        context.shutdown.assert_called_once()

    async def test_no_orchestrator_starts_no_loop(self):
        context = self._context(None)
        hangar = Hangar.from_builder(HangarConfig().add_mcp_server("math", command=["python"]).build())

        with patch.object(bootstrap_package, "bootstrap", return_value=context):
            await hangar.start()
            await hangar.stop()

        assert hangar._discovery is None
        context.shutdown.assert_called_once()

    async def test_a_discovery_that_fails_to_start_shuts_the_context_down(self):
        orchestrator = MagicMock()
        orchestrator.start.side_effect = RuntimeError("no start")
        context = self._context(orchestrator)
        hangar = Hangar.from_builder(HangarConfig().enable_discovery(docker=True).build())

        with patch.object(bootstrap_package, "bootstrap", return_value=context), pytest.raises(RuntimeError):
            await hangar.start()

        assert hangar._started is False
        assert hangar._context is None
        # A caller that stops anyway does not shut the context down a second
        # time, and the stop releases the thread pool the failed start used.
        await hangar.stop()
        context.shutdown.assert_called_once()
        assert hangar._executor._shutdown is True


class _RecordingWorker:
    """A background worker that counts its starts, stops and waits."""

    def __init__(self, task: str) -> None:
        self.task = task
        self.starts = 0
        self.stops = 0
        self.joins = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    def join(self, timeout_s: float | None = None) -> bool:
        self.joins += 1
        return True


#: The workers `bootstrap()` builds for a config file with the defaults.
_WORKER_TASKS = ["gc", "health_check", "metrics_snapshot", "config_reload"]


def _workers() -> list[_RecordingWorker]:
    return [_RecordingWorker(task) for task in _WORKER_TASKS]


def _context_with(workers: list[_RecordingWorker]) -> Any:
    """A real `ApplicationContext`, so `stop()` reaches the workers through its `shutdown()`."""
    runtime = MagicMock()
    runtime.repository.get_all.return_value = {}
    return bootstrap_package.ApplicationContext(
        runtime=runtime,
        mcp_server=MagicMock(),
        background_workers=list(workers),
        discovery_orchestrator=None,
        config={},
    )


@contextlib.contextmanager
def _coordinating(keeper: Any = None, tailer: Any = None):
    """The lease keeper and event tailer bootstrap built: none, unless given."""
    with (
        patch("mcp_hangar.server.lifecycle.get_lease_keeper", return_value=keeper),
        patch("mcp_hangar.server.lifecycle.get_event_tailer", return_value=tailer),
    ):
        yield


@contextlib.contextmanager
def _booting(context: Any):
    """`bootstrap()` hands the facade *context*; the process-wide closers and coordination are left alone."""
    with (
        patch.object(bootstrap_package, "bootstrap", return_value=context),
        patch.object(bootstrap_package, "close_what_bootstrap_started"),
        _coordinating(),
    ):
        yield


def _builder_hangar() -> Hangar:
    return Hangar.from_builder(HangarConfig().add_mcp_server("math", command=["python"]).build())


class TestHangarRunsTheBackgroundWorkers:
    """`Hangar.start()` starts the workers `serve` starts, and `stop()` stops them (#1435).

    Under the facade the GC and health-check workers `bootstrap()` built were
    never started, so an idle server was never stopped and none was health
    checked. The facade and `ServerLifecycle` now start them through one
    function. Real threads, a real idle server and a real health check are in
    `tests/integration/test_the_facade_runs_the_background_workers.py`.
    """

    async def test_start_starts_each_worker_once_and_stop_stops_it(self):
        workers = _workers()
        hangar = _builder_hangar()

        with _booting(_context_with(workers)):
            await hangar.start()
            await hangar.start()  # a second start does nothing
            assert [(w.starts, w.stops) for w in workers] == [(1, 0)] * len(workers)

            await hangar.stop()
            await hangar.stop()  # a second stop does nothing

        assert [(w.starts, w.stops, w.joins) for w in workers] == [(1, 1, 1)] * len(workers)

    def test_the_sync_facade_starts_and_stops_them_too(self):
        workers = _workers()
        hangar = SyncHangar(_builder_hangar())

        with _booting(_context_with(workers)):
            with hangar:
                hangar.start()  # a second start does nothing
                assert [(w.starts, w.stops) for w in workers] == [(1, 0)] * len(workers)
            hangar.stop()  # a second stop does nothing

        assert [(w.starts, w.stops, w.joins) for w in workers] == [(1, 1, 1)] * len(workers)

    async def test_a_worker_that_fails_to_start_shuts_the_context_down(self):
        workers = _workers()
        workers[1].start = Mock(side_effect=RuntimeError("no start"))  # type: ignore[method-assign]
        hangar = _builder_hangar()

        with _booting(_context_with(workers)), pytest.raises(RuntimeError, match="no start"):
            await hangar.start()

        assert hangar._started is False
        assert hangar._context is None
        # The worker that did start is stopped with the others, and a caller
        # that stops anyway does not stop them a second time.
        assert workers[0].starts == 1
        assert [w.stops for w in workers] == [1] * len(workers)
        await hangar.stop()
        assert [w.stops for w in workers] == [1] * len(workers)

    async def test_serve_and_the_facade_start_the_same_workers(self):
        served, embedded = _workers(), _workers()

        lifecycle = ServerLifecycle(_context_with(served))
        with patch("mcp_hangar.server.lifecycle.start_front_door_warm_up"):
            lifecycle.start()

        hangar = _builder_hangar()
        with _booting(_context_with(embedded)):
            await hangar.start()
            await hangar.stop()

        def started(workers: list[_RecordingWorker]) -> list[str]:
            return [w.task for w in workers if w.starts == 1]

        assert started(served) == _WORKER_TASKS
        assert started(embedded) == started(served)


def _recording(order: list[str], name: str) -> MagicMock:
    """A keeper or tailer that records its start and stop in *order*."""
    component = MagicMock()
    component.start.side_effect = lambda: order.append(f"{name} started")
    component.stop.side_effect = lambda: order.append(f"{name} stopped")
    return component


class TestHangarStartsCoordinationAndTheWarmUp:
    """`Hangar.start()` starts the lease keeper, the tailer and the warm-up `serve` starts (#1465).

    The facade started none of them, so an embedded gateway with a
    `coordination:` block never took the management lease, and an embedded
    front door listed nothing until each server was started some other way.
    Real threads, a real lease and a real warm-up are in
    `tests/integration/test_the_facade_starts_coordination_and_the_warm_up.py`.
    """

    async def test_the_lease_is_taken_first_and_released_last(self):
        order: list[str] = []
        keeper, tailer = _recording(order, "keeper"), _recording(order, "tailer")
        context = _context_with(_workers())
        hangar = _builder_hangar()

        with (
            _booting(context),
            _coordinating(keeper, tailer),
            patch.object(context, "shutdown", side_effect=lambda: order.append("context shut down")),
        ):
            await hangar.start()
            await hangar.stop()
            await hangar.stop()  # a second stop does nothing

        assert order == ["keeper started", "tailer started", "tailer stopped", "context shut down", "keeper stopped"]

    async def test_serve_and_the_facade_order_coordination_the_same_way(self):
        served: list[str] = []
        lifecycle = ServerLifecycle(_context_with(_workers()))
        with (
            _coordinating(_recording(served, "keeper"), _recording(served, "tailer")),
            patch.object(lifecycle._context, "shutdown", side_effect=lambda: served.append("context shut down")),
            patch("mcp_hangar.server.lifecycle.start_front_door_warm_up"),
        ):
            lifecycle.start()
            lifecycle.shutdown()

        embedded: list[str] = []
        context = _context_with(_workers())
        hangar = _builder_hangar()
        with (
            _booting(context),
            _coordinating(_recording(embedded, "keeper"), _recording(embedded, "tailer")),
            patch.object(context, "shutdown", side_effect=lambda: embedded.append("context shut down")),
        ):
            await hangar.start()
            await hangar.stop()

        assert embedded == served

    async def test_a_failed_start_releases_the_lease(self):
        order: list[str] = []
        workers = _workers()
        workers[1].start = Mock(side_effect=RuntimeError("no start"))  # type: ignore[method-assign]
        hangar = _builder_hangar()

        with (
            _booting(_context_with(workers)),
            _coordinating(_recording(order, "keeper"), _recording(order, "tailer")),
            pytest.raises(RuntimeError, match="no start"),
        ):
            await hangar.start()

        assert order == ["keeper started", "tailer started", "tailer stopped", "keeper stopped"]
        assert hangar._context is None
        # A start that raised leaves the facade's thread pool running, and only
        # `stop()` releases it (see `Hangar.stop`). Without this the pool's
        # worker outlived the test and was left to garbage collection; under
        # pytest-xdist the unit thread guard ran before that and failed an
        # unrelated test for it.
        await hangar.stop()

    async def test_start_warms_the_front_door_and_stop_stops_the_retry(self):
        order: list[str] = []
        retry = MagicMock()
        retry.run.side_effect = lambda: order.append("retry ran")
        retry.stop.side_effect = lambda: order.append("retry stopped")
        context = _context_with(_workers())
        hangar = _builder_hangar()

        def warm(runtime: Any) -> None:
            assert runtime is context.runtime
            order.append("warmed")

        with (
            _booting(context),
            patch("mcp_hangar.server.lifecycle.warm_the_front_door_catalogue", side_effect=warm),
            patch("mcp_hangar.server.catalogue_readiness.CatalogueRetry", return_value=retry),
        ):
            await hangar.start()
            assert hangar._warm_up is not None
            thread = hangar._warm_up[1]
            await hangar.stop()

        assert order == ["warmed", "retry ran", "retry stopped"]
        assert not thread.is_alive()
        assert hangar._warm_up is None

    async def test_concurrent_starts_bootstrap_once_and_a_stop_waits_for_them(self):
        workers = _workers()
        context = _context_with(workers)
        boots: list[int] = []

        def slow_bootstrap(**_: Any) -> Any:
            boots.append(1)
            time.sleep(0.2)
            return context

        hangar = _builder_hangar()
        with _booting(context), patch.object(bootstrap_package, "bootstrap", side_effect=slow_bootstrap):
            await asyncio.gather(hangar.start(), hangar.start(), hangar.stop())

        assert boots == [1]
        assert [(w.starts, w.stops) for w in workers] == [(1, 1)] * len(workers)
        assert hangar._context is None

    def test_concurrent_sync_starts_bootstrap_once(self):
        workers = _workers()
        context = _context_with(workers)
        boots: list[int] = []

        def slow_bootstrap(**_: Any) -> Any:
            boots.append(1)
            time.sleep(0.2)
            return context

        hangar = SyncHangar(_builder_hangar())
        with _booting(context), patch.object(bootstrap_package, "bootstrap", side_effect=slow_bootstrap):
            starters = [threading.Thread(target=hangar.start) for _ in range(2)]
            for starter in starters:
                starter.start()
            for starter in starters:
                starter.join(10)
            hangar.stop()

        assert boots == [1]
        assert [(w.starts, w.stops) for w in workers] == [(1, 1)] * len(workers)


class TestHangarNotStarted:
    """Tests for Hangar methods when not started."""

    async def test_invoke_raises_when_not_started(self):
        """Should raise ConfigurationError when invoke called before start."""
        hangar = Hangar.from_config("config.yaml")

        with pytest.raises(ConfigurationError, match="not started"):
            await hangar.invoke("math", "add", {"a": 1})

    async def test_list_providers_raises_when_not_started(self):
        """Should raise ConfigurationError when list_providers called before start."""
        hangar = Hangar.from_config("config.yaml")

        with pytest.raises(ConfigurationError, match="not started"):
            await hangar.list_mcp_servers()


def _batch(**call: Any) -> dict[str, Any]:
    """What `hangar_call` returns for one call, with *call*'s fields over a success."""
    result = {
        "index": 0,
        "call_id": "c-1",
        "success": True,
        "result": {"result": 42},
        "error": None,
        "error_type": None,
        "elapsed_ms": 1.0,
        **call,
    }
    ok = bool(result["success"])
    return {
        "batch_id": "b-1",
        "success": ok,
        "total": 1,
        "succeeded": int(ok),
        "failed": int(not ok),
        "elapsed_ms": 1.0,
        "results": [result],
    }


def _invalid(field: str, message: str) -> dict[str, Any]:
    """What `hangar_call` returns for a call its validation refused."""
    return {
        "batch_id": "b-1",
        "success": False,
        "error": "Validation failed",
        "validation_errors": [{"index": 0, "field": field, "message": message}],
    }


@pytest.fixture
def governed(monkeypatch):
    """The executor path `invoke` runs (#1453): answers with `.answer`, records each call in `.calls`.

    Its governance is tested where it lives: `tests/unit/test_tool_invoke_authz.py`
    for the caller, and `tests/integration/test_the_facade_invoke_is_governed_like_hangar_call.py`
    over a real boot.
    """
    import mcp_hangar.server.tools.batch as batch_package

    seen = SimpleNamespace(calls=[], answer=_batch())

    def call_as(principal, mcp_server, tool, arguments, *, timeout):
        seen.calls.append(
            SimpleNamespace(principal=principal, mcp_server=mcp_server, tool=tool, arguments=arguments, timeout=timeout)
        )
        return seen.answer

    monkeypatch.setattr(batch_package, "call_as", call_as)
    return seen


def _caller() -> Principal:
    return Principal(id=PrincipalId("agent-1"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id="tenant:a")


class TestHangarWithMockedContext:
    """Tests for Hangar with mocked ApplicationContext."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider."""
        provider = Mock()
        provider.state = McpServerState.READY
        provider.mode = McpServerMode.SUBPROCESS
        provider.tools = Mock()
        provider.tools.list_names.return_value = ["add", "subtract"]
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
        yield hangar
        # The pool `invoke` and the other methods ran in.
        hangar._executor.shutdown(wait=True)

    @pytest.mark.asyncio
    async def test_invoke_runs_the_call_through_the_executor_path(self, hangar_with_context, mock_provider, governed):
        """The call goes to `hangar_call`'s path, not straight to the server (#1453)."""
        result = await hangar_with_context.invoke("math", "add", {"a": 1, "b": 2})

        assert result == {"result": 42}
        (call,) = governed.calls
        assert (call.mcp_server, call.tool, call.arguments, call.timeout) == ("math", "add", {"a": 1, "b": 2}, 30.0)
        mock_provider.invoke_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_invoke_with_empty_args(self, hangar_with_context, governed):
        """Should invoke tool with empty args when not provided."""
        await hangar_with_context.invoke("math", "list", timeout_s=5.0)

        (call,) = governed.calls
        assert (call.arguments, call.timeout) == ({}, 5.0)

    @pytest.mark.asyncio
    async def test_invoke_without_a_principal_is_an_anonymous_call(self, hangar_with_context, governed):
        await hangar_with_context.invoke("math", "add", {"a": 1})

        assert governed.calls[0].principal.is_anonymous()

    @pytest.mark.asyncio
    async def test_invoke_is_made_as_the_principal_given(self, hangar_with_context, governed):
        caller = _caller()

        await hangar_with_context.invoke("math", "add", {"a": 1}, principal=caller)

        assert governed.calls[0].principal is caller

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code",
        [
            "AuthorizationDenied",
            "ToolAccessDeniedError",
            "ToolWithdrawnError",
            "ToolDigestMismatchError",
            "ValidatorDenied",
            "TenantQuotaExceeded",
            "CircuitBreakerOpen",
            "EgressPolicyDeniedError",
        ],
    )
    async def test_a_call_that_did_not_succeed_raises_with_its_code(self, hangar_with_context, governed, code):
        governed.answer = _batch(success=False, result=None, error="what hangar_call says", error_type=code)

        with pytest.raises(ToolCallFailedError) as raised:
            await hangar_with_context.invoke("math", "add")

        assert (raised.value.code, raised.value.message) == (code, "what hangar_call says")
        assert (raised.value.mcp_server_id, raised.value.tool_name) == ("math", "add")
        # Caught by the handlers written for the exception `invoke` documented.
        assert isinstance(raised.value, ToolInvocationError)

    @pytest.mark.asyncio
    async def test_invoke_unknown_provider_raises_error(self, hangar_with_context, governed):
        """Should raise McpServerNotFoundError for a name that is neither a server nor a group."""
        governed.answer = _invalid("mcp_server", "McpServer 'unknown' not found")

        with pytest.raises(McpServerNotFoundError):
            await hangar_with_context.invoke("unknown", "tool")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("answer", "raised"),
        [
            (
                _batch(success=False, result=None, error="gone", error_type="McpServerNotFoundError"),
                McpServerNotFoundError,
            ),
            (_invalid("tool", "Tool 'x' not found in mcp_server 'math'"), ToolNotFoundError),
            (
                _batch(success=False, result=None, error="Tool not found: x", error_type="ToolNotFoundError"),
                ToolNotFoundError,
            ),
            (_batch(success=False, result=None, error="Timeout", error_type="TimeoutError"), TimeoutError),
        ],
    )
    async def test_the_failures_invoke_raised_by_their_own_type_still_are(
        self, hangar_with_context, governed, answer, raised
    ):
        governed.answer = answer

        with pytest.raises(raised):
            await hangar_with_context.invoke("math", "x")

    @pytest.mark.asyncio
    async def test_any_other_invalid_call_raises_a_validation_code(self, hangar_with_context, governed):
        governed.answer = _invalid("arguments", "arguments must be a dictionary")

        with pytest.raises(ToolCallFailedError) as raised:
            await hangar_with_context.invoke("math", "add")

        assert (raised.value.code, raised.value.message) == ("ValidationError", "arguments must be a dictionary")

    @pytest.mark.asyncio
    async def test_a_batch_with_no_result_raises_no_result(self, hangar_with_context, governed):
        governed.answer = {**_batch(), "total": 0, "succeeded": 0, "results": []}

        with pytest.raises(ToolCallFailedError) as raised:
            await hangar_with_context.invoke("math", "add")

        assert (raised.value.code, raised.value.message) == ("NoResult", "The call returned no result")

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
        sync_hangar = SyncHangar(hangar)
        yield sync_hangar
        # The pool `invoke` ran in, and the loop the wrapper opened.
        hangar._executor.shutdown(wait=True)
        if sync_hangar._loop is not None:
            sync_hangar._loop.close()

    def test_invoke_returns_result(self, sync_hangar_with_context, mock_provider, governed):
        """Should invoke tool synchronously, through the executor path (#1453)."""
        result = sync_hangar_with_context.invoke("math", "add", {"a": 1})

        assert result == {"result": 42}
        assert governed.calls[0].principal.is_anonymous()
        mock_provider.invoke_tool.assert_not_called()

    def test_invoke_is_made_as_the_principal_given(self, sync_hangar_with_context, governed):
        caller = _caller()

        sync_hangar_with_context.invoke("math", "add", {"a": 1}, principal=caller, timeout_s=7.0)

        (call,) = governed.calls
        assert (call.principal, call.timeout) == (caller, 7.0)

    def test_list_mcp_servers(self, sync_hangar_with_context):
        """Should list providers synchronously."""
        providers = sync_hangar_with_context.list_mcp_servers()

        assert len(providers) == 1
        assert providers[0].name == "math"

    def test_health(self, sync_hangar_with_context):
        """Should get health synchronously."""
        health = sync_hangar_with_context.health()

        assert health.total_count == 1
