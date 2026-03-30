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
8. Load providers from config
9. Initialize discovery (if enabled)
10. Create MCP server with tools
11. Create background workers (DO NOT START)

Key principle: Bootstrap returns a fully configured but NOT running application.
Starting is handled by the lifecycle module.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ...application.commands.load_handlers import LoadProviderHandler, UnloadProviderHandler
from ...application.discovery import DiscoveryOrchestrator
from ...application.ports.observability import ObservabilityPort
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...gc import BackgroundWorker
from ...logging_config import get_logger
from ..config import load_config, load_configuration
from ..context import get_context, init_context
from ..state import get_runtime, GROUPS, PROVIDERS

from ...domain.value_objects.license import LicenseTier
from .enterprise import EnterpriseComponents, load_enterprise_modules

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

    background_workers: list[BackgroundWorker] = field(default_factory=list)
    """Background workers (GC, health check) - not started."""

    discovery_orchestrator: DiscoveryOrchestrator | None = None
    """Discovery orchestrator if enabled - not started."""

    auth_components: Any = None
    """Authentication and authorization components."""

    license_tier: LicenseTier = LicenseTier.COMMUNITY
    """License tier governing enterprise module availability."""

    config: dict[str, Any] = field(default_factory=dict)
    """Full configuration dictionary."""

    load_provider_handler: LoadProviderHandler | None = None
    """Handler for loading providers at runtime."""

    unload_provider_handler: UnloadProviderHandler | None = None
    """Handler for unloading providers at runtime."""

    observability_adapter: ObservabilityPort | None = None
    """Observability adapter for tracing (Langfuse, etc.)."""

    saga_state_store: SagaStateStore | NullSagaStateStore | None = None
    """Saga state store for persisting saga state and circuit breakers."""

    discovery_registry: "DiscoveryRegistry | None" = None
    """Discovery source registry (wraps DiscoveryOrchestrator)."""

    @property
    def providers(self) -> dict[str, Any]:
        """Get providers dictionary for easy access."""
        return PROVIDERS

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

        # Stop discovery orchestrator
        if self.discovery_orchestrator:
            try:
                asyncio.run(self.discovery_orchestrator.stop())
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if discovery stop fails
                logger.warning("discovery_orchestrator_stop_failed", error=str(e))

        # Save circuit breaker state for provider groups before stopping
        if self.saga_state_store is not None:
            try:
                save_group_circuit_breakers(self.saga_state_store, GROUPS)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if CB save fails
                logger.warning("circuit_breaker_save_failed", error=str(e))

        # Stop all providers
        for provider_id, provider in PROVIDERS.items():
            try:
                provider.stop()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if individual provider stop fails
                logger.warning(
                    "provider_stop_failed",
                    provider_id=provider_id,
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
    8. Load configuration and providers
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

    logger.info(
        "security_config_loaded",
        rate_limit_rps=runtime.rate_limit_config.requests_per_second,
        burst_size=runtime.rate_limit_config.burst_size,
    )

    # Add rate limit middleware to command bus
    from ...infrastructure.command_bus import RateLimitMiddleware

    rate_limit_mw = RateLimitMiddleware(rate_limiter=runtime.rate_limiter)
    runtime.command_bus.add_middleware(rate_limit_mw)

    # Validate license key and determine tier
    raw_license_key = os.environ.get("HANGAR_LICENSE_KEY")
    license_tier = LicenseTier.COMMUNITY
    try:
        from enterprise.auth.license import LicenseValidator

        result = LicenseValidator().validate(raw_license_key)
        license_tier = result.tier
        if result.grace_period:
            logger.warning("license_grace_period", tier=license_tier.value, org=result.org)
    except ImportError:
        logger.debug("license_validator_not_available", reason="enterprise_not_installed")

    logger.info("license_tier", tier=license_tier.value)

    # Load enterprise modules based on license tier
    enterprise = load_enterprise_modules(
        tier=license_tier,
        config=full_config,
        event_bus=runtime.event_bus,
        event_publisher=lambda event: runtime.event_bus.publish(event),
    )

    # Wire enterprise components with null fallbacks
    auth_components = enterprise.auth_components
    if auth_components is None:

        class _StubAuthComponents:
            """Stub auth components used when enterprise auth is not loaded."""

            enabled = False
            api_key_store = None
            role_store = None
            tap_store = None
            authn_middleware = None
            authz_middleware = None

        auth_components = _StubAuthComponents()

    init_auth_cqrs(runtime, auth_components)

    # Initialize retry configuration
    init_retry_config(full_config)

    # Initialize truncation system
    init_truncation(full_config)

    # Initialize hot-loading components
    load_handler, unload_handler = init_hot_loading(runtime, full_config)

    # Create MCP server and register tools
    mcp_server = FastMCP("mcp-registry")
    register_all_tools(mcp_server)

    # Wire log buffers to providers (must run after load_config populates PROVIDERS)
    init_log_buffers(PROVIDERS)

    # Create background workers (not started)
    workers = create_background_workers(config=full_config)

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
        workers.append(config_reload_worker)  # type: ignore[arg-type]
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
        license_tier=license_tier,
        config=full_config,
        load_provider_handler=load_handler,
        unload_provider_handler=unload_handler,
        observability_adapter=observability_adapter,
        saga_state_store=saga_state_store,
        discovery_registry=discovery_registry,
    )

    # Update application context for tools to access
    ctx = get_context()
    ctx.groups = GROUPS  # Wire shared GROUPS dict so API reads/writes use same instance
    ctx.load_provider_handler = load_handler
    ctx.unload_provider_handler = unload_handler
    ctx.discovery_registry = discovery_registry
    ctx.full_config = full_config  # Store for config round-trip serialization

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

# Backward compatibility: enterprise auth shims for existing code and tests that
# import these names from bootstrap.__init__.  The real implementations now live
# in bootstrap/enterprise.py (MIT) and enterprise/auth/* (BSL).
try:
    from enterprise.auth.bootstrap import AuthComponents, NullAuthComponents, bootstrap_auth
    from enterprise.auth.config import parse_auth_config

    _enterprise_auth_available = True
except ImportError:
    _enterprise_auth_available = False

    class AuthComponents:  # type: ignore[no-redef]
        """Stub AuthComponents used when enterprise is not installed."""

        enabled: bool = False
        api_key_store = None
        role_store = None
        tap_store = None

    class NullAuthComponents(AuthComponents):  # type: ignore[no-redef]
        """Null/noop auth implementation used when enterprise is not installed."""

        enabled: bool = False

    def bootstrap_auth(config=None, **kwargs) -> AuthComponents:  # type: ignore[misc]
        """Return noop auth components when enterprise is not installed."""
        return NullAuthComponents()

    def parse_auth_config(raw: dict | None = None):  # type: ignore[misc]
        """Return empty config when enterprise is not installed."""
        return None


# Re-export for backward compatibility
__all__ = [
    "ApplicationContext",
    "EnterpriseComponents",
    "bootstrap",
    "load_enterprise_modules",
    "LicenseTier",
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
    # Backward compatibility: enterprise auth shims
    "AuthComponents",
    "NullAuthComponents",
    "bootstrap_auth",
    "parse_auth_config",
    "_enterprise_auth_available",
]
