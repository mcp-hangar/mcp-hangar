"""High-level Hangar Facade.

Provides a simple, user-friendly API for interacting with MCP mcp_servers.
This is the recommended entry point for most use cases.

Example (async):
    async with Hangar.from_config("config.yaml") as hangar:
        result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)  # {"result": 3}

A call made through `invoke` runs through the executor behind `hangar_call`,
under its controls: pass the caller as `principal=`, or it is made as an
anonymous caller. It has no session and no request headers, so session
suspension does not apply to it, and an L7 rule on `Mcp-Param-*` does not fire,
as for `hangar_call` over stdio.

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
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .domain.exceptions import ConfigurationError, McpServerNotFoundError, ToolCallFailedError, ToolNotFoundError
from .domain.value_objects import McpServerMode, McpServerState, Principal
from .logging_config import get_logger

if TYPE_CHECKING:
    from .domain.model import McpServer
    from .server.bootstrap import ApplicationContext

logger = get_logger(__name__)


# --- Configuration Builder ---


@dataclass
class DiscoverySpec:
    """Specification for discovery settings.

    `filesystem` holds at most one directory. The gateway keeps one discovery
    source per type, so a second filesystem source replaces the first rather
    than adding to it; a second path is refused here instead.
    """

    docker: bool = False
    kubernetes: bool = False
    filesystem: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.filesystem) > 1:
            raise ConfigurationError(
                f"discovery takes one filesystem directory, got {len(self.filesystem)}: the gateway "
                "holds one discovery source per type, so a second path would replace the first. "
                "Put the server files in one directory."
            )


# Facade concurrency defaults
FACADE_DEFAULT_CONCURRENCY = 20
"""Default thread pool size for Hangar.invoke() concurrent tool calls."""

FACADE_MAX_CONCURRENCY = 100
"""Upper bound for facade thread pool size."""

#: The builder options each mode's server reads. The builder used to write any
#: option into any spec, so `url=` on a subprocess server or `env=` on a remote
#: one was stored and never read. Group is absent: a group needs members, which
#: this builder cannot declare. A docker or container server's `command` is the
#: container command: `_load_mcp_server_config` passes it as `container_command`
#: and the container launcher runs it.
_OPTIONS_READ_BY_MODE: dict[McpServerMode, frozenset[str]] = {
    McpServerMode.SUBPROCESS: frozenset({"command", "env"}),
    McpServerMode.DOCKER: frozenset({"image", "command", "env"}),
    McpServerMode.CONTAINER: frozenset({"image", "command", "env"}),
    McpServerMode.REMOTE: frozenset({"url"}),
}

#: The option a mode cannot start without. Container mode needs an image as
#: docker does: the config value object does not check it, and the launcher
#: only refuses a missing image at start, long after `build()` returned.
_OPTION_REQUIRED_BY_MODE: dict[McpServerMode, str] = {
    McpServerMode.SUBPROCESS: "command",
    McpServerMode.DOCKER: "image",
    McpServerMode.CONTAINER: "image",
    McpServerMode.REMOTE: "url",
}

#: A builder option whose spec key has another name. The builder's `url=` is
#: its public argument; the gateway reads a remote server's address from
#: `endpoint`, and a spec carrying `url` booted a server with no address.
_SPEC_KEY_FOR_OPTION = {"url": "endpoint"}

#: The mode of every discovery source the builder declares. Additive only adds
#: servers; authoritative also removes the ones a source stops reporting, which
#: a boolean flag should not decide for its caller.
_BUILDER_DISCOVERY_MODE = "additive"


@dataclass
class HangarConfigData:
    """Internal configuration data structure."""

    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    discovery: DiscoverySpec = field(default_factory=DiscoverySpec)
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
            mode: McpServer mode - "subprocess", "docker", "container" or "remote".
            command: Command for subprocess mode, or the container command for
                docker and container mode.
            image: Container image for docker and container mode.
            url: Address of a remote server. Written as the spec's `endpoint`,
                the key the gateway reads.
            env: Environment variables for a subprocess or container server.
            idle_ttl_s: Idle timeout before auto-shutdown (default: 300s).

        Returns:
            Self for chaining.

        Raises:
            ConfigurationError: If the name is empty, the mode is `group`, the
                mode's required option is missing, or an option is given that
                the mode does not read.

        Example:
            config.add_mcp_server("math", command=["python", "-m", "math_server"])
            config.add_mcp_server("fetch", mode="docker", image="mcp/fetch:latest")
            config.add_mcp_server("api", mode="remote", url="http://localhost:8080/mcp")
        """
        self._check_not_built()

        if not name:
            raise ConfigurationError("McpServer name cannot be empty")

        normalized_mode = McpServerMode.normalize(mode)
        options = {
            option: value
            for option, value in {"command": command, "image": image, "url": url, "env": env}.items()
            if value
        }
        _check_server_options(name, normalized_mode, options)

        mcp_server_config: dict[str, Any] = {
            "mode": normalized_mode.value,
            "idle_ttl_s": idle_ttl_s,
        }
        for option, value in options.items():
            mcp_server_config[_SPEC_KEY_FOR_OPTION.get(option, option)] = value

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

        Each requested source becomes one entry of the gateway's
        `discovery.sources`, in `additive` mode: a discovered server is added,
        and a server the source stops reporting is left alone.

        Args:
            docker: Enable Docker container discovery.
            kubernetes: Enable Kubernetes discovery (in-cluster, every
                namespace). Needs the `kubernetes` extra; without it the
                gateway logs `discovery_source_unavailable` and runs the other
                sources.
            filesystem: One directory to scan for mcp_server YAML files, as a
                one-element list.

        Returns:
            Self for chaining.

        Raises:
            ConfigurationError: If no source is requested, or more than one
                filesystem directory is given.

        Example:
            config.enable_discovery(docker=True, filesystem=["./mcp_servers"])
        """
        self._check_not_built()
        if not (docker or kubernetes or filesystem):
            raise ConfigurationError("enable_discovery() needs at least one source: docker, kubernetes or filesystem")
        self._data.discovery = DiscoverySpec(
            docker=docker,
            kubernetes=kubernetes,
            filesystem=list(filesystem or []),
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
        """Refused: the gateway reads no worker interval from its configuration.

        This used to store both values on the built data, where nothing read
        them: the GC and health-check workers run on fixed intervals, and no
        configuration key sets either one. A call that looked like it applied
        a setting and applied nothing is refused rather than kept.

        Raises:
            ConfigurationError: Always.
        """
        self._check_not_built()
        raise ConfigurationError(
            "set_intervals() is refused: the gateway reads no GC or health-check interval from its "
            "configuration, so the value was never applied. Remove the call."
        )

    def build(self) -> HangarConfigData:
        """Build and validate the configuration.

        The configuration is checked against the gateway's config schema, so a
        key the gateway does not read fails here rather than at boot.

        Returns:
            Immutable configuration data.

        Raises:
            ConfigurationError: If the configuration carries a key the gateway
                does not read.
        """
        from .server.config_schema import validate_config

        problems = validate_config(self.to_dict())
        if problems:
            raise ConfigurationError("HangarConfig built keys the gateway does not read:\n  " + "\n  ".join(problems))
        self._built = True
        return self._data

    def to_dict(self) -> dict[str, Any]:
        """The gateway configuration this builder describes, in `config.yaml`'s shape.

        Holds only keys the gateway reads, so it can be passed to
        `bootstrap(config_dict=...)` or saved as YAML. `max_concurrency` is not
        in it: that sizes the facade's own thread pool, not the gateway.

        Returns:
            The configuration dictionary.
        """
        result: dict[str, Any] = {
            "mcp_servers": {name: dict(spec) for name, spec in self._data.mcp_servers.items()},
        }
        sources = _discovery_sources(self._data.discovery)
        if sources:
            result["discovery"] = {"enabled": True, "sources": sources}
        return result

    def _check_not_built(self) -> None:
        """Check that config hasn't been built yet."""
        if self._built:
            raise ConfigurationError("Configuration already built, cannot modify")


def _check_server_options(name: str, mode: McpServerMode, options: dict[str, Any]) -> None:
    """Refuse a server the gateway could not start, or an option it would not read.

    Args:
        name: The server's name, for the message.
        mode: The normalized mode.
        options: The options given, the unset ones left out.

    Raises:
        ConfigurationError: If the mode is `group`, its required option is
            missing, or an option is given that the mode does not read.
    """
    read = _OPTIONS_READ_BY_MODE.get(mode)
    if read is None:
        raise ConfigurationError(
            f"McpServer '{name}': mode {mode.value!r} cannot be built with HangarConfig, which does not "
            "declare a group's members. Declare the group in a config file."
        )
    required = _OPTION_REQUIRED_BY_MODE.get(mode)
    if required is not None and required not in options:
        raise ConfigurationError(f"McpServer '{name}': {required} is required for {mode.value} mode")
    unread = sorted(set(options) - read)
    if unread:
        raise ConfigurationError(
            f"McpServer '{name}': {', '.join(unread)} has no effect on a {mode.value} server and would "
            "be ignored. Remove it."
        )


def _discovery_sources(discovery: DiscoverySpec) -> list[dict[str, Any]]:
    """The `discovery.sources` entries for *discovery*, one per requested type.

    Each entry is what `infrastructure/discovery/registry.py` builds a source
    from: `type`, `mode`, and the source's own keys. The filesystem source reads
    one directory from `path`.
    """
    sources: list[dict[str, Any]] = []
    if discovery.docker:
        sources.append({"type": "docker", "mode": _BUILDER_DISCOVERY_MODE})
    if discovery.kubernetes:
        sources.append({"type": "kubernetes", "mode": _BUILDER_DISCOVERY_MODE})
    for directory in discovery.filesystem:
        sources.append({"type": "filesystem", "mode": _BUILDER_DISCOVERY_MODE, "path": directory})
    return sources


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
        #: Discovery's loop and the thread running it, while discovery runs.
        self._discovery: tuple[asyncio.AbstractEventLoop, threading.Thread] | None = None

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
        and starts the background workers `serve` starts: the GC worker,
        which stops a server idle past its `idle_ttl_s`, the health-check
        worker, the metrics snapshot worker and, for a config file, the
        config reload worker. Discovery starts too, when configured.

        Called automatically when using async context manager. A second call
        while started does nothing.
        """
        if self._started:
            return

        # Import here to avoid circular imports
        from .server.bootstrap import bootstrap

        # Bootstrap with config
        loop = asyncio.get_event_loop()

        if self._config:
            # Programmatic config. `to_dict()` holds only gateway keys: the
            # facade's `max_concurrency` sized the thread pool in `__init__`.
            builder = HangarConfig()
            builder._data = self._config
            gateway_config = builder.to_dict()
            self._context = await loop.run_in_executor(
                self._executor,
                lambda: bootstrap(config_dict=gateway_config),
            )
        else:
            # File-based config
            self._context = await loop.run_in_executor(
                self._executor,
                lambda: bootstrap(config_path=self._config_path),
            )

        await loop.run_in_executor(self._executor, self._start_background)
        self._started = True
        logger.info("hangar_started", config_path=self._config_path)

    async def stop(self) -> None:
        """Stop Hangar and cleanup resources.

        Stops all mcp_servers, and stops the background workers and waits for
        their threads to end. Called automatically when using async context
        manager. A second call does nothing.
        """
        # Not gated on `_started`: a `start()` that raised leaves the thread
        # pool running, and only this releases it. The context is shut down
        # once -- `ApplicationContext.shutdown()` has no guard of its own -- so
        # it is dropped here, and a failed start drops it after shutting it down.
        if self._context:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._stop_discovery)
            await loop.run_in_executor(
                self._executor,
                self._context.shutdown,
            )
            self._context = None

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

    def _start_background(self) -> None:
        """Start the background workers, then discovery when configured.

        `bootstrap()` builds the workers, the orchestrator and its sources, and
        starts none of them; `serve` starts them in `ServerLifecycle`. The
        facade started neither, so an embedded gateway never stopped an idle
        server or health-checked one (#1435), and a discovery section, from a
        file or from `enable_discovery()`, was built and never ran a cycle
        (#1423). The workers start through the function `ServerLifecycle`
        uses, so the two run the same set. `stop()` stops them, through
        `ApplicationContext.shutdown()`.
        """
        from .server.bootstrap.workers import start_background_workers
        from .server.lifecycle import start_discovery_loop

        assert self._context is not None
        context = self._context
        try:
            start_background_workers(context.background_workers)
            if context.discovery_orchestrator is not None:
                self._discovery = start_discovery_loop(context.discovery_orchestrator)
        except Exception:
            # A caller whose `start()` raised does not call `stop()`, so the
            # context bootstrap just built would be left running, its workers
            # with it. Dropped once shut down: `ApplicationContext.shutdown()`
            # has no guard against a second call.
            self._context = None
            context.shutdown()
            raise

    def _stop_discovery(self) -> None:
        """Stop the discovery `_start_discovery` started, if it did."""
        from .server.lifecycle import stop_discovery_loop

        running, self._discovery = self._discovery, None
        orchestrator = self._context.discovery_orchestrator if self._context else None
        if running is None or orchestrator is None:
            return
        stop_discovery_loop(orchestrator, *running)

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
        assert self._context is not None
        mcp_server = self._context.mcp_servers.get(name)
        if not mcp_server:
            raise McpServerNotFoundError(mcp_server_id=name)
        return cast("McpServer", mcp_server)

    async def invoke(
        self,
        mcp_server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
        principal: Principal | None = None,
    ) -> Any:
        """Invoke a tool on a mcp_server or group, under the controls `hangar_call` applies.

        The call runs through the executor behind `hangar_call`, so every
        call-time control the configuration sets applies: tool access and
        withdrawals, digest pins, validators and interceptors, approval, the
        global and per-server concurrency limits, and tenant budgets (#1453).
        Auto-starts the mcp_server if it's cold. The result is returned whole:
        response truncation does not apply to it, and no continuation is stored.

        The call is made on behalf of *principal*. It is authorized for
        `tool:invoke` as an authenticated `hangar_call` caller is, and its
        `tenant_id` is the tenant the per-tenant controls apply to. Without
        one, the call is an anonymous caller's, as an unauthenticated
        `hangar_call` is: refused where authentication is configured, and
        carrying no tenant.

        Nothing verifies the principal: the embedder vouches for its id, groups
        and tenant. `Principal.system()` is refused with `ValueError`, because
        authorization grants the system principal every permission. The call
        has no session and no request headers, so session suspension does not
        apply to it, and an L7 rule on `Mcp-Param-*` does not fire, as for
        `hangar_call` over stdio.

        A tool that needs approval holds one of this facade's pool threads
        until the approval is decided or expires (`approval_timeout_seconds`,
        300 s by default), even after `invoke` has raised `TimeoutError` at
        `timeout_s`. An approval given after that is refused. The same pool
        runs `stop()` and `health()`, so size `max_concurrency` for the
        approvals that can be pending at once.

        Args:
            mcp_server_name: Name of the mcp_server or group.
            tool_name: Name of the tool to invoke.
            arguments: Tool arguments (default: empty dict).
            timeout_s: Timeout in seconds (default: 30s).
            principal: The caller (default: an anonymous caller).

        Returns:
            Tool result.

        Raises:
            ConfigurationError: If Hangar is not started.
            McpServerNotFoundError: If no mcp_server or group has that name.
            ToolNotFoundError: If the mcp_server does not have the tool.
            ToolCallFailedError: If a control refused the call or the tool
                failed. Its `code` is the `error_type` `hangar_call` reports.
            TimeoutError: If invocation times out.
            ValueError: If *principal* is the system principal.

        Example:
            result = await hangar.invoke("math", "add", {"a": 1, "b": 2})

            caller = Principal(id=PrincipalId("agent-1"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id="team-a")
            result = await hangar.invoke("math", "add", {"a": 1, "b": 2}, principal=caller)
        """
        self._ensure_started()
        # Imported here: the batch package reaches `server.bootstrap`.
        from .server.tools.batch import call_as

        caller = principal if principal is not None else Principal.anonymous()
        loop = asyncio.get_event_loop()

        # Run in the thread pool: the executor blocks until the call returns.
        batch = await asyncio.wait_for(
            loop.run_in_executor(
                self._executor,
                lambda: call_as(caller, mcp_server_name, tool_name, arguments or {}, timeout=timeout_s),
            ),
            timeout=timeout_s,
        )
        return _invoke_outcome(mcp_server_name, tool_name, batch)

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
        await loop.run_in_executor(self._executor, mcp_server.start)  # type: ignore[attr-defined]  # start is handled by the execution layer

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
            tools=mcp_server.tools.list_names() if hasattr(mcp_server, "tools") else [],
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
        assert self._context is not None
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


#: Failures `invoke` raised as their own types before it ran through the
#: executor, by the `error_type` the executor reports them with.
_RAISED_AS_BEFORE: dict[str, Any] = {
    "McpServerNotFoundError": lambda server, tool, message: McpServerNotFoundError(mcp_server_id=server),
    "ToolNotFoundError": lambda server, tool, message: ToolNotFoundError(server, tool),
    "TimeoutError": lambda server, tool, message: TimeoutError(message),
}


def _invoke_outcome(mcp_server_name: str, tool_name: str, batch: dict[str, Any]) -> Any:
    """The tool result of the one call `call_as` made, or the exception its failure raises.

    *batch* is what `hangar_call` returns for that call.
    """
    for error in batch.get("validation_errors", ()):
        if error["field"] == "mcp_server":
            raise McpServerNotFoundError(mcp_server_id=mcp_server_name)
        if error["field"] == "tool":
            raise ToolNotFoundError(mcp_server_name, tool_name)
        raise ToolCallFailedError(mcp_server_name, tool_name, "ValidationError", error["message"])

    results = batch.get("results") or []
    if not results:
        raise ToolCallFailedError(mcp_server_name, tool_name, "NoResult", "The call returned no result")
    call = results[0]
    if not call["success"]:
        code = call["error_type"] or "UnknownError"
        message = call["error"] or "Tool call failed"
        raised = _RAISED_AS_BEFORE.get(code)
        if raised is not None:
            raise raised(mcp_server_name, tool_name, message)
        raise ToolCallFailedError(mcp_server_name, tool_name, code, message)
    return call["result"]


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
        principal: Principal | None = None,
    ) -> Any:
        """Invoke a tool on a mcp_server or group, under the controls `hangar_call` applies.

        See Hangar.invoke() for full documentation. Blocks the calling thread
        for up to `timeout_s`.
        """
        return self._run(
            self._hangar.invoke(mcp_server_name, tool_name, arguments, timeout_s=timeout_s, principal=principal)
        )

    def start_mcp_server(self, name: str) -> None:
        """Start a mcp_server."""
        self._run(self._hangar.start_mcp_server(name))

    def stop_mcp_server(self, name: str) -> None:
        """Stop a mcp_server."""
        self._run(self._hangar.stop_mcp_server(name))

    def get_mcp_server(self, name: str) -> McpServerInfo:
        """Get mcp_server information."""
        return cast(McpServerInfo, self._run(self._hangar.get_mcp_server(name)))

    def list_mcp_servers(self) -> list[McpServerInfo]:
        """List all mcp_servers."""
        return cast(list[McpServerInfo], self._run(self._hangar.list_mcp_servers()))

    def health(self) -> HealthSummary:
        """Get health summary."""
        return cast(HealthSummary, self._run(self._hangar.health()))

    def health_check(self, name: str) -> bool:
        """Run health check on a mcp_server."""
        return cast(bool, self._run(self._hangar.health_check(name)))


# legacy aliases
ProviderInfo = McpServerInfo
