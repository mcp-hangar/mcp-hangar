"""CQRS and Saga initialization."""

from typing import Any, TYPE_CHECKING

from ...application.commands import register_all_handlers as register_command_handlers
from ...application.queries import register_all_handlers as register_query_handlers
from ...application.sagas import GroupRebalanceSaga
from ...application.sagas.mcp_server_failover_saga import McpServerFailoverEventSaga
from ...application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from ...domain.model.circuit_breaker import CircuitBreaker
from ...infrastructure.event_store import get_event_store
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...infrastructure.saga_manager import get_saga_manager
from ...logging_config import get_logger
from ..config import ServerConfigLoader
from ..context import get_context
from ..state import get_runtime, GROUPS, RUNTIME_PROVIDERS, set_group_rebalance_saga

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


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
        event_store=get_event_store(),
    )
    register_crud_handlers(runtime.command_bus, repository, runtime.event_bus, GROUPS)

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

    try:
        from enterprise.auth.commands.handlers import register_auth_command_handlers
        from enterprise.auth.queries.handlers import register_auth_query_handlers
    except ImportError:
        logger.info("auth_cqrs_skipped", reason="enterprise_not_installed")
        return

    tap_store = getattr(auth_components, "tap_store", None)
    event_bus = getattr(runtime, "event_bus", None)

    register_auth_command_handlers(
        runtime.command_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
        tap_store=tap_store,
        event_bus=event_bus,
    )
    register_auth_query_handlers(
        runtime.query_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
        tap_store=tap_store,
    )
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


def _restore_group_circuit_breakers(
    store: SagaStateStore | NullSagaStateStore,
    groups: dict[str, Any],
) -> None:
    """Restore circuit breaker state for mcp_server groups from saga state store.

    Loads CB state persisted under saga_type="circuit_breaker" with saga_id=group_id.
    If found, replaces the group's CircuitBreaker with the restored one.

    Args:
        store: Saga state store to load from.
        groups: Dictionary of group_id -> McpServerGroup.
    """
    for group_id, group in groups.items():
        result = store.load("circuit_breaker")
        if result is None:
            continue

        try:
            cb = CircuitBreaker.from_dict(result["state_data"])
            group._circuit_breaker = cb
            # Re-wire the state-change callback so transitions after restore emit events/metrics.
            cb._on_state_change = group._on_circuit_breaker_state_change
            logger.info(
                "circuit_breaker_restored",
                group_id=group_id,
                state=cb.state.value,
                failure_count=cb.failure_count,
            )
        except Exception as e:  # noqa: BLE001 -- fault-barrier: CB restore failure must not prevent bootstrap
            logger.warning(
                "circuit_breaker_restore_failed",
                group_id=group_id,
                error=str(e),
            )


def save_group_circuit_breakers(
    store: SagaStateStore | NullSagaStateStore,
    groups: dict[str, Any],
) -> None:
    """Save circuit breaker state for all mcp_server groups.

    Persists CB state under saga_type="circuit_breaker" with saga_id=group_id.
    Called during shutdown to preserve CB state across restarts.

    Args:
        store: Saga state store to save to.
        groups: Dictionary of group_id -> McpServerGroup.
    """
    for group_id, group in groups.items():
        try:
            cb_dict = group._circuit_breaker.to_dict()
            store.checkpoint(
                saga_type="circuit_breaker",
                saga_id=group_id,
                state_data=cb_dict,
                last_event_position=0,
            )
            logger.debug("circuit_breaker_saved", group_id=group_id)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: CB save failure must not prevent shutdown
            logger.warning(
                "circuit_breaker_save_failed",
                group_id=group_id,
                error=str(e),
            )


def init_saga(full_config: dict[str, Any] | None = None) -> SagaStateStore | NullSagaStateStore:
    """Initialize all sagas with optional persistence.

    Creates SagaStateStore when SQLite event store is configured, loads
    persisted state for recovery and failover sagas, restores circuit
    breaker state for mcp_server groups, and registers all three sagas.

    Args:
        full_config: Full application configuration dictionary.

    Returns:
        The saga state store instance (for shutdown access).
    """
    ctx = get_context()
    saga_manager = get_saga_manager()

    # Create SagaStateStore if SQLite event store is configured
    saga_state_store = _create_saga_state_store(full_config)

    # Inject store into saga manager
    saga_manager._saga_state_store = saga_state_store

    # 1. GroupRebalanceSaga (existing)
    group_saga = GroupRebalanceSaga(groups=ctx.groups)
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

    # 4. Restore circuit breaker state for groups
    _restore_group_circuit_breakers(saga_state_store, ctx.groups)

    logger.info(
        "sagas_initialized",
        sagas_registered=3,
        persistence_enabled=not isinstance(saga_state_store, NullSagaStateStore),
    )

    return saga_state_store
