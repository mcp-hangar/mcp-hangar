"""High-level Hangar Facade.

Provides a simple, user-friendly API for interacting with MCP mcp_servers.
This is the recommended entry point for most use cases.

Example (async):
    async with Hangar.from_config("config.yaml") as hangar:
        result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)  # {"result": 3}

Example (sync):
    from mcp_hangar import SyncHangar

    with SyncHangar.from_config("config.yaml") as hangar:
        result = hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)

Example (programmatic config):
    config = (
        HangarConfig()
        .add_mcp_server("math", command=["python", "-m", "math_server"])
        .add_mcp_server("fetch", mode="docker", image="mcp/fetch:latest")
        .build()
    )
    hangar = Hangar(config)
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .domain.exceptions import ConfigurationError, McpServerNotFoundError
from .domain.value_objects import McpServerMode, McpServerState
from .logging_config import get_logger

if TYPE_CHECKING:
    from .domain.model import McpServer
    from .server.bootstrap import ApplicationContext

logger = get_logger(__name__)


# --- Configuration Builder ---


@dataclass
class DiscoverySpec:
    """Specification for discovery settings."""

    docker: bool = False
    kubernetes: bool = False
    filesystem: list[str] = field(default_factory=list)


# Facade concurrency defaults
FACADE_DEFAULT_CONCURRENCY = 20
"""Default thread pool size for Hangar.invoke() concurrent tool calls."""

FACADE_MAX_CONCURRENCY = 100
"""Upper bound for facade thread pool size."""


@dataclass
class HangarConfigData:
    """Internal configuration data structure."""

    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    discovery: DiscoverySpec = field(default_factory=DiscoverySpec)
    gc_interval_s: int = 30
    health_check_interval_s: int = 10
    max_concurrency: int = FACADE_DEFAULT_CONCURRENCY


class HangarConfig:
    """Fluent builder for Hangar configuration.

    Example:
        config = (
            HangarConfig()
            .add_mcp_server("math", command=["python", "-m", "math_server"])
            .add_mcp_server("fetch", mode="docker", image="mcp/fetch:latest")
            .add_mcp_server("api", mode="remote", url="http://localhost:8080")
            .enable_discovery(docker=True)
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize empty configuration."""
        self._data = HangarConfigData()
        self._built = False

    def add_mcp_server(
        self,
        name: str,
        *,
        mode: str = "subprocess",
        command: list[str] | None = None,
        image: str | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int = 300,
    ) -> HangarConfig:
        """Add a mcp_server to the configuration.

        Args:
            name: Unique mcp_server name.
            mode: McpServer mode - "subprocess", "docker", or "remote".
            command: Command for subprocess mode.
            image: Docker image for docker mode.
            url: URL for remote mode.
            env: Environment variables for the mcp_server.
            idle_ttl_s: Idle timeout before auto-shutdown (default: 300s).

        Returns:
            Self for chaining.

        Raises:
            ConfigurationError: If mcp_server name is empty or mode is invalid.

        Example:
            config.add_mcp_server("math", command=["python", "-m", "math_server"])
            config.add_mcp_server("fetch", mode="docker", image="mcp/fetch:latest")
        """
        self._check_not_built()

        if not name:
            raise ConfigurationError("McpServer name cannot be empty")

        normalized_mode = McpServerMode.normalize(mode)

        # Validate mode-specific requirements
        if normalized_mode == McpServerMode.SUBPROCESS and not command:
            raise ConfigurationError(f"McpServer '{name}': command is required for subprocess mode")
        if normalized_mode == McpServerMode.DOCKER and not image:
            raise ConfigurationError(f"McpServer '{name}': image is required for docker mode")
        if normalized_mode == McpServerMode.REMOTE and not url:
            raise ConfigurationError(f"McpServer '{name}': url is required for remote mode")

        mcp_server_config: dict[str, Any] = {
            "mode": normalized_mode.value,
            "idle_ttl_s": idle_ttl_s,
        }
        if command:
            mcp_server_config["command"] = command
        if image:
            mcp_server_config["image"] = image
        if url:
            mcp_server_config["url"] = url
        if env:
            mcp_server_config["env"] = env

        self._data.mcp_servers[name] = mcp_server_config
        return self

    def enable_discovery(
        self,
        *,
        docker: bool = False,
        kubernetes: bool = False,
        filesystem: list[str] | None = None,
    ) -> HangarConfig:
        """Enable mcp_server discovery.

        Args:
            docker: Enable Docker container discovery.
            kubernetes: Enable Kubernetes discovery.
            filesystem: List of paths to scan for mcp_server YAML files.

        Returns:
            Self for chaining.

        Example:
            config.enable_discovery(docker=True, filesystem=["./mcp_servers"])
        """
        self._check_not_built()
        self._data.discovery = DiscoverySpec(
            docker=docker,
            kubernetes=kubernetes,
            filesystem=filesystem or [],
        )
        return self

    def max_concurrency(self, value: int) -> HangarConfig:
        """Set maximum concurrent tool invocations via Hangar.invoke().

        Controls the thread pool size for the async facade.
        Default: 20. Range: 1-100.

        Args:
            value: Maximum concurrent invocations.

        Returns:
            Self for chaining.

        Raises:
            ValueError: If value is outside the allowed range.
        """
        self._check_not_built()
        if value < 1 or value > FACADE_MAX_CONCURRENCY:
            raise ValueError(f"max_concurrency must be between 1 and {FACADE_MAX_CONCURRENCY}, got {value}")
        self._data.max_concurrency = value
        return self

    def set_intervals(
        self,
        *,
        gc_interval_s: int | None = None,
        health_check_interval_s: int | None = None,
    ) -> HangarConfig:
        """Set background worker intervals.

        Args:
            gc_interval_s: Garbage collection interval (default: 30s).
            health_check_interval_s: Health check interval (default: 10s).

        Returns:
            Self for chaining.
        """
        self._check_not_built()
        if gc_interval_s is not None:
            self._data.gc_interval_s = gc_interval_s
        if health_check_interval_s is not None:
            self._data.health_check_interval_s = health_check_interval_s
        return self

    def build(self) -> HangarConfigData:
        """Build and validate the configuration.

        Returns:
            Immutable configuration data.

        Raises:
            ConfigurationError: If configuration is invalid.
        """
        self._built = True
        return self._data

    def to_dict(self) -> dict[str, Any]:
        """Convert to config dict format (compatible with YAML config).

        Returns:
            Dictionary that can be passed to bootstrap or saved as YAML.
        """
        result: dict[str, Any] = {
            "mcp_servers": dict(self._data.mcp_servers),
            "max_concurrency": self._data.max_concurrency,
        }

        # Add discovery if enabled
        discovery = self._data.discovery
        if discovery.docker or discovery.kubernetes or discovery.filesystem:
            result["discovery"] = {}
            if discovery.docker:
                result["discovery"]["docker"] = {"enabled": True}
            if discovery.kubernetes:
                result["discovery"]["kubernetes"] = {"enabled": True}
            if discovery.filesystem:
                result["discovery"]["filesystem"] = {
                    "enabled": True,
                    "paths": discovery.filesystem,
                }

        return result

    def _check_not_built(self) -> None:
        """Check that config hasn't been built yet."""
        if self._built:
            raise ConfigurationError("Configuration already built, cannot modify")


# --- McpServer Info ---


@dataclass(frozen=True)
class McpServerInfo:
    """Information about a mcp_server.

    Immutable snapshot of mcp_server state.
    """

    name: str
    state: str
    mode: str
    tools: list[str]
    last_used: float | None = None
    error: str | None = None

    @property
    def is_ready(self) -> bool:
        """Check if mcp_server is ready to handle requests."""
        return self.state == "ready"

    @property
    def is_cold(self) -> bool:
        """Check if mcp_server is not started."""
        return self.state == "cold"


@dataclass(frozen=True)
class HealthSummary:
    """Health summary for all mcp_servers."""

    mcp_servers: dict[str, str]  # name -> state
    ready_count: int
    total_count: int

    @property
    def all_ready(self) -> bool:
        """Check if all mcp_servers are ready."""
        return self.ready_count == self.total_count

    @property
    def any_ready(self) -> bool:
        """Check if at least one mcp_server is ready."""
        return self.ready_count > 0


# --- Async Hangar Facade ---


class Hangar:
    """High-level async facade for MCP Hangar.

    Provides a simple API for managing mcp_servers and invoking tools.
    Handles mcp_server lifecycle automatically (auto-start on invoke).

    Example:
        async with Hangar.from_config("config.yaml") as hangar:
            # List mcp_servers
            mcp_servers = await hangar.list_mcp_servers()

            # Invoke a tool (auto-starts mcp_server if needed)
            result = await hangar.invoke("math", "add", {"a": 1, "b": 2})

            # Check health
            health = await hangar.health()
            print(f"Ready: {health.ready_count}/{health.total_count}")
    """

    def __init__(
        self,
        config: HangarConfigData | None = None,
        *,
        config_path: str | Path | None = None,
        _context: ApplicationContext | None = None,
    ) -> None:
        """Initialize Hangar.

        Use from_config() class method for easier initialization.

        Args:
            config: Programmatic configuration from HangarConfig.build().
            config_path: Path to YAML config file.
            _context: Internal - pre-initialized ApplicationContext.
        """
        self._config = config
        self._config_path = str(config_path) if config_path else None
        self._context = _context
        pool_size = config.max_concurrency if config else FACADE_DEFAULT_CONCURRENCY
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="hangar-")
        self._started = False

    @classmethod
    def from_config(cls, config_path: str | Path) -> Hangar:
        """Create Hangar from YAML config file.

        Args:
            config_path: Path to configuration file.

        Returns:
            Hangar instance (not yet started).

        Example:
            hangar = Hangar.from_config("config.yaml")
            await hangar.start()
        """
        return cls(config_path=config_path)

    @classmethod
    def from_builder(cls, config: HangarConfigData) -> Hangar:
        """Create Hangar from programmatic configuration.

        Args:
            config: Configuration from HangarConfig.build().

        Returns:
            Hangar instance (not yet started).

        Example:
            config = HangarConfig().add_mcp_server(...).build()
            hangar = Hangar.from_builder(config)
        """
        return cls(config=config)

    async def start(self) -> None:
        """Start Hangar and initialize all components.

        This bootstraps the application context, registers mcp_servers,
        and starts background workers.

        Called automatically when using async context manager.
        """
        if self._started:
            return

        # Import here to avoid circular imports
        from .server.bootstrap import bootstrap

        # Bootstrap with config
        loop = asyncio.get_event_loop()

        if self._config:
            # Programmatic config - convert to dict and bootstrap
            config_dict = HangarConfig()
            config_dict._data = self._config
            self._context = await loop.run_in_executor(
                self._executor,
                lambda: bootstrap(config_dict=config_dict.to_dict()),
            )
        else:
            # File-based config
            self._context = await loop.run_in_executor(
                self._executor,
                lambda: bootstrap(config_path=self._config_path),
            )

        self._started = True
        logger.info("hangar_started", config_path=self._config_path)

    async def stop(self) -> None:
        """Stop Hangar and cleanup resources.

        Stops all mcp_servers and background workers.
        Called automatically when using async context manager.
        """
        if not self._started:
            return

        if self._context:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                self._context.shutdown,
            )

        self._executor.shutdown(wait=False)
        self._started = False
        logger.info("hangar_stopped")

    async def __aenter__(self) -> Hangar:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()

    def _ensure_started(self) -> None:
        """Ensure Hangar is started."""
        if not self._started or not self._context:
            raise ConfigurationError(
                "Hangar not started. Use 'async with Hangar.from_config(...) as hangar:' "
                "or call 'await hangar.start()' first."
            )

    def _get_mcp_server(self, name: str) -> McpServer:
        """Get mcp_server by name.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.
        """
        self._ensure_started()
        mcp_server = self._context.mcp_servers.get(name)
        if not mcp_server:
            raise McpServerNotFoundError(mcp_server_id=name)
        return mcp_server

    async def invoke(
        self,
        mcp_server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> Any:
        """Invoke a tool on a mcp_server.

        Auto-starts the mcp_server if it's cold.

        Args:
            mcp_server_name: Name of the mcp_server.
            tool_name: Name of the tool to invoke.
            arguments: Tool arguments (default: empty dict).
            timeout_s: Timeout in seconds (default: 30s).

        Returns:
            Tool result.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.
            ToolNotFoundError: If tool doesn't exist.
            ToolInvocationError: If tool invocation fails.
            TimeoutError: If invocation times out.

        Example:
            result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        """
        mcp_server = self._get_mcp_server(mcp_server_name)
        loop = asyncio.get_event_loop()

        # Run invoke in thread pool (McpServer is sync)
        result = await asyncio.wait_for(
            loop.run_in_executor(
                self._executor,
                lambda: mcp_server.invoke_tool(tool_name, arguments or {}),
            ),
            timeout=timeout_s,
        )
        return result

    async def start_mcp_server(self, name: str) -> None:
        """Explicitly start a mcp_server.

        Args:
            name: McpServer name.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.
            McpServerStartError: If mcp_server fails to start.

        Example:
            await hangar.start_mcp_server("math")
        """
        mcp_server = self._get_mcp_server(name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, mcp_server.start)

    async def stop_mcp_server(self, name: str) -> None:
        """Stop a mcp_server.

        Args:
            name: McpServer name.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.

        Example:
            await hangar.stop_mcp_server("math")
        """
        mcp_server = self._get_mcp_server(name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, mcp_server.stop)

    async def get_mcp_server(self, name: str) -> McpServerInfo:
        """Get information about a mcp_server.

        Args:
            name: McpServer name.

        Returns:
            McpServerInfo with current state.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.

        Example:
            info = await hangar.get_mcp_server("math")
            print(f"State: {info.state}, Tools: {info.tools}")
        """
        mcp_server = self._get_mcp_server(name)

        return McpServerInfo(
            name=name,
            state=mcp_server.state.value if isinstance(mcp_server.state, McpServerState) else str(mcp_server.state),
            mode=mcp_server.mode.value if isinstance(mcp_server.mode, McpServerMode) else str(mcp_server.mode),
            tools=list(mcp_server.tools.keys()) if hasattr(mcp_server, "tools") else [],
            last_used=getattr(mcp_server, "_last_used", None),
            error=None,
        )

    async def list_mcp_servers(self) -> list[McpServerInfo]:
        """List all registered mcp_servers.

        Returns:
            List of McpServerInfo for all mcp_servers.

        Example:
            mcp_servers = await hangar.list_mcp_servers()
            for p in mcp_servers:
                print(f"{p.name}: {p.state}")
        """
        self._ensure_started()
        result = []
        for name in self._context.mcp_servers.keys():
            try:
                info = await self.get_mcp_server(name)
                result.append(info)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: single mcp_server info failure must not break list_mcp_servers
                # Include mcp_server even if we can't get full info
                result.append(
                    McpServerInfo(
                        name=name,
                        state="unknown",
                        mode="unknown",
                        tools=[],
                        error=str(e),
                    )
                )
        return result

    async def health(self) -> HealthSummary:
        """Get health summary for all mcp_servers.

        Returns:
            HealthSummary with mcp_server states.

        Example:
            health = await hangar.health()
            if health.all_ready:
                print("All mcp_servers ready!")
            else:
                print(f"Ready: {health.ready_count}/{health.total_count}")
        """
        mcp_servers = await self.list_mcp_servers()
        states = {p.name: p.state for p in mcp_servers}
        ready_count = sum(1 for p in mcp_servers if p.is_ready)

        return HealthSummary(
            mcp_servers=states,
            ready_count=ready_count,
            total_count=len(mcp_servers),
        )

    async def health_check(self, name: str) -> bool:
        """Run health check on a specific mcp_server.

        Args:
            name: McpServer name.

        Returns:
            True if health check passed, False otherwise.

        Raises:
            McpServerNotFoundError: If mcp_server doesn't exist.
        """
        mcp_server = self._get_mcp_server(name)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, mcp_server.health_check)


# --- Sync Wrapper ---


class SyncHangar:
    """Synchronous wrapper for Hangar.

    Provides the same API as Hangar but with synchronous methods.
    Useful for scripts and simple use cases where async is not needed.

    Example:
        with SyncHangar.from_config("config.yaml") as hangar:
            result = hangar.invoke("math", "add", {"a": 1, "b": 2})
            print(result)
    """

    def __init__(self, hangar: Hangar) -> None:
        """Initialize sync wrapper.

        Args:
            hangar: Async Hangar instance to wrap.
        """
        self._hangar = hangar
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_config(cls, config_path: str | Path) -> SyncHangar:
        """Create SyncHangar from YAML config file.

        Args:
            config_path: Path to configuration file.

        Returns:
            SyncHangar instance.
        """
        return cls(Hangar.from_config(config_path))

    @classmethod
    def from_builder(cls, config: HangarConfigData) -> SyncHangar:
        """Create SyncHangar from programmatic configuration.

        Args:
            config: Configuration from HangarConfig.build().

        Returns:
            SyncHangar instance.
        """
        return cls(Hangar.from_builder(config))

    def _run(self, coro):
        """Run coroutine synchronously."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def start(self) -> None:
        """Start Hangar."""
        self._run(self._hangar.start())

    def stop(self) -> None:
        """Stop Hangar."""
        self._run(self._hangar.stop())
        if self._loop:
            self._loop.close()
            self._loop = None

    def __enter__(self) -> SyncHangar:
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()

    def invoke(
        self,
        mcp_server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> Any:
        """Invoke a tool on a mcp_server.

        See Hangar.invoke() for full documentation.
        """
        return self._run(self._hangar.invoke(mcp_server_name, tool_name, arguments, timeout_s=timeout_s))

    def start_mcp_server(self, name: str) -> None:
        """Start a mcp_server."""
        self._run(self._hangar.start_mcp_server(name))

    def stop_mcp_server(self, name: str) -> None:
        """Stop a mcp_server."""
        self._run(self._hangar.stop_mcp_server(name))

    def get_mcp_server(self, name: str) -> McpServerInfo:
        """Get mcp_server information."""
        return self._run(self._hangar.get_mcp_server(name))

    def list_mcp_servers(self) -> list[McpServerInfo]:
        """List all mcp_servers."""
        return self._run(self._hangar.list_mcp_servers())

    def health(self) -> HealthSummary:
        """Get health summary."""
        return self._run(self._hangar.health())

    def health_check(self, name: str) -> bool:
        """Run health check on a mcp_server."""
        return self._run(self._hangar.health_check(name))


# legacy aliases
ProviderInfo = McpServerInfo
