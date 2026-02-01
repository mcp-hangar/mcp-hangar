"""Application Bootstrap - Composition Root.

This module is responsible for wiring up all dependencies and initializing
application components. It is the composition root of the application.

The bootstrap process:
1. Load configuration
2. Initialize runtime (event bus, command bus, query bus)
3. Initialize event store (for event sourcing)
4. Register event handlers
5. Register CQRS handlers
6. Initialize sagas
7. Load providers from config
8. Initialize discovery (if enabled)
9. Create MCP server with tools
10. Create background workers (DO NOT START)

Key principle: Bootstrap returns a fully configured but NOT running application.
Starting is handled by the lifecycle module.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ...application.commands.load_handlers import LoadProviderHandler, UnloadProviderHandler
from ...application.discovery import DiscoveryOrchestrator
from ...gc import BackgroundWorker
from ...logging_config import get_logger
from ..auth_bootstrap import AuthComponents, bootstrap_auth
from ..auth_config import parse_auth_config
from ..config import load_config, load_configuration
from ..context import get_context, init_context
from ..state import get_runtime, GROUPS, PROVIDERS
from .cqrs import init_cqrs, init_saga
from .discovery import _auto_add_volumes, _create_discovery_source, create_discovery_orchestrator
from .event_handlers import init_event_handlers
from .event_store import init_event_store
from .hot_loading import init_hot_loading
from .knowledge_base import init_knowledge_base
from .retry_config import init_retry_config
from .tools import register_all_tools
from .truncation import init_truncation
from .workers import create_background_workers, GC_WORKER_INTERVAL_SECONDS, HEALTH_CHECK_INTERVAL_SECONDS

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

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

    background_workers: list[BackgroundWorker] = field(default_factory=list)
    """Background workers (GC, health check) - not started."""

    discovery_orchestrator: DiscoveryOrchestrator | None = None
    """Discovery orchestrator if enabled - not started."""

    auth_components: AuthComponents | None = None
    """Authentication and authorization components."""

    config: dict[str, Any] = field(default_factory=dict)
    """Full configuration dictionary."""

    load_provider_handler: LoadProviderHandler | None = None
    """Handler for loading providers at runtime."""

    unload_provider_handler: UnloadProviderHandler | None = None
    """Handler for unloading providers at runtime."""

    @property
    def providers(self) -> dict[str, Any]:
        """Get providers dictionary for easy access."""
        return PROVIDERS

    def shutdown(self) -> None:
        """Graceful shutdown of all components.

        Stops background workers, discovery orchestrator, and cleans up resources.
        """
        logger.info("application_context_shutdown_start")

        # Stop background workers
        for worker in self.background_workers:
            try:
                worker.stop()
            except Exception as e:
                logger.warning(
                    "worker_stop_failed",
                    task=worker.task,
                    error=str(e),
                )

        # Stop discovery orchestrator
        if self.discovery_orchestrator:
            try:
                asyncio.run(self.discovery_orchestrator.stop())
            except Exception as e:
                logger.warning("discovery_orchestrator_stop_failed", error=str(e))

        # Stop all providers
        for provider_id, provider in PROVIDERS.items():
            try:
                provider.stop()
            except Exception as e:
                logger.warning(
                    "provider_stop_failed",
                    provider_id=provider_id,
                    error=str(e),
                )

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
    8. Load configuration and providers
    9. Initialize retry configuration
    10. Initialize knowledge base (if enabled)
    11. Create MCP server with tools
    12. Create background workers (DO NOT START)
    13. Initialize discovery (if enabled, DO NOT START)

    Args:
        config_path: Optional path to config.yaml
        config_dict: Optional configuration dictionary (takes precedence over config_path)

    Returns:
        Fully initialized ApplicationContext (components not started)
    """
    logger.info("bootstrap_start", config_path=config_path, has_config_dict=config_dict is not None)

    # Ensure data directory exists
    _ensure_data_dir()

    # Initialize runtime and context
    runtime = get_runtime()
    init_context(runtime)

    # Load configuration early (needed for event store config)
    if config_dict is not None:
        # Use provided config dict, merge with defaults
        full_config = load_configuration(None)
        full_config.update(config_dict)
        # Load providers from config_dict
        providers_config = config_dict.get("providers", {})
        if providers_config:
            load_config(providers_config)
    else:
        full_config = load_configuration(config_path)

    # Initialize event store for event sourcing
    init_event_store(runtime, full_config)

    # Initialize event handlers
    init_event_handlers(runtime)

    # Initialize CQRS
    init_cqrs(runtime)

    # Initialize saga
    init_saga()

    logger.info(
        "security_config_loaded",
        rate_limit_rps=runtime.rate_limit_config.requests_per_second,
        burst_size=runtime.rate_limit_config.burst_size,
    )

    # Initialize authentication and authorization
    auth_config = parse_auth_config(full_config.get("auth"))
    auth_components = bootstrap_auth(
        config=auth_config,
        event_publisher=lambda event: runtime.event_bus.publish(event),
    )

    # Initialize retry configuration
    init_retry_config(full_config)

    # Initialize knowledge base
    init_knowledge_base(full_config)

    # Initialize truncation system
    init_truncation(full_config)

    # Initialize hot-loading components
    load_handler, unload_handler = init_hot_loading(runtime, full_config)

    # Create MCP server and register tools
    mcp_server = FastMCP("mcp-registry")
    register_all_tools(mcp_server)

    # Create background workers (not started)
    workers = create_background_workers()

    # Initialize discovery (not started)
    discovery_orchestrator = None
    discovery_config = full_config.get("discovery", {})
    if discovery_config.get("enabled", False):
        discovery_orchestrator = create_discovery_orchestrator(full_config)

    # Log ready state
    provider_ids = list(PROVIDERS.keys())
    group_ids = list(GROUPS.keys())
    logger.info(
        "bootstrap_complete",
        providers=provider_ids,
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
        load_provider_handler=load_handler,
        unload_provider_handler=unload_handler,
    )

    # Update application context for tools to access
    ctx = get_context()
    ctx.load_provider_handler = load_handler
    ctx.unload_provider_handler = unload_handler

    return context


# Backward compatibility aliases with underscore prefix
_init_event_store = init_event_store
_init_event_handlers = init_event_handlers
_init_cqrs = init_cqrs
_init_saga = init_saga
_init_retry_config = init_retry_config
_init_knowledge_base = init_knowledge_base
_init_truncation = init_truncation
_init_hot_loading = init_hot_loading
_register_all_tools = register_all_tools
_create_background_workers = create_background_workers
_create_discovery_orchestrator = create_discovery_orchestrator

# Re-export for backward compatibility
__all__ = [
    "ApplicationContext",
    "bootstrap",
    "GC_WORKER_INTERVAL_SECONDS",
    "HEALTH_CHECK_INTERVAL_SECONDS",
    # Initialization functions (with and without underscore prefix)
    "init_cqrs",
    "init_event_handlers",
    "init_event_store",
    "init_hot_loading",
    "init_knowledge_base",
    "init_retry_config",
    "init_saga",
    "init_truncation",
    "create_background_workers",
    "create_discovery_orchestrator",
    "register_all_tools",
    "_ensure_data_dir",
    "_init_cqrs",
    "_init_event_handlers",
    "_init_event_store",
    "_init_hot_loading",
    "_init_knowledge_base",
    "_init_retry_config",
    "_init_saga",
    "_init_truncation",
    "_create_background_workers",
    "_create_discovery_orchestrator",
    "_register_all_tools",
    "_auto_add_volumes",
    "_create_discovery_source",
]
