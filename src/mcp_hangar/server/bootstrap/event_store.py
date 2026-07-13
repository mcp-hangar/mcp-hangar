"""Event store initialization."""

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ...domain.contracts.event_store import NullEventStore
from ...domain.exceptions import ConfigurationError
from ...logging_config import get_logger
from .enterprise import create_enterprise_event_store

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_event_store(runtime: "Runtime", config: dict[str, Any]) -> None:
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

    from ...domain.contracts.event_store import IEventStore

    event_store: IEventStore

    if driver == "memory":
        from ...infrastructure.persistence import InMemoryEventStore

        event_store = InMemoryEventStore()
        logger.info("event_store_initialized", driver="memory")
    elif driver == "sqlite":
        db_path = event_store_config.get("path", "data/events.db")
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            _result = create_enterprise_event_store(driver, event_store_config)
            if _result is None:
                raise ConfigurationError("SQLite event store is unavailable")
            event_store = _result
            logger.info("event_store_initialized", driver="sqlite", path=db_path)
        except OSError as e:
            raise ConfigurationError(
                f"SQLite event store path is unavailable: {db_path}",
                details={"path": db_path, "error": str(e)},
            ) from e
    else:
        logger.warning(
            "unknown_event_store_driver",
            driver=driver,
            fallback="memory",
        )
        from ...infrastructure.persistence import InMemoryEventStore

        event_store = InMemoryEventStore()

    runtime.event_bus.set_event_store(event_store)
