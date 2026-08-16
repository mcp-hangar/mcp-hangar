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
import socket
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast, TYPE_CHECKING

from mcp_hangar import __version__
from mcp_hangar._sdk_compat import FastMCP, new_mcp_server

from ...application.commands.load_handlers import LoadMcpServerHandler, UnloadMcpServerHandler
from ...application.discovery import DiscoveryOrchestrator
from ...application.ports.observability import ObservabilityPort
from ...domain.events import set_instance_id
from ...protocol import HANGAR_SERVER_NAME
from ...fastmcp_server.flat_tool_projection import maybe_register_flat_tool_handlers
from ...fastmcp_server.governance_extensions import advertise_governance_extensions
from ...fastmcp_server.modern_surface import register_modern_surface
from ...fastmcp_server.served_capabilities import withdraw_unserved_capabilities
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...gc import BackgroundWorker
from ...logging_config import get_logger
from ..config import _interpolate_env_vars, load_config, load_configuration
from ..context import get_context, init_context
from ..state import get_runtime, GROUPS

from .components import ServerComponents, get_auth_compat_exports, load_components

from .coordination import init_event_tailer, init_lease_keeper
from .cqrs import init_cqrs, init_auth_cqrs, init_saga, save_group_circuit_breakers
from .discovery import _auto_add_volumes, create_discovery_orchestrator
from .event_handlers import init_event_handlers
from .event_store import init_event_store, recover_undelivered_events
from .hot_loading import init_hot_loading
from .logs import init_log_buffers
from .observability import init_metrics_publisher, init_observability, shutdown_observability
from .reachability import (
    check_subsystem_reachability,
    collect_subsystem_requirements,
    enforce_subsystem_reachability,
    SubsystemRequirement,
)
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


def build_serving_mcp_server() -> FastMCP:
    """Build the MCP server the CLI serves: control-plane tools + modern surface.

    Extracted from :func:`bootstrap` so the protocol surface the shipped
    ``mcp-hangar serve`` exposes can be constructed — and probed — without
    standing up the whole application (bootstrap registers process-global command
    handlers, so tests cannot call it). The absence of that seam is why #560 went
    unnoticed: the modern surface was unit-tested through ``MCPServerFactory``,
    which has no production call site, while the served server quietly lacked it.

    ``name``/``version`` are the INBOUND ``serverInfo`` reported to our own
    clients, and are shared with the factory path so ``initialize`` and
    ``server/discover`` agree on one identity (#560). ``version`` must be passed
    explicitly: left unset the SDK reports ITS OWN version as the server's, so
    ``initialize`` advertised the mcp SDK's version while ``server/discover``
    reported Hangar's.

    Does NOT wire the ADR-014 task relay: that publishes onto the
    ApplicationContext and so belongs to :func:`bootstrap`, after the context
    exists.
    """
    mcp_server = new_mcp_server(HANGAR_SERVER_NAME, version=__version__)
    register_all_tools(mcp_server)
    # Topology decides the tool surface: front_door swaps the hangar_* meta-API
    # for flat backend names. Gate must run here, not only in the unused factory,
    # or the configured mode silently does nothing on the shipped path (#596).
    maybe_register_flat_tool_handlers(mcp_server)
    # SEP-2133 governance descriptors, advertised on both the handshake and the
    # stateless discovery surface (#595).
    advertise_governance_extensions(mcp_server)
    # SEP-2575 `server/discover`. Without this the shipped `serve --http` surface
    # 404s the modern/stateless discovery entrypoint the 2.x line depends on.
    register_modern_surface(mcp_server)
    # Last, after every registration above: the SDK derives `prompts` and
    # `resources` from handlers it registers unconditionally, so both were
    # advertised and neither served (#888). Runs here as well as in the factory
    # -- wiring only the factory is dead on the shipped path (#596).
    withdraw_unserved_capabilities(mcp_server)
    return mcp_server


def _init_instance_identity() -> str:
    """Mint this process's instance identity and log it.

    `HANGAR_INSTANCE_LABEL` is a *label*: it prefixes the identity so an
    operator can recognise which pod wrote a row, and the uniqueness comes from
    the minted suffix instead. Under Kubernetes, set it from the downward API
    (`metadata.name`); the hostname is that same pod name, which is why it is
    the fallback.

    Logged at startup because it is the only place the value is visible before
    it starts appearing on events.
    """
    label = os.environ.get("HANGAR_INSTANCE_LABEL") or socket.gethostname()
    instance_id = set_instance_id(label)
    logger.info("instance_identity_minted", instance_id=instance_id, label=label)
    return instance_id


def _register_configured_sources(registry: Any, full_config: dict[str, Any]) -> None:
    """Give the sources the orchestrator holds an identity in the registry.

    There were two registries and a source only ever reached one of them. A
    source declared in configuration went to the orchestrator, which runs it;
    the UUID-keyed registry was created empty and only the REST API ever wrote
    to it. So `POST /api/discovery/sources/<id>/scan` answered 404 for every id
    an operator could obtain -- and `GET /api/discovery/sources`, which reads
    the orchestrator, returned no `id` to try in the first place.

    The membership comes from the orchestrator rather than from a second read of
    `config.yaml`, because the two do not agree and each disagreement is the
    original defect wearing a different hat. A source whose `mode` is misspelt
    is built anyway -- the builder resolves anything that is not
    `authoritative` to additive -- so it appears in the listing with an id,
    while `DiscoveryMode(...)` here refused it and the scan answered 404. A
    source that failed to build is absent from the listing, while a config-only
    reading registered it regardless and the scan answered 200 for something
    that does not exist. One list, one answer.

    Configuration is still where the spec's `config` payload comes from: it is
    the source's own settings, which the orchestrator hands to the source and
    does not keep.

    The id is derived from the source type rather than generated, because the
    orchestrator keys its sources by type and a random id would change on every
    restart. Registering here does not start anything: the orchestrator already
    owns the running source, and this only makes it addressable.
    """
    from ...domain.value_objects.discovery import DiscoverySourceSpec, config_source_id

    declared = {
        str(source_config["type"]): source_config
        for source_config in (full_config.get("discovery") or {}).get("sources", []) or []
        if source_config.get("type")
    }

    for source in registry.orchestrator.get_sources():
        source_type = str(source.source_type)
        source_config = declared.get(source_type, {})
        registry.register_source(
            DiscoverySourceSpec(
                source_id=config_source_id(source_type),
                source_type=source_type,
                mode=source.mode,
                enabled=True,
                config={k: v for k, v in source_config.items() if k not in ("type", "mode")},
            )
        )


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

    # First, before anything can publish: every event constructed after this
    # point carries the identity, and one constructed before it would carry a
    # different one -- two producers in one process.
    _init_instance_identity()

    # Before `load_config` below, which is where McpServer instances are
    # constructed and where they read the default publisher.
    init_metrics_publisher()

    # Ensure data directory exists
    _ensure_data_dir()

    # Load configuration early (needed for runtime rate-limit and event store config)
    # Read the configuration, but do not build the servers it declares yet.
    # Building one reaches for the runtime singleton, and the runtime takes the
    # storage backend at construction because it is frozen afterwards -- so
    # loading servers before the backend is selected leaves the gateway with the
    # in-memory config repository for the rest of its life, and every durable
    # half of the fleet then quietly does nothing.
    if config_dict is not None:
        # Use provided config dict, merge with defaults.
        #
        # Interpolate `${VAR}` here, once, exactly as the file path does inside
        # `load_config_from_file`. The programmatic entry point never touches
        # that file loader -- it hands the dict straight through -- so without
        # this pass a programmatic `auth: {token: "${API_TOKEN}"}` reached the
        # upstream as the literal fourteen characters (a 401), and a missing
        # variable no longer failed the boot closed. Applied to the caller's
        # dict alone, before the merge, so the defaults (already interpolated by
        # `load_configuration`) are not passed through a second time.
        full_config = load_configuration(None, load_servers=False)
        full_config.update(_interpolate_env_vars(config_dict))
    else:
        full_config = load_configuration(config_path, load_servers=False)

    # Initialize runtime and context. The rate_limit section (config > env > default)
    # is applied when the runtime singleton is first constructed.
    # The storage decision comes first, because `Runtime` is frozen once built
    # and its config/audit repositories are part of it. Selecting afterwards
    # would mean either mutating a frozen object or leaving those two on a
    # different backend than everything else.
    from .composition import set_persistence_backend
    from .persistence import (
        refuse_a_cluster_that_cannot_coordinate,
        refuse_local_modes_in_a_declared_cluster,
        restore_persisted_fleet,
        select_backend,
    )

    # Before the backend, because it reads configuration only: a cluster that
    # declares a child-process server is wrong whether or not its database is
    # reachable, and it should not be told about the database first.
    refuse_local_modes_in_a_declared_cluster(full_config)

    # Same reason, same place: pins addressed to a tenant no caller can carry
    # are wrong before any of them is parsed, and the operator should hear it
    # from the file rather than from an audit (#902).
    from .pinning import refuse_pins_that_no_caller_can_match

    refuse_pins_that_no_caller_can_match(full_config)

    _backend = select_backend(full_config)
    set_persistence_backend(_backend)
    refuse_a_cluster_that_cannot_coordinate(full_config)

    runtime = get_runtime(rate_limit=full_config.get("rate_limit"), persistence_backend=_backend)
    init_context(runtime)

    # Now the servers, with a runtime that knows where it persists.
    load_config(full_config.get("mcp_servers", {}))

    # After they are loaded, so the warning describes a fleet that exists. These
    # upstreams are outside the SSRF policy on purpose; the operator just had no
    # way to find that out from anywhere but the source (#903).
    from .unguarded_endpoints import warn_about_endpoints_the_ssrf_policy_does_not_cover

    warn_about_endpoints_the_ssrf_policy_does_not_cover(full_config)

    # Initialize observability (tracing, Langfuse) early
    _, observability_adapter = init_observability(full_config)

    if _backend is not None:
        # The metric history store is reached through a module-level accessor
        # rather than passed around, so a selected backend has to install it
        # here. Note what the accessor's own docstring says production should do
        # and nobody did: without this, the default is an in-memory store, so
        # metric history has never survived a restart.
        from ...infrastructure.persistence.metrics_history_store import set_metrics_history_store

        set_metrics_history_store(_backend.metrics_history_store())

    # Initialize event store for event sourcing
    init_event_store(runtime, full_config)

    # Initialize event handlers
    init_event_handlers(runtime)
    # Strictly after the handlers exist. Sweeping before them delivers a crash's
    # leftovers to an empty handler table and marks them delivered anyway.
    recover_undelivered_events(runtime)

    # The tailer's cursor is taken here, and the order with the line below is
    # load-bearing: head first, then snapshot. An event that lands between the
    # two is then delivered by the tail rather than falling in the gap between
    # "not in the snapshot yet" and "before my cursor".
    init_event_tailer(runtime)

    # And the fleet itself. After the event store, because each restored server
    # replays its own stream to get its lifecycle state back; before anything
    # serves, because a gateway that answers "no such server" for the first few
    # seconds after every restart is a gateway that lost its fleet.
    restore_persisted_fleet(runtime)

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
        # The store `init_event_store` just configured, not a second one. The
        # `event_sourcing` auth driver calls `read_stream`, `get_stream_version`
        # and `list_streams` -- the port's API -- and used to be handed a legacy
        # in-memory store that has none of the three.
        event_store=runtime.event_bus.event_store,
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

    # Create the MCP server the CLI serves (tools + modern protocol surface).
    mcp_server = build_serving_mcp_server()

    # Wire the ADR-014 governed task-relay serving surface. This HTTP-serve path
    # builds FastMCP directly (not via MCPServerFactory), so without this call
    # ctx.governed_task_store stays None and every upstream task handle is
    # rejected TaskRelayNotSupported regardless of the kill-switch. Kill-switch
    # defaults True (reactivated 2026-07-28 once the SEP-2663 wire was actually
    # served -- see ServerConfig.relay_tasks_enabled). Set
    # relay_tasks_enabled: false in config to restore the relay-only stance.
    from ...fastmcp_server.task_relay_wiring import (
        advertise_tasks_capability,
        enable_governed_task_relay,
    )

    relay_tasks_enabled = bool(full_config.get("relay_tasks_enabled", True))
    enable_governed_task_relay(mcp_server, relay_tasks_enabled=relay_tasks_enabled)
    advertise_tasks_capability(mcp_server, relay_tasks_enabled=relay_tasks_enabled)

    # Wire log buffers to mcp_servers after configuration populates the shared repository.
    init_log_buffers(runtime.repository.get_all())

    # Before the workers and the orchestrator, which ask it whether they may
    # run. Created here and started by lifecycle: holding the lease before the
    # loops it gates exist would mean being the manager with nothing to manage.
    init_lease_keeper(full_config)

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
        _register_configured_sources(discovery_registry, full_config)
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

    # Last gate before the process serves: every subsystem this configuration
    # asks for must be reachable on the path this process actually took. Placed
    # here because `bootstrap()` is the funnel every entry point goes through --
    # `serve`, `serve --http` and the facade all land in it -- so a subsystem
    # wired on one path and not another cannot start quietly (#678). Checks the
    # singleton context, the same object the executor and the REST routes read.
    enforce_subsystem_reachability(full_config, ctx)

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
    "SubsystemRequirement",
    "bootstrap",
    "build_serving_mcp_server",
    "check_subsystem_reachability",
    "collect_subsystem_requirements",
    "enforce_subsystem_reachability",
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
    # Backward compatibility: auth shims
    "AuthComponents",
    "NullAuthComponents",
    "bootstrap_auth",
    "parse_auth_config",
    "_auth_available",
]
