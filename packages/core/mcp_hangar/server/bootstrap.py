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

from ..application.commands.load_handlers import LoadProviderHandler, UnloadProviderHandler
from ..application.commands import register_all_handlers as register_command_handlers
from ..application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from ..application.event_handlers import AlertEventHandler, AuditEventHandler, LoggingEventHandler, MetricsEventHandler
from ..application.queries import register_all_handlers as register_query_handlers
from ..application.sagas import GroupRebalanceSaga
from ..application.services.package_resolver import PackageResolver, RuntimeAvailability
from ..application.services.secrets_resolver import SecretsResolver
from ..domain.contracts.event_store import NullEventStore
from ..domain.discovery import DiscoveryMode
from ..domain.model import Provider
from ..gc import BackgroundWorker
from ..infrastructure.persistence import SQLiteEventStore
from ..infrastructure.saga_manager import get_saga_manager
from ..logging_config import get_logger
from ..retry import get_retry_store
from .auth_bootstrap import AuthComponents, bootstrap_auth
from .auth_config import parse_auth_config
from .config import load_config, load_configuration
from .context import get_context, init_context
from .state import (
    get_runtime,
    get_runtime_providers,
    GROUPS,
    PROVIDER_REPOSITORY,
    PROVIDERS,
    set_discovery_orchestrator,
    set_group_rebalance_saga,
)
from .tools import (
    register_batch_tools,
    register_discovery_tools,
    register_group_tools,
    register_hangar_tools,
    register_health_tools,
    register_load_tools,
    register_provider_tools,
)

if TYPE_CHECKING:
    from ..bootstrap.runtime import Runtime

logger = get_logger(__name__)

# =============================================================================
# Constants
# =============================================================================

GC_WORKER_INTERVAL_SECONDS = 30
"""Interval for garbage collection worker."""

HEALTH_CHECK_INTERVAL_SECONDS = 60
"""Interval for health check worker."""


# =============================================================================
# Application Context
# =============================================================================


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


# =============================================================================
# Bootstrap Functions
# =============================================================================


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
    _init_event_store(runtime, full_config)

    # Initialize event handlers
    _init_event_handlers(runtime)

    # Initialize CQRS
    _init_cqrs(runtime)

    # Initialize saga
    _init_saga()

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
    _init_retry_config(full_config)

    # Initialize knowledge base
    _init_knowledge_base(full_config)

    # Initialize hot-loading components
    load_handler, unload_handler = _init_hot_loading(runtime, full_config)

    # Create MCP server and register tools
    mcp_server = FastMCP("mcp-registry")
    _register_all_tools(mcp_server)

    # Create background workers (not started)
    workers = _create_background_workers()

    # Initialize discovery (not started)
    discovery_orchestrator = None
    discovery_config = full_config.get("discovery", {})
    if discovery_config.get("enabled", False):
        discovery_orchestrator = _create_discovery_orchestrator(full_config)

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


# =============================================================================
# Internal Initialization Functions
# =============================================================================


def _ensure_data_dir() -> None:
    """Ensure data directory exists for persistent storage."""
    data_dir = Path("./data")
    if not data_dir.exists():
        try:
            data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
            logger.info("data_directory_created", path=str(data_dir.absolute()))
        except OSError as e:
            logger.warning("data_directory_creation_failed", error=str(e))


def _init_event_store(runtime: "Runtime", config: dict[str, Any]) -> None:
    """Initialize event store for event sourcing.

    Configures the event store based on config.yaml settings.
    Defaults to SQLite if not specified.

    Config example:
        event_store:
            enabled: true
            driver: sqlite  # or "memory"
            path: data/events.db

    Args:
        runtime: Runtime instance with event bus.
        config: Full configuration dictionary.
    """
    event_store_config = config.get("event_store", {})
    enabled = event_store_config.get("enabled", True)

    if not enabled:
        logger.info("event_store_disabled")
        runtime.event_bus.set_event_store(NullEventStore())
        return

    driver = event_store_config.get("driver", "sqlite")

    if driver == "memory":
        from ..infrastructure.persistence import InMemoryEventStore

        event_store = InMemoryEventStore()
        logger.info("event_store_initialized", driver="memory")
    elif driver == "sqlite":
        db_path = event_store_config.get("path", "data/events.db")
        # Ensure directory exists - fallback to in-memory if read-only
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            event_store = SQLiteEventStore(db_path)
            logger.info("event_store_initialized", driver="sqlite", path=db_path)
        except OSError as e:
            # Read-only filesystem or permission denied - use in-memory store
            logger.warning(
                "event_store_sqlite_fallback_to_memory",
                error=str(e),
                path=db_path,
            )
            from ..infrastructure.persistence import InMemoryEventStore

            event_store = InMemoryEventStore()
            logger.info("event_store_initialized", driver="memory", reason="sqlite_unavailable")
    else:
        logger.warning(
            "unknown_event_store_driver",
            driver=driver,
            fallback="memory",
        )
        from ..infrastructure.persistence import InMemoryEventStore

        event_store = InMemoryEventStore()

    runtime.event_bus.set_event_store(event_store)


def _init_event_handlers(runtime: "Runtime") -> None:
    """Register all event handlers.

    Args:
        runtime: Runtime instance with event bus.
    """
    logging_handler = LoggingEventHandler()
    runtime.event_bus.subscribe_to_all(logging_handler.handle)

    metrics_handler = MetricsEventHandler()
    runtime.event_bus.subscribe_to_all(metrics_handler.handle)

    alert_handler = AlertEventHandler()
    runtime.event_bus.subscribe_to_all(alert_handler.handle)

    audit_handler = AuditEventHandler()
    runtime.event_bus.subscribe_to_all(audit_handler.handle)

    runtime.event_bus.subscribe_to_all(runtime.security_handler.handle)

    # Knowledge base handler (PostgreSQL persistence)
    from ..application.event_handlers.knowledge_base_handler import KnowledgeBaseEventHandler

    kb_handler = KnowledgeBaseEventHandler()
    runtime.event_bus.subscribe_to_all(kb_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=["logging", "metrics", "alert", "audit", "security", "knowledge_base"],
    )


def _init_cqrs(runtime: "Runtime") -> None:
    """Register command and query handlers.

    Args:
        runtime: Runtime instance with command and query buses.
    """
    register_command_handlers(runtime.command_bus, PROVIDER_REPOSITORY, runtime.event_bus)
    register_query_handlers(runtime.query_bus, PROVIDER_REPOSITORY)
    logger.info("cqrs_handlers_registered")


def _init_saga() -> None:
    """Initialize group rebalance saga."""
    ctx = get_context()
    saga = GroupRebalanceSaga(groups=ctx.groups)
    ctx.group_rebalance_saga = saga
    set_group_rebalance_saga(saga)  # For backward compatibility
    saga_manager = get_saga_manager()
    saga_manager.register_event_saga(saga)
    logger.info("group_rebalance_saga_registered")


def _init_retry_config(config: dict[str, Any]) -> None:
    """Initialize retry configuration from config.yaml.

    Args:
        config: Full configuration dictionary.
    """
    retry_store = get_retry_store()
    retry_store.load_from_config(config)
    logger.info("retry_config_loaded")


def _init_knowledge_base(config: dict[str, Any]) -> None:
    """Initialize knowledge base from config.yaml.

    Supports multiple drivers (postgres, sqlite, memory) with auto-detection.

    Args:
        config: Full configuration dictionary.
    """
    from ..infrastructure.knowledge_base import init_knowledge_base, KnowledgeBaseConfig

    kb_config_dict = config.get("knowledge_base", {})
    kb_config = KnowledgeBaseConfig.from_dict(kb_config_dict)

    if not kb_config.enabled:
        logger.info("knowledge_base_disabled")
        return

    # Initialize asynchronously
    async def init():
        kb = await init_knowledge_base(kb_config)
        if kb:
            # Verify health
            healthy = await kb.is_healthy()
            if healthy:
                logger.info("knowledge_base_health_ok")
            else:
                logger.warning("knowledge_base_health_check_failed")

    asyncio.run(init())


def _init_hot_loading(
    runtime: "Runtime",
    config: dict[str, Any],
) -> tuple[LoadProviderHandler | None, UnloadProviderHandler | None]:
    """Initialize hot-loading components for runtime provider injection.

    Args:
        runtime: Runtime instance.
        config: Full configuration dictionary.

    Returns:
        Tuple of (LoadProviderHandler, UnloadProviderHandler) or (None, None) if disabled.
    """
    hot_loading_config = config.get("hot_loading", {})
    if not hot_loading_config.get("enabled", True):
        logger.info("hot_loading_disabled")
        return None, None

    try:
        from ..infrastructure.installers import BinaryInstaller, NpmInstaller, OciInstaller, PyPIInstaller
        from ..infrastructure.registry import RegistryCache, RegistryClient

        # Read config values
        registry_config = hot_loading_config.get("registry", {})
        cache_config = hot_loading_config.get("cache", {})

        # Create cache with config
        cache = RegistryCache(
            ttl_seconds=cache_config.get("ttl_s", 3600),
            max_entries=cache_config.get("max_entries", 1000),
        )

        # Create registry client with config
        registry_client = RegistryClient(
            base_url=registry_config.get("base_url", RegistryClient.DEFAULT_BASE_URL),
            timeout=registry_config.get("timeout_s", RegistryClient.DEFAULT_TIMEOUT),
            max_retries=registry_config.get("max_retries", RegistryClient.DEFAULT_MAX_RETRIES),
            cache=cache,
        )

        npm_installer = NpmInstaller()
        pypi_installer = PyPIInstaller()
        oci_installer = OciInstaller()
        binary_installer = BinaryInstaller()

        installers = [pypi_installer, npm_installer, oci_installer, binary_installer]

        availability = RuntimeAvailability(
            pypi=True,
            npm=True,
            oci=True,
            binary=True,
        )
        package_resolver = PackageResolver(availability)

        secrets_resolver = SecretsResolver()

        runtime_store = get_runtime_providers()

        def provider_factory(**kwargs):
            return Provider(**kwargs)

        load_handler = LoadProviderHandler(
            registry_client=registry_client,
            package_resolver=package_resolver,
            secrets_resolver=secrets_resolver,
            installers=installers,
            runtime_store=runtime_store,
            event_bus=runtime.event_bus,
            provider_factory=provider_factory,
            provider_repository=PROVIDER_REPOSITORY,
        )

        unload_handler = UnloadProviderHandler(
            runtime_store=runtime_store,
            event_bus=runtime.event_bus,
        )

        logger.info("hot_loading_initialized")
        return load_handler, unload_handler

    except ImportError as e:
        logger.warning(
            "hot_loading_unavailable",
            error=str(e),
            suggestion="Install httpx for registry client support",
        )
        return None, None


def _register_all_tools(mcp_server: FastMCP) -> None:
    """Register all MCP tools on the server.

    Args:
        mcp_server: FastMCP server instance.
    """
    register_hangar_tools(mcp_server)
    register_load_tools(mcp_server)
    register_provider_tools(mcp_server)
    register_health_tools(mcp_server)
    register_discovery_tools(mcp_server)
    register_group_tools(mcp_server)
    register_batch_tools(mcp_server)
    logger.info("mcp_tools_registered")


def _create_background_workers() -> list[BackgroundWorker]:
    """Create (but don't start) background workers.

    Returns:
        List of BackgroundWorker instances (not started).
    """
    gc_worker = BackgroundWorker(
        PROVIDERS,
        interval_s=GC_WORKER_INTERVAL_SECONDS,
        task="gc",
    )

    health_worker = BackgroundWorker(
        PROVIDERS,
        interval_s=HEALTH_CHECK_INTERVAL_SECONDS,
        task="health_check",
    )

    logger.info("background_workers_created", workers=["gc", "health_check"])
    return [gc_worker, health_worker]


# =============================================================================
# Discovery Initialization
# =============================================================================


def _create_discovery_orchestrator(config: dict[str, Any]) -> DiscoveryOrchestrator | None:
    """Create discovery orchestrator from config (not started).

    Args:
        config: Full configuration dictionary.

    Returns:
        DiscoveryOrchestrator instance or None if disabled.
    """
    discovery_config = config.get("discovery", {})
    if not discovery_config.get("enabled", False):
        logger.info("discovery_disabled")
        return None

    logger.info("discovery_initializing")

    static_providers = set(PROVIDERS.keys())
    orchestrator_config = DiscoveryConfig.from_dict(discovery_config)
    orchestrator = DiscoveryOrchestrator(
        config=orchestrator_config,
        static_providers=static_providers,
    )

    sources_config = discovery_config.get("sources", [])
    for source_config in sources_config:
        source_type = source_config.get("type")
        try:
            source = _create_discovery_source(source_type, source_config)
            if source:
                orchestrator.add_source(source)
        except ImportError as e:
            logger.warning(
                "discovery_source_unavailable",
                source_type=source_type,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                "discovery_source_error",
                source_type=source_type,
                error=str(e),
            )

    # Set up registration callbacks
    orchestrator.on_register = _on_provider_register
    orchestrator.on_deregister = _on_provider_deregister

    set_discovery_orchestrator(orchestrator)
    return orchestrator


def _create_discovery_source(source_type: str, config: dict[str, Any]):
    """Create a discovery source based on type and config.

    Args:
        source_type: Type of discovery source (kubernetes, docker, filesystem, entrypoint).
        config: Source configuration dictionary.

    Returns:
        Discovery source instance or None.
    """
    mode_str = config.get("mode", "additive")
    mode = DiscoveryMode.AUTHORITATIVE if mode_str == "authoritative" else DiscoveryMode.ADDITIVE

    if source_type == "kubernetes":
        from ..infrastructure.discovery import KubernetesDiscoverySource

        return KubernetesDiscoverySource(
            mode=mode,
            namespaces=config.get("namespaces"),
            label_selector=config.get("label_selector"),
            in_cluster=config.get("in_cluster", True),
        )
    elif source_type == "docker":
        from ..infrastructure.discovery import DockerDiscoverySource

        return DockerDiscoverySource(
            mode=mode,
            socket_path=config.get("socket_path"),
        )
    elif source_type == "filesystem":
        from ..infrastructure.discovery import FilesystemDiscoverySource

        path = config.get("path", "/etc/mcp-hangar/providers.d/")
        resolved_path = Path(path)
        if not resolved_path.is_absolute():
            resolved_path = Path.cwd() / resolved_path
        return FilesystemDiscoverySource(
            mode=mode,
            path=str(resolved_path),
            pattern=config.get("pattern", "*.yaml"),
            watch=config.get("watch", True),
        )
    elif source_type == "entrypoint":
        from ..infrastructure.discovery import EntrypointDiscoverySource

        return EntrypointDiscoverySource(
            mode=mode,
            group=config.get("group", "mcp.providers"),
        )
    else:
        logger.warning("discovery_unknown_source_type", source_type=source_type)
        return None


async def _on_provider_register(provider) -> bool:
    """Callback when discovery wants to register a provider.

    Args:
        provider: Discovered provider information.

    Returns:
        True if registration succeeded, False otherwise.
    """
    try:
        conn_info = provider.connection_info
        mode = provider.mode

        if mode == "container":
            provider_mode = "docker"
        elif mode in ("http", "sse"):
            provider_mode = "remote"
        elif mode in ("subprocess", "docker", "remote"):
            provider_mode = mode
        else:
            logger.warning(
                "unknown_provider_mode_skipping",
                mode=mode,
                provider_name=provider.name,
            )
            return False

        provider_kwargs = {
            "provider_id": provider.name,
            "mode": provider_mode,
            "description": f"Discovered from {provider.source_type}",
        }

        if provider_mode == "docker":
            image = conn_info.get("image")
            if not image:
                logger.warning(
                    "container_provider_no_image_skipping",
                    provider_name=provider.name,
                )
                return False
            provider_kwargs["image"] = image
            provider_kwargs["read_only"] = conn_info.get("read_only", False)
            if conn_info.get("command"):
                provider_kwargs["command"] = conn_info.get("command")

            volumes = conn_info.get("volumes", [])
            if not volumes:
                volumes = _auto_add_volumes(provider.name)
            if volumes:
                provider_kwargs["volumes"] = volumes

        elif provider_mode == "remote":
            host = conn_info.get("host")
            port = conn_info.get("port")
            endpoint = conn_info.get("endpoint")
            if endpoint:
                provider_kwargs["endpoint"] = endpoint
            elif host and port:
                provider_kwargs["endpoint"] = f"http://{host}:{port}"
            else:
                logger.warning(
                    "http_provider_no_endpoint_skipping",
                    provider_name=provider.name,
                )
                return False
        else:
            command = conn_info.get("command")
            if not command:
                logger.warning(
                    "subprocess_provider_no_command_skipping",
                    provider_name=provider.name,
                )
                return False
            provider_kwargs["command"] = command

        provider_kwargs["env"] = conn_info.get("env", {})

        new_provider = Provider(**provider_kwargs)
        PROVIDERS[provider.name] = new_provider
        logger.info(
            "discovery_registered_provider",
            provider_name=provider.name,
            mode=provider_mode,
        )
        return True
    except Exception as e:
        logger.error(
            "discovery_registration_failed",
            provider_name=provider.name,
            error=str(e),
        )
        return False


async def _on_provider_deregister(name: str, reason: str):
    """Callback when discovery wants to deregister a provider.

    Args:
        name: Provider name to deregister.
        reason: Reason for deregistration.
    """
    try:
        if name in PROVIDERS:
            provider = PROVIDERS.get(name)
            if provider:
                provider.stop()
            del PROVIDERS._repo._providers[name]
            logger.info(
                "discovery_deregistered_provider",
                provider_name=name,
                reason=reason,
            )
    except Exception as e:
        logger.error(
            "discovery_deregistration_failed",
            provider_name=name,
            error=str(e),
        )


def _auto_add_volumes(provider_name: str) -> list:
    """Auto-add persistent volumes for known stateful providers.

    Args:
        provider_name: Provider name to check for known volume patterns.

    Returns:
        List of volume mount strings.
    """
    volumes = []
    provider_name_lower = provider_name.lower()
    data_base = Path("./data").absolute()

    try:
        if "memory" in provider_name_lower:
            memory_dir = data_base / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_dir.chmod(0o777)
            volumes.append(f"{memory_dir}:/app/data:rw")
            logger.info(
                "auto_added_memory_volume",
                provider_name=provider_name,
                volume=f"{memory_dir}:/app/data",
            )

        elif "filesystem" in provider_name_lower:
            fs_dir = data_base / "filesystem"
            fs_dir.mkdir(parents=True, exist_ok=True)
            fs_dir.chmod(0o777)
            volumes.append(f"{fs_dir}:/data:rw")
            logger.info(
                "auto_added_filesystem_volume",
                provider_name=provider_name,
                volume=f"{fs_dir}:/data",
            )
    except OSError as e:
        logger.warning(
            "auto_volume_creation_failed",
            provider_name=provider_name,
            error=str(e),
        )

    return volumes


__all__ = [
    "ApplicationContext",
    "bootstrap",
    "GC_WORKER_INTERVAL_SECONDS",
    "HEALTH_CHECK_INTERVAL_SECONDS",
    # Internal functions exported for backward compatibility / testing
    "_auto_add_volumes",
    "_create_background_workers",
    "_create_discovery_source",
    "_ensure_data_dir",
    "_init_cqrs",
    "_init_event_handlers",
    "_init_knowledge_base",
    "_init_retry_config",
    "_init_saga",
    "_register_all_tools",
]
