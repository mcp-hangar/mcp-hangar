"""Event store initialization."""

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ...domain.contracts.event_store import NullEventStore
from ...domain.exceptions import ConfigurationError
from ...logging_config import get_logger
from ...observability.health import (
    EventStoreDurabilityStatus,
    register_event_store_durability_check,
    set_event_store_durability_status,
)
from .enterprise import create_enterprise_event_store

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


class EventStoreConfigurationError(RuntimeError):
    """Raised when a durable event store cannot be initialized as configured.

    Surfaces (instead of silently degrading to a non-durable in-memory store)
    when the configured SQLite driver cannot be used -- e.g. the path/dir is
    not writable on a read-only deploy, or the SQLite backend is unavailable.
    """


def init_event_store(runtime: "Runtime", config: dict[str, Any]) -> None:
    """Initialize event store for event sourcing.

    Configures the event store based on config.yaml settings.
    Defaults to SQLite if not specified.

    Durability policy: when a durable driver (``sqlite``) is configured but the
    store cannot be initialized (path not writable, backend unavailable), this
    fails fast with :class:`EventStoreConfigurationError` rather than silently
    swapping in a non-durable in-memory store. A non-durable store is only used
    when the operator opts in explicitly -- either ``driver: memory`` or
    ``allow_memory_fallback: true``. When the fallback is taken, the degraded
    durability posture is recorded so ``/health/ready`` reports it.

    Config example:
        event_store:
            enabled: true
            driver: sqlite  # or "memory"
            path: data/events.db
            allow_memory_fallback: false  # opt in to non-durable fallback

    Args:
        runtime: Runtime instance with event bus.
        config: Full configuration dictionary.

    Raises:
        EventStoreConfigurationError: when a durable driver is configured but
            cannot be initialized and no explicit memory fallback was requested,
            or when an unknown driver is configured.
    """
    event_store_config = config.get("event_store", {})
    enabled = event_store_config.get("enabled", True)

    if not enabled:
        logger.info("event_store_disabled")
        runtime.event_bus.set_event_store(NullEventStore())
        set_event_store_durability_status(
            EventStoreDurabilityStatus(
                configured_driver="disabled",
                durable=False,
                degraded=False,
                detail="event store disabled",
            )
        )
        register_event_store_durability_check()
        return

    driver = event_store_config.get("driver", "sqlite")
    allow_memory_fallback = bool(event_store_config.get("allow_memory_fallback", False))

    from ...domain.contracts.event_store import IEventStore

    event_store: IEventStore

    if driver == "memory":
        from ...infrastructure.persistence import InMemoryEventStore

        event_store = InMemoryEventStore()
        logger.info("event_store_initialized", driver="memory")
        set_event_store_durability_status(
            EventStoreDurabilityStatus(
                configured_driver="memory",
                durable=False,
                degraded=False,
                detail="in-memory store explicitly configured (non-durable)",
            )
        )
    elif driver == "sqlite":
        db_path = event_store_config.get("path", "data/events.db")
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            _result = create_enterprise_event_store(driver, event_store_config)
            if _result is None:
                raise ConfigurationError("SQLite event store is unavailable")
            event_store = _result
            logger.info("event_store_initialized", driver="sqlite", path=db_path)
            set_event_store_durability_status(
                EventStoreDurabilityStatus(
                    configured_driver="sqlite",
                    durable=True,
                    degraded=False,
                    detail=f"sqlite at {db_path}",
                )
            )
        except OSError as e:
            # Path/dir is not writable (e.g. a read-only deploy). Do NOT silently
            # drop durability -- fail fast unless an in-memory fallback was
            # explicitly opted into.
            if not allow_memory_fallback:
                raise EventStoreConfigurationError(
                    f"event store path {db_path!r} is not writable ({e}); "
                    "set event_store.driver: memory to explicitly opt into a "
                    "non-durable store, or event_store.allow_memory_fallback: true "
                    "to accept a non-durable in-memory fallback"
                ) from e
            logger.warning(
                "event_store_sqlite_fallback_to_memory",
                error=str(e),
                path=db_path,
                allow_memory_fallback=True,
            )
            from ...infrastructure.persistence import InMemoryEventStore

            event_store = InMemoryEventStore()
            logger.warning(
                "event_store_degraded_to_memory",
                driver="sqlite",
                reason="path_not_writable",
                path=db_path,
            )
            set_event_store_durability_status(
                EventStoreDurabilityStatus(
                    configured_driver="sqlite",
                    durable=False,
                    degraded=True,
                    detail=f"sqlite path {db_path} not writable; degraded to in-memory",
                )
            )
        except ImportError:
            # The SQLite backend could not be loaded. Same policy: fail fast unless
            # a non-durable fallback was explicitly requested.
            if not allow_memory_fallback:
                raise EventStoreConfigurationError(
                    "the SQLite event store backend could not be loaded; "
                    "install the persistence backend, set event_store.driver: memory "
                    "to explicitly opt into a non-durable store, or "
                    "event_store.allow_memory_fallback: true to accept a non-durable "
                    "in-memory fallback"
                )
            logger.warning(
                "event_store_sqlite_unavailable",
                fallback="memory",
                hint="SQLite event store could not be loaded.",
                allow_memory_fallback=True,
            )
            from ...infrastructure.persistence import InMemoryEventStore

            event_store = InMemoryEventStore()
            set_event_store_durability_status(
                EventStoreDurabilityStatus(
                    configured_driver="sqlite",
                    durable=False,
                    degraded=True,
                    detail="sqlite backend unavailable; degraded to in-memory",
                )
            )
    else:
        raise EventStoreConfigurationError(f"unknown event_store.driver {driver!r}; expected 'sqlite' or 'memory'")

    runtime.event_bus.set_event_store(event_store)
    register_event_store_durability_check()
