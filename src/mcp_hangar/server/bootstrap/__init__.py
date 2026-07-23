"""Application Bootstrap - Composition Root.

This module is responsible for wiring up all dependencies and initializing
application components. It is the composition root of the application.

The bootstrap process:
1. Load configuration
2. Initialize observability (tracing, Langfuse)
3. Initialize runtime (event bus, command bus, query bus)
4. Initialize event store (for event sourcing)
5. Register event handlers
6. Register CQRS handlers
7. Initialize sagas
8. Load mcp_servers from config
9. Initialize discovery (if enabled)
10. Create MCP server with tools
11. Create background workers (DO NOT START)

Key principle: Bootstrap returns a fully configured but NOT running application.
Starting is handled by the lifecycle module.
"""

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast, TYPE_CHECKING

from mcp_hangar._sdk_compat import FastMCP

from ...application.commands.load_handlers import LoadMcpServerHandler, UnloadMcpServerHandler
from ...application.discovery import DiscoveryOrchestrator
from ...application.ports.observability import ObservabilityPort
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...gc import BackgroundWorker
from ...logging_config import get_logger
from ..config import load_config, load_configuration
from ..context import get_context, init_context
from ..state import get_runtime, GROUPS

from .components import ServerComponents, get_auth_compat_exports, load_components

from .cqrs import init_cqrs, init_auth_cqrs, init_saga, save_group_circuit_breakers
from .discovery import _auto_add_volumes, _create_discovery_source, create_discovery_orchestrator
from .event_handlers import init_event_handlers
from .event_store import init_event_store
from .hot_loading import init_hot_loading
from .logs import init_log_buffers
from .observability import init_observability, shutdown_observability
from .retry_config import init_retry_config
from .tools import register_all_tools
from .truncation import init_truncation
from .workers import (
    create_background_workers,
    GC_WORKER_INTERVAL_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
)

WorkerLike = BackgroundWorker | Any

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime
    from ...application.discovery.discovery_registry import DiscoveryRegistry

logger = get_logger(__name__)


@dataclass
class ApplicationContext:
    """Fully initialized application context.

    Contains all components needed to run the server.
    Components are initialized but not started.
    """

    runtime: "Runtime"
    """Runtime instance with buses and repository."""

    mcp_server: FastMCP
    """FastMCP server instance with registered tools."""

    background_workers: list[WorkerLike] = field(default_factory=list)
    """Background workers (GC, health check) - not started."""

    discovery_orchestrator: DiscoveryOrchestrator | None = None
    """Discovery orchestrator if enabled - not started."""

    auth_components: Any = None
    """Authentication and authorization components."""

    config: dict[str, Any] = field(default_factory=dict)
    """Full configuration dictionary."""

    load_mcp_server_handler: LoadMcpServerHandler | None = None
    """Handler for loading mcp_servers at runtime."""

    unload_mcp_server_handler: UnloadMcpServerHandler | None = None
    """Handler for unloading mcp_servers at runtime."""

    observability_adapter: ObservabilityPort | None = None
    """Observability adapter for tracing (Langfuse, etc.)."""

    saga_state_store: SagaStateStore | NullSagaStateStore | None = None
    """Saga state store for persisting saga state and circuit breakers."""

    discovery_registry: "DiscoveryRegistry | None" = None
    """Discovery source registry (wraps DiscoveryOrchestrator)."""

    approval_service: Any = None
    """Approval gate service (when approvals are configured)."""

    @property
    def mcp_servers(self):
        """Get mcp_servers mapping for easy access."""
        return self.runtime.repository

    def shutdown(self) -> None:
        """Graceful shutdown of all components.

        Stops background workers, discovery orchestrator, observability, and cleans up resources.
        """
        logger.info("application_context_shutdown_start")

        # Stop background workers
        for worker in self.background_workers:
            try:
                worker.stop()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if individual worker stop fails
                logger.warning(
                    "worker_stop_failed",
                    task=worker.task,
                    error=str(e),
                )

        # Save circuit breaker state for mcp_server groups before stopping
        if self.saga_state_store is not None:
            try:
                save_group_circuit_breakers(self.saga_state_store, GROUPS)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if CB save fails
                logger.warning("circuit_breaker_save_failed", error=str(e))

        # Stop all mcp_servers
        for mcp_server_id, mcp_server in self.runtime.repository.get_all().items():
            try:
                mcp_server.stop()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if individual mcp_server stop fails
                logger.warning(
                    "mcp_server_stop_failed",
                    mcp_server_id=mcp_server_id,
                    error=str(e),
                )

        # Shutdown observability (tracing, Langfuse)
        shutdown_observability(self.observability_adapter)

        logger.info("application_context_shutdown_complete")


def _ensure_data_dir() -> None:
    """Ensure data directory exists for persistent storage."""
    data_dir = Path("./data")
    if not data_dir.exists():
        try:
            data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
            logger.info("data_directory_created", path=str(data_dir.absolute()))
        except OSError as e:
            logger.warning("data_directory_creation_failed", error=str(e))


def bootstrap(
    config_path: str | None = None,
    config_dict: dict[str, Any] | None = None,
) -> ApplicationContext:
    """Bootstrap the application.

    Initializes all components in correct order:
    1. Ensure data directory exists
    2. Initialize runtime (event bus, command bus, query bus)
    3. Initialize event store (for event sourcing)
    4. Initialize application context
    5. Register event handlers
    6. Register CQRS handlers
    7. Initialize sagas
    8. Load configuration and mcp_servers
    9. Initialize retry configuration
    10. Create MCP server with tools
    11. Create background workers (DO NOT START)
    12. Initialize discovery (if enabled, DO NOT START)

    Args:
        config_path: Optional path to config.yaml
        config_dict: Optional configuration dictionary (takes precedence over config_path)

    Returns:
        Fully initialized ApplicationContext (components not started)
    """
    logger.info("bootstrap_start", config_path=config_path, has_config_dict=config_dict is not None)

    # Ensure data directory exists
    _ensure_data_dir()

    # Load configuration early (needed for runtime rate-limit and event store config)
    if config_dict is not None:
        # Use provided config dict, merge with defaults
        full_config = load_configuration(None)
        full_config.update(config_dict)
        # Load mcp_servers from config_dict
        mcp_servers_config = config_dict.get("mcp_servers", {})
        if mcp_servers_config:
            load_config(mcp_servers_config)
    else:
        full_config = load_configuration(config_path)

    # Initialize runtime and context. The rate_limit section (config > env > default)
    # is applied when the runtime singleton is first constructed.
    runtime = get_runtime(rate_limit=full_config.get("rate_limit"))
    init_context(runtime)

    # Initialize observability (tracing, Langfuse) early
    _, observability_adapter = init_observability(full_config)

    # Initialize event store for event sourcing
    init_event_store(runtime, full_config)

    # Initialize event handlers
    init_event_handlers(runtime)

    # Initialize CQRS (base handlers; discovery handlers registered after DiscoveryRegistry is created)
    init_cqrs(runtime, config_path)
    # Initialize saga with persistence
    saga_state_store = init_saga(full_config)

    # Apply config.yaml rate_limit overrides (config takes precedence over env)
    from ...bootstrap.runtime import apply_rate_limit_config

    apply_rate_limit_config(runtime, full_config)

    logger.info(
        "security_config_loaded",
        rate_limit_rps=runtime.rate_limit_config.requests_per_second,
        burst_size=runtime.rate_limit_config.burst_size,
    )

    # Add rate limit middleware to command bus
    from ...infrastructure.command_bus import RateLimitMiddleware

    rate_limit_mw = RateLimitMiddleware(rate_limiter=cast(Any, runtime.rate_limiter))
    runtime.command_bus.add_middleware(rate_limit_mw)

    # Deprecation warning for legacy license key env var
    if os.environ.get("HANGAR_LICENSE_KEY"):
        warnings.warn(
            "HANGAR_LICENSE_KEY is deprecated and has no effect. All features are now available under the MIT license.",
            DeprecationWarning,
            stacklevel=1,
        )

    # Load optional auth / approval components unconditionally
    components = load_components(
        config=full_config,
        event_bus=runtime.event_bus,
        event_publisher=lambda event: runtime.event_bus.publish(event),
    )

    # Wire optional components with null fallbacks
    auth_components = components.auth_components if components.auth_components is not None else NullAuthComponents()

    init_auth_cqrs(runtime, auth_components)

    # Wire auth components onto the global application context so the API
    # permission guard (`_check_permission`, server/api/mcp_servers.py) can reach
    # the authz middleware. `init_context()` ran earlier with no auth_components,
    # so without this the guard reads `auth_components=None`, finds
    # `authz_middleware is None`, and fail-OPENs (returns early) -- disabling RBAC
    # enforcement entirely (any authenticated principal passes every check). (SECURITY)
    get_context().auth_components = auth_components

    # Initialize retry configuration
    init_retry_config(full_config)

    # Initialize truncation system
    init_truncation(full_config)

    # Initialize hot-loading components
    load_handler, unload_handler = init_hot_loading(runtime, full_config)

    # Create MCP server and register tools
    mcp_server = FastMCP("mcp-registry")
    register_all_tools(mcp_server)

    # Wire the ADR-014 governed task-relay serving surface. This HTTP-serve path
    # builds FastMCP directly (not via MCPServerFactory), so without this call
    # ctx.governed_task_store stays None and every upstream task handle is
    # rejected TaskRelayNotSupported regardless of the kill-switch. Kill-switch
    # defaults True (activated per ADR-014 D5/D6); set relay_tasks_enabled: false
    # in config to restore the dark relay-only stance.
    from ...fastmcp_server.task_relay_wiring import (
        advertise_tasks_capability,
        enable_governed_task_relay,
    )

    relay_tasks_enabled = bool(full_config.get("relay_tasks_enabled", True))
    enable_governed_task_relay(mcp_server, relay_tasks_enabled=relay_tasks_enabled)
    advertise_tasks_capability(mcp_server, relay_tasks_enabled=relay_tasks_enabled)

    # Wire log buffers to mcp_servers after configuration populates the shared repository.
    init_log_buffers(runtime.repository.get_all())

    # Create background workers (not started)
    workers: list[WorkerLike] = create_background_workers(config=full_config)

    # Add config reload worker if enabled
    reload_config = full_config.get("config_reload", {})
    if reload_config.get("enabled", True):  # Enabled by default
        from ...gc import ConfigReloadWorker

        config_reload_worker = ConfigReloadWorker(
            config_path=config_path,
            command_bus=runtime.command_bus,
            interval_s=reload_config.get("interval_s", 5),
            use_watchdog=reload_config.get("use_watchdog", True),
        )
        # ConfigReloadWorker has .start() and .stop() compatible with BackgroundWorker
        workers.append(config_reload_worker)
        logger.info("config_reload_worker_created")

    # Initialize discovery (not started)
    discovery_orchestrator = None
    discovery_config = full_config.get("discovery", {})
    if discovery_config.get("enabled", False):
        discovery_orchestrator = create_discovery_orchestrator(full_config)

    # Create DiscoveryRegistry and register CQRS handlers
    discovery_registry = None
    if discovery_orchestrator is not None:
        from ...application.commands.discovery_handlers import register_discovery_handlers
        from ...application.discovery.discovery_registry import DiscoveryRegistry

        discovery_registry = DiscoveryRegistry(orchestrator=discovery_orchestrator)
        register_discovery_handlers(runtime.command_bus, discovery_registry)
        logger.info("discovery_registry_created")

    # Log ready state
    mcp_server_ids = runtime.repository.get_all_ids()
    group_ids = list(GROUPS.keys())
    logger.info(
        "bootstrap_complete",
        mcp_servers=mcp_server_ids,
        groups=group_ids,
        discovery_enabled=discovery_orchestrator is not None,
        auth_enabled=auth_components.enabled,
    )

    context = ApplicationContext(
        runtime=runtime,
        mcp_server=mcp_server,
        background_workers=workers,
        discovery_orchestrator=discovery_orchestrator,
        auth_components=auth_components,
        config=full_config,
        load_mcp_server_handler=load_handler,
        unload_mcp_server_handler=unload_handler,
        observability_adapter=observability_adapter,
        saga_state_store=saga_state_store,
        discovery_registry=discovery_registry,
        approval_service=components.approval_service,
    )

    # Update application context for tools to access
    ctx = get_context()
    ctx.groups = GROUPS  # Wire shared GROUPS dict so API reads/writes use same instance
    ctx.load_mcp_server_handler = load_handler
    ctx.unload_mcp_server_handler = unload_handler
    ctx.discovery_orchestrator = discovery_orchestrator
    ctx.discovery_registry = discovery_registry
    ctx.full_config = full_config  # Store for config round-trip serialization
    if components.approval_service is not None:
        ctx.approval_gate = components.approval_service

    return context


# Backward compatibility aliases with underscore prefix
_init_event_store = init_event_store
_init_event_handlers = init_event_handlers
_init_cqrs = init_cqrs
_init_saga = init_saga
_init_retry_config = init_retry_config
_init_truncation = init_truncation
_init_hot_loading = init_hot_loading
_init_observability = init_observability
_register_all_tools = register_all_tools
_create_background_workers = create_background_workers
_create_discovery_orchestrator = create_discovery_orchestrator

# Backward compatibility: auth shims for existing code and tests that
# import these names from bootstrap.__init__.
_auth_compat_exports = get_auth_compat_exports()
AuthComponents = _auth_compat_exports.AuthComponents
NullAuthComponents = _auth_compat_exports.NullAuthComponents
bootstrap_auth = _auth_compat_exports.bootstrap_auth
parse_auth_config = _auth_compat_exports.parse_auth_config
_auth_available = _auth_compat_exports.auth_available


# Re-export for backward compatibility
__all__ = [
    "ApplicationContext",
    "ServerComponents",
    "bootstrap",
    "load_components",
    "GC_WORKER_INTERVAL_SECONDS",
    "HEALTH_CHECK_INTERVAL_SECONDS",
    # Initialization functions (with and without underscore prefix)
    "init_cqrs",
    "init_auth_cqrs",
    "init_event_handlers",
    "init_event_store",
    "init_hot_loading",
    "init_log_buffers",
    "init_observability",
    "init_retry_config",
    "init_saga",
    "init_truncation",
    "shutdown_observability",
    "create_background_workers",
    "create_discovery_orchestrator",
    "register_all_tools",
    "_ensure_data_dir",
    "_init_cqrs",
    "_init_event_handlers",
    "_init_event_store",
    "_init_hot_loading",
    "_init_observability",
    "_init_retry_config",
    "_init_saga",
    "_init_truncation",
    "_create_background_workers",
    "_create_discovery_orchestrator",
    "_register_all_tools",
    "_auto_add_volumes",
    "_create_discovery_source",
    # Backward compatibility: auth shims
    "AuthComponents",
    "NullAuthComponents",
    "bootstrap_auth",
    "parse_auth_config",
    "_auth_available",
]
