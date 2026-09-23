"""CQRS and Saga initialization."""

from typing import TYPE_CHECKING, Any

from ...application.commands import register_all_handlers as register_command_handlers
from ...application.queries import register_all_handlers as register_query_handlers
from ...application.sagas import GroupRebalanceSaga
from ...application.sagas.mcp_server_failover_saga import McpServerFailoverEventSaga
from ...application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...infrastructure.saga_manager import get_saga_manager
from ...logging_config import get_logger
from ..config import ServerConfigLoader
from ..context import get_context
from ..state import GROUPS, RUNTIME_PROVIDERS, get_runtime, set_group_rebalance_saga
from .components import register_auth_cqrs
from .composition import close_at_shutdown
from .logs import LogBuffers
from .persistence import fleet_restore_gap

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def _current_lease() -> Any:
    """The tenure this instance believes it holds, or None if it holds nothing.

    None is meaningful: it means this instance is coordinating and is not the
    manager right now, so a convergence loop's write must be refused. A gateway
    with no keeper at all never reaches here -- `_fleet_writer` passes no
    provider in that case, which is the standalone answer.
    """
    from .coordination import get_lease_keeper

    keeper = get_lease_keeper()
    return None if keeper is None else keeper.lease


def _coordinated() -> bool:
    """Whether this gateway shares its state with peers.

    Read per registration rather than captured, for the same reason everything
    else in this file is: bootstrap order should not decide the answer.
    """
    from .coordination import get_lease_keeper

    return get_lease_keeper() is not None


def _fleet_writer(runtime: "Runtime") -> Any:
    """Where fleet changes get recorded, or None if nothing keeps them.

    Deliberately reads `runtime.config_repository` rather than reaching for a
    backend: that field already *is* the selected backend's repository when one
    was chosen (#786), and it is the in-memory one otherwise. Writing to the
    in-memory repository would be worse than not writing at all -- the record
    would exist, `/api/config` would report it, and it would still be gone on
    restart, which is a lie rather than a gap.
    """
    from ...infrastructure.persistence.config_repository import InMemoryMcpServerConfigRepository
    from ...infrastructure.persistence.fleet_writer import RepositoryFleetWriter

    repository = runtime.config_repository
    if repository is None or isinstance(repository, InMemoryMcpServerConfigRepository):
        logger.info(
            "fleet_writer_absent",
            detail="no durable config repository; registrations live in memory and end with the process",
        )
        return None
    # The lease is asked per write rather than captured here: a tenure that has
    # ended between bootstrap and the write is exactly the case being fenced.
    writer = RepositoryFleetWriter(repository, lease_provider=_current_lease)
    close_at_shutdown(writer.close)
    logger.info("fleet_writer_configured", repository=type(repository).__name__)
    return writer


def init_cqrs(
    runtime: "Runtime",
    current_config_path: str | None = None,
    discovery_registry: Any | None = None,
) -> None:
    """Register command and query handlers.

    Args:
        runtime: Runtime instance with command and query buses.
        current_config_path: Current configuration file path for reload handler.
        discovery_registry: Optional DiscoveryRegistry; if provided, registers
            discovery source management handlers.
    """
    from ...application.commands.crud_handlers import register_crud_handlers

    repository = get_runtime().repository

    register_command_handlers(
        runtime.command_bus,
        repository,
        runtime.event_bus,
        current_config_path,
        config_loader=ServerConfigLoader(),
        groups=GROUPS,
        runtime_store=RUNTIME_PROVIDERS,
    )
    register_query_handlers(
        runtime.query_bus,
        repository,
        runtime_store=RUNTIME_PROVIDERS,
        event_store=runtime.event_bus.event_store,
    )
    register_crud_handlers(
        runtime.command_bus,
        repository,
        runtime.event_bus,
        GROUPS,
        _fleet_writer(runtime),
        coordinated=_coordinated,
        # So deleting a server releases the buffer its output was read from,
        # rather than leaving it registered under an id that is free (#1506).
        log_buffers=LogBuffers(),
        restore_gap=fleet_restore_gap(runtime),
    )

    if discovery_registry is not None:
        from ...application.commands.discovery_handlers import register_discovery_handlers

        register_discovery_handlers(runtime.command_bus, discovery_registry)
        logger.info("discovery_cqrs_handlers_registered")

    logger.info("cqrs_handlers_registered")


def init_auth_cqrs(runtime: "Runtime", auth_components: Any) -> None:
    """Register auth command and query handlers if auth is enabled.

    Args:
        runtime: Runtime instance with command and query buses.
        auth_components: AuthComponents from bootstrap_auth().
    """
    if auth_components is None or not getattr(auth_components, "enabled", False):
        logger.info("auth_cqrs_skipped", reason="auth_disabled")
        return

    if not register_auth_cqrs(runtime, auth_components):
        logger.info("auth_cqrs_skipped", reason="auth_module_unavailable")
        return
    logger.info("auth_cqrs_handlers_registered")


def _create_saga_state_store(
    full_config: dict[str, Any] | None = None,
) -> SagaStateStore | NullSagaStateStore:
    """Create SagaStateStore based on event_store configuration.

    Creates a SagaStateStore backed by SQLite when the event store driver is
    "sqlite". Otherwise returns a NullSagaStateStore (no-op).

    Args:
        full_config: Full application configuration dictionary.

    Returns:
        SagaStateStore or NullSagaStateStore instance.
    """
    if full_config is None:
        return NullSagaStateStore()

    # A selected backend supplies this, like every other persisted concern. The
    # driver reading below is the compatibility path -- note that it keys saga
    # state off the *event store's* driver, which is exactly the kind of
    # cross-subsystem coupling one storage decision removes.
    from .composition import get_persistence_backend

    backend = get_persistence_backend()
    if backend is not None:
        # The backend is untyped at this boundary -- it hands back a port
        # implementation and mypy sees Any. Narrow here rather than letting it
        # leak out of a function that promises a store.
        store: SagaStateStore = backend.saga_state_store()
        logger.info("saga_state_store_from_persistence_backend")
        return store

    event_store_config = full_config.get("event_store", {})
    driver = event_store_config.get("driver", "memory")

    if driver != "sqlite":
        return NullSagaStateStore()

    from ...infrastructure.persistence.database_common import SQLiteConfig, SQLiteConnectionFactory

    saga_db_path = "data/saga_state.db"
    try:
        factory = SQLiteConnectionFactory(SQLiteConfig(path=saga_db_path))
        store = SagaStateStore(factory)
        logger.info("saga_state_store_created", path=saga_db_path)
        return store
    except OSError as e:
        logger.warning(
            "saga_state_store_sqlite_fallback_to_null",
            error=str(e),
            path=saga_db_path,
        )
        return NullSagaStateStore()


def _restore_saga_state(
    store: SagaStateStore | NullSagaStateStore,
    saga: "McpServerRecoverySaga | McpServerFailoverEventSaga",
) -> None:
    """Restore saga state from persistent store.

    Loads the last checkpointed state for the given saga and calls
    from_dict() to restore it. If no persisted state exists (first boot),
    this is a no-op.

    Args:
        store: Saga state store to load from.
        saga: Saga instance to restore state into.
    """
    result = store.load(saga.saga_type)
    if result is None:
        logger.debug("saga_state_not_found", saga_type=saga.saga_type)
        return

    saga.from_dict(result["state_data"])
    logger.info(
        "saga_state_restored",
        saga_type=saga.saga_type,
        last_event_position=result["last_event_position"],
    )


def init_saga(full_config: dict[str, Any] | None = None) -> None:
    """Initialize all sagas with optional persistence.

    Creates SagaStateStore when SQLite event store is configured, loads
    persisted state for recovery and failover sagas, and registers all three
    sagas.

    Group circuit breakers are not persisted. Each replica keeps its own, and a
    group's circuit closes by itself once its members recover (#1383), so one
    saved as open hours earlier would only hold traffic off a group that is
    healthy now (#1388). A `circuit_breaker` row an older version left in the
    store is never loaded.

    Args:
        full_config: Full application configuration dictionary.
    """
    ctx = get_context()
    saga_manager = get_saga_manager()

    # Create SagaStateStore if SQLite event store is configured
    saga_state_store = _create_saga_state_store(full_config)

    # Inject store into saga manager
    saga_manager._saga_state_store = saga_state_store

    # 1. GroupRebalanceSaga. `GROUPS`, not `ctx.groups`: at this point in
    # `bootstrap()` the context still holds the empty dict it was built with,
    # and is pointed at `GROUPS` only at the end. The saga kept the empty one,
    # found no group for any member, and so no passing health check ever put a
    # member back in rotation (#1355).
    # The bus, so a group drains on the check that changed it (#1410).
    group_saga = GroupRebalanceSaga(groups=GROUPS, event_bus=ctx.event_bus)
    ctx.group_rebalance_saga = group_saga
    set_group_rebalance_saga(group_saga)
    saga_manager.register_event_saga(group_saga)

    # 2. McpServerRecoverySaga
    recovery_saga = McpServerRecoverySaga(saga_manager=saga_manager)
    _restore_saga_state(saga_state_store, recovery_saga)
    saga_manager.register_event_saga(recovery_saga)

    # 3. McpServerFailoverEventSaga
    failover_saga = McpServerFailoverEventSaga(saga_manager=saga_manager)
    _restore_saga_state(saga_state_store, failover_saga)
    saga_manager.register_event_saga(failover_saga)

    logger.info(
        "sagas_initialized",
        sagas_registered=3,
        persistence_enabled=not isinstance(saga_state_store, NullSagaStateStore),
    )
