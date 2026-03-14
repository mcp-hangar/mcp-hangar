"""CQRS and Saga initialization."""

from typing import Any, TYPE_CHECKING

from ...application.commands import register_all_handlers as register_command_handlers
from ...application.commands.auth_handlers import register_auth_command_handlers
from ...application.queries import register_all_handlers as register_query_handlers
from ...application.queries.auth_handlers import register_auth_query_handlers
from ...application.sagas import GroupRebalanceSaga
from ...application.sagas.provider_failover_saga import ProviderFailoverSaga
from ...application.sagas.provider_recovery_saga import ProviderRecoverySaga
from ...domain.model.circuit_breaker import CircuitBreaker
from ...infrastructure.persistence.saga_state_store import NullSagaStateStore, SagaStateStore
from ...infrastructure.saga_manager import get_saga_manager
from ...logging_config import get_logger
from ..context import get_context
from ..state import set_group_rebalance_saga

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_cqrs(runtime: "Runtime", current_config_path: str | None = None) -> None:
    """Register command and query handlers.

    Args:
        runtime: Runtime instance with command and query buses.
        current_config_path: Current configuration file path for reload handler.
    """
    from ..state import PROVIDER_REPOSITORY

    register_command_handlers(runtime.command_bus, PROVIDER_REPOSITORY, runtime.event_bus, current_config_path)
    register_query_handlers(runtime.query_bus, PROVIDER_REPOSITORY)
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

    register_auth_command_handlers(
        runtime.command_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
    )
    register_auth_query_handlers(
        runtime.query_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
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
    factory = SQLiteConnectionFactory(SQLiteConfig(path=saga_db_path))
    store = SagaStateStore(factory)
    logger.info("saga_state_store_created", path=saga_db_path)
    return store


def _restore_saga_state(
    store: SagaStateStore | NullSagaStateStore,
    saga: "ProviderRecoverySaga | ProviderFailoverSaga",
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
    """Restore circuit breaker state for provider groups from saga state store.

    Loads CB state persisted under saga_type="circuit_breaker" with saga_id=group_id.
    If found, replaces the group's CircuitBreaker with the restored one.

    Args:
        store: Saga state store to load from.
        groups: Dictionary of group_id -> ProviderGroup.
    """
    for group_id, group in groups.items():
        result = store.load("circuit_breaker")
        if result is None:
            continue

        try:
            cb = CircuitBreaker.from_dict(result["state_data"])
            group._circuit_breaker = cb
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
    """Save circuit breaker state for all provider groups.

    Persists CB state under saga_type="circuit_breaker" with saga_id=group_id.
    Called during shutdown to preserve CB state across restarts.

    Args:
        store: Saga state store to save to.
        groups: Dictionary of group_id -> ProviderGroup.
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
    breaker state for provider groups, and registers all three sagas.

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

    # 2. ProviderRecoverySaga
    recovery_saga = ProviderRecoverySaga()
    _restore_saga_state(saga_state_store, recovery_saga)
    saga_manager.register_event_saga(recovery_saga)

    # 3. ProviderFailoverSaga
    failover_saga = ProviderFailoverSaga()
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
