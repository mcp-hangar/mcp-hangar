"""High-level Hangar Facade.

Provides a simple, user-friendly API for interacting with MCP providers.
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
        .add_provider("math", command=["python", "-m", "math_server"])
        .add_provider("fetch", mode="docker", image="mcp/fetch:latest")
        .build()
    )
    hangar = Hangar(config)
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .domain.exceptions import ConfigurationError, ProviderNotFoundError
from .domain.value_objects import ProviderMode, ProviderState
from .logging_config import get_logger

if TYPE_CHECKING:
    from .domain.model import Provider
    from .server.bootstrap import ApplicationContext

logger = get_logger(__name__)


# --- Configuration Builder ---


@dataclass
class ProviderSpec:
    """Specification for a single provider.

    Internal use - created via HangarConfig.add_provider().
    """

    name: str
    mode: ProviderMode
    command: list[str] | None = None
    image: str | None = None
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300

    def to_dict(self) -> dict[str, Any]:
        """Convert to config dict format."""
        result: dict[str, Any] = {
            "mode": self.mode.value,
            "idle_ttl_s": self.idle_ttl_s,
        }
        if self.command:
            result["command"] = self.command
        if self.image:
            result["image"] = self.image
        if self.url:
            result["url"] = self.url
        if self.env:
            result["env"] = self.env
        return result


@dataclass
class DiscoverySpec:
    """Specification for discovery settings."""

    docker: bool = False
    kubernetes: bool = False
    filesystem: list[str] = field(default_factory=list)


@dataclass
class HangarConfigData:
    """Internal configuration data structure."""

    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    discovery: DiscoverySpec = field(default_factory=DiscoverySpec)
    gc_interval_s: int = 30
    health_check_interval_s: int = 10


class HangarConfig:
    """Fluent builder for Hangar configuration.

    Example:
        config = (
            HangarConfig()
            .add_provider("math", command=["python", "-m", "math_server"])
            .add_provider("fetch", mode="docker", image="mcp/fetch:latest")
            .add_provider("api", mode="remote", url="http://localhost:8080")
            .enable_discovery(docker=True)
            .build()
        )
    """

    def __init__(self) -> None:
        """Initialize empty configuration."""
        self._data = HangarConfigData()
        self._built = False

    def add_provider(
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
        """Add a provider to the configuration.

        Args:
            name: Unique provider name.
            mode: Provider mode - "subprocess", "docker", or "remote".
            command: Command for subprocess mode.
            image: Docker image for docker mode.
            url: URL for remote mode.
            env: Environment variables for the provider.
            idle_ttl_s: Idle timeout before auto-shutdown (default: 300s).

        Returns:
            Self for chaining.

        Raises:
            ConfigurationError: If provider name is empty or mode is invalid.

        Example:
            config.add_provider("math", command=["python", "-m", "math_server"])
            config.add_provider("fetch", mode="docker", image="mcp/fetch:latest")
        """
        self._check_not_built()

        if not name:
            raise ConfigurationError("Provider name cannot be empty")

        normalized_mode = ProviderMode.normalize(mode)

        # Validate mode-specific requirements
        if normalized_mode == ProviderMode.SUBPROCESS and not command:
            raise ConfigurationError(f"Provider '{name}': command is required for subprocess mode")
        if normalized_mode == ProviderMode.DOCKER and not image:
            raise ConfigurationError(f"Provider '{name}': image is required for docker mode")
        if normalized_mode == ProviderMode.REMOTE and not url:
            raise ConfigurationError(f"Provider '{name}': url is required for remote mode")

        self._data.providers[name] = ProviderSpec(
            name=name,
            mode=normalized_mode,
            command=command,
            image=image,
            url=url,
            env=env or {},
            idle_ttl_s=idle_ttl_s,
        )
        return self

    def enable_discovery(
        self,
        *,
        docker: bool = False,
        kubernetes: bool = False,
        filesystem: list[str] | None = None,
    ) -> HangarConfig:
        """Enable provider discovery.

        Args:
            docker: Enable Docker container discovery.
            kubernetes: Enable Kubernetes discovery.
            filesystem: List of paths to scan for provider YAML files.

        Returns:
            Self for chaining.

        Example:
            config.enable_discovery(docker=True, filesystem=["./providers"])
        """
        self._check_not_built()
        self._data.discovery = DiscoverySpec(
            docker=docker,
            kubernetes=kubernetes,
            filesystem=filesystem or [],
        )
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
            "providers": {name: spec.to_dict() for name, spec in self._data.providers.items()},
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


# --- Provider Info ---


@dataclass(frozen=True)
class ProviderInfo:
    """Information about a provider.

    Immutable snapshot of provider state.
    """

    name: str
    state: str
    mode: str
    tools: list[str]
    last_used: float | None = None
    error: str | None = None

    @property
    def is_ready(self) -> bool:
        """Check if provider is ready to handle requests."""
        return self.state == "ready"

    @property
    def is_cold(self) -> bool:
        """Check if provider is not started."""
        return self.state == "cold"


@dataclass(frozen=True)
class HealthSummary:
    """Health summary for all providers."""

    providers: dict[str, str]  # name -> state
    ready_count: int
    total_count: int

    @property
    def all_ready(self) -> bool:
        """Check if all providers are ready."""
        return self.ready_count == self.total_count

    @property
    def any_ready(self) -> bool:
        """Check if at least one provider is ready."""
        return self.ready_count > 0


# --- Async Hangar Facade ---


class Hangar:
    """High-level async facade for MCP Hangar.

    Provides a simple API for managing providers and invoking tools.
    Handles provider lifecycle automatically (auto-start on invoke).

    Example:
        async with Hangar.from_config("config.yaml") as hangar:
            # List providers
            providers = await hangar.list_providers()

            # Invoke a tool (auto-starts provider if needed)
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
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hangar-")
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
            config = HangarConfig().add_provider(...).build()
            hangar = Hangar.from_builder(config)
        """
        return cls(config=config)

    async def start(self) -> None:
        """Start Hangar and initialize all components.

        This bootstraps the application context, registers providers,
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

        Stops all providers and background workers.
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

    def _get_provider(self, name: str) -> Provider:
        """Get provider by name.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.
        """
        self._ensure_started()
        provider = self._context.providers.get(name)
        if not provider:
            raise ProviderNotFoundError(provider_id=name)
        return provider

    async def invoke(
        self,
        provider_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> Any:
        """Invoke a tool on a provider.

        Auto-starts the provider if it's cold.

        Args:
            provider_name: Name of the provider.
            tool_name: Name of the tool to invoke.
            arguments: Tool arguments (default: empty dict).
            timeout_s: Timeout in seconds (default: 30s).

        Returns:
            Tool result.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.
            ToolNotFoundError: If tool doesn't exist.
            ToolInvocationError: If tool invocation fails.
            TimeoutError: If invocation times out.

        Example:
            result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        """
        provider = self._get_provider(provider_name)
        loop = asyncio.get_event_loop()

        # Run invoke in thread pool (Provider is sync)
        result = await asyncio.wait_for(
            loop.run_in_executor(
                self._executor,
                lambda: provider.invoke_tool(tool_name, arguments or {}),
            ),
            timeout=timeout_s,
        )
        return result

    async def start_provider(self, name: str) -> None:
        """Explicitly start a provider.

        Args:
            name: Provider name.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.
            ProviderStartError: If provider fails to start.

        Example:
            await hangar.start_provider("math")
        """
        provider = self._get_provider(name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, provider.start)

    async def stop_provider(self, name: str) -> None:
        """Stop a provider.

        Args:
            name: Provider name.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.

        Example:
            await hangar.stop_provider("math")
        """
        provider = self._get_provider(name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, provider.stop)

    async def get_provider(self, name: str) -> ProviderInfo:
        """Get information about a provider.

        Args:
            name: Provider name.

        Returns:
            ProviderInfo with current state.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.

        Example:
            info = await hangar.get_provider("math")
            print(f"State: {info.state}, Tools: {info.tools}")
        """
        provider = self._get_provider(name)

        return ProviderInfo(
            name=name,
            state=provider.state.value if isinstance(provider.state, ProviderState) else str(provider.state),
            mode=provider.mode.value if isinstance(provider.mode, ProviderMode) else str(provider.mode),
            tools=list(provider.tools.keys()) if hasattr(provider, "tools") else [],
            last_used=getattr(provider, "_last_used", None),
            error=None,
        )

    async def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers.

        Returns:
            List of ProviderInfo for all providers.

        Example:
            providers = await hangar.list_providers()
            for p in providers:
                print(f"{p.name}: {p.state}")
        """
        self._ensure_started()
        result = []
        for name in self._context.providers.keys():
            try:
                info = await self.get_provider(name)
                result.append(info)
            except Exception as e:
                # Include provider even if we can't get full info
                result.append(
                    ProviderInfo(
                        name=name,
                        state="unknown",
                        mode="unknown",
                        tools=[],
                        error=str(e),
                    )
                )
        return result

    async def health(self) -> HealthSummary:
        """Get health summary for all providers.

        Returns:
            HealthSummary with provider states.

        Example:
            health = await hangar.health()
            if health.all_ready:
                print("All providers ready!")
            else:
                print(f"Ready: {health.ready_count}/{health.total_count}")
        """
        providers = await self.list_providers()
        states = {p.name: p.state for p in providers}
        ready_count = sum(1 for p in providers if p.is_ready)

        return HealthSummary(
            providers=states,
            ready_count=ready_count,
            total_count=len(providers),
        )

    async def health_check(self, name: str) -> bool:
        """Run health check on a specific provider.

        Args:
            name: Provider name.

        Returns:
            True if health check passed, False otherwise.

        Raises:
            ProviderNotFoundError: If provider doesn't exist.
        """
        provider = self._get_provider(name)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, provider.health_check)


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
        provider_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> Any:
        """Invoke a tool on a provider.

        See Hangar.invoke() for full documentation.
        """
        return self._run(self._hangar.invoke(provider_name, tool_name, arguments, timeout_s=timeout_s))

    def start_provider(self, name: str) -> None:
        """Start a provider."""
        self._run(self._hangar.start_provider(name))

    def stop_provider(self, name: str) -> None:
        """Stop a provider."""
        self._run(self._hangar.stop_provider(name))

    def get_provider(self, name: str) -> ProviderInfo:
        """Get provider information."""
        return self._run(self._hangar.get_provider(name))

    def list_providers(self) -> list[ProviderInfo]:
        """List all providers."""
        return self._run(self._hangar.list_providers())

    def health(self) -> HealthSummary:
        """Get health summary."""
        return self._run(self._hangar.health())

    def health_check(self, name: str) -> bool:
        """Run health check on a provider."""
        return self._run(self._hangar.health_check(name))
