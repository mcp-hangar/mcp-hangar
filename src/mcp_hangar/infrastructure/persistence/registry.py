"""Where a storage backend name becomes every adapter the gateway persists through.

One decision, one backend, all of it. `sqlite` or `postgresql` selects a whole
set of adapters at once, so a deployment that is half one and half the other
stops being expressible -- which it was, because `auth.storage.driver` and
`event_store.driver` were independent settings and nothing compared them.

This mirrors `infrastructure/discovery/registry.py` deliberately, down to the
entry point group, because the problem is the same one: a family of adapters
that must be selectable, isolated from each other, and extensible without
patching core. A third backend -- MySQL, CockroachDB, a hosted variant -- is a
package and an entry point, exactly as a Consul discovery source is.

**A backend is complete or it is refused.** `create_backend` checks that every
concern is provided and raises with the missing ones named. That rule exists
because of a specific failure: the PostgreSQL auth driver returned `None` for
the tool-access policy store, which silently disabled policy management and its
replay at startup -- a security surface, switched off by an assignment nobody
read. A partial backend is now unrepresentable rather than merely discouraged.

Isolation, concretely: each backend package owns its own driver import and its
own SQL. `sqlite3` is known to the sqlite backend, `psycopg2` to the postgresql
one, and no store anywhere carries a dialect branch. That is what makes the
second backend an adapter rather than a rewrite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final, Protocol, runtime_checkable

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: Entry point group third-party persistence backends register under.
ENTRY_POINT_GROUP: Final = "mcp_hangar.persistence_backends"

#: The concerns a backend must provide. Named here rather than derived from the
#: Protocol so the error message can list them in a fixed, readable order, and
#: so adding a concern is a deliberate edit in one place.
REQUIRED_CONCERNS: Final = (
    "event_store",
    "dispatch_checkpoint",
    "config_repository",
    "audit_repository",
    "saga_state_store",
    "approval_repository",
    "api_key_store",
    "role_store",
    "tool_access_policy_store",
    "metrics_history_store",
)


@runtime_checkable
class PersistenceBackend(Protocol):
    """Everything the gateway persists, from one backend.

    Every method returns a port-typed object. Nothing here exposes a connection,
    a cursor, a dialect or a driver: callers get an adapter and never learn what
    is behind it.

    Methods are expected to be idempotent -- called more than once during
    bootstrap, they return the same instance rather than opening a second
    connection pool.
    """

    def event_store(self) -> Any: ...

    def dispatch_checkpoint(self) -> Any: ...

    def config_repository(self) -> Any: ...

    def audit_repository(self) -> Any: ...

    def saga_state_store(self) -> Any: ...

    def approval_repository(self) -> Any: ...

    def api_key_store(self) -> Any: ...

    def role_store(self) -> Any: ...

    def tool_access_policy_store(self) -> Any: ...

    def metrics_history_store(self) -> Any: ...

    def close(self) -> None: ...


#: Builds a backend from its own configuration. The config dict is passed
#: through untouched -- only the factory knows what its keys mean, which is what
#: keeps `path` out of the postgresql backend and `host` out of the sqlite one.
BackendFactory = Callable[[dict[str, Any]], PersistenceBackend]

_FACTORIES: dict[str, BackendFactory] = {}
_ENTRY_POINTS_LOADED = False


class UnknownPersistenceBackendError(ValueError):
    """A configured backend name has no factory.

    Raised rather than defaulted. Falling back to SQLite when someone asked for
    PostgreSQL would start a gateway that quietly writes to a local file while
    its operator believes a shared database is being used -- which is the worst
    version of this failure, because it looks like it worked.
    """

    def __init__(self, name: str, known: list[str]) -> None:
        self.name = name
        self.known = known
        super().__init__(
            f"unknown persistence backend {name!r}; known backends: {', '.join(known) or '(none)'}. "
            f"Third-party backends register under the {ENTRY_POINT_GROUP!r} entry point group."
        )


class IncompletePersistenceBackendError(ValueError):
    """A backend does not provide every concern.

    The error names what is missing, because the alternative -- returning `None`
    for a concern and letting the consumer decide -- is how tool-access policies
    were silently disabled on the PostgreSQL driver for as long as it existed.
    """

    def __init__(self, name: str, missing: list[str]) -> None:
        self.backend = name
        self.missing = missing
        super().__init__(
            f"persistence backend {name!r} does not provide: {', '.join(missing)}. "
            "A backend serves every concern or it is not selectable -- a partial backend "
            "means a feature is silently switched off by whichever store is absent."
        )


def register_backend_factory(name: str, factory: BackendFactory, *, replace: bool = False) -> None:
    """Make `name` selectable as a storage backend.

    Args:
        name: The value used in configuration.
        factory: Callable taking that backend's config and returning it.
        replace: Allow overriding an existing registration. Off by default so a
            plugin cannot quietly shadow a built-in backend -- taking over
            `postgresql` should be a decision, not an import side effect.

    Raises:
        ValueError: if the name is already registered and `replace` is False.
    """
    if not replace and name in _FACTORIES:
        raise ValueError(
            f"persistence backend {name!r} is already registered; pass replace=True to override it deliberately"
        )
    _FACTORIES[name] = factory


def available_backends() -> list[str]:
    """Every registered backend name, entry points included."""
    _load_entry_points()
    return sorted(_FACTORIES)


def create_backend(name: str, config: dict[str, Any]) -> PersistenceBackend:
    """Build the one backend this gateway will use.

    Args:
        name: The configured backend.
        config: That backend's own configuration, passed to its factory
            untouched.

    Returns:
        The backend, verified to provide every concern.

    Raises:
        UnknownPersistenceBackendError: if nothing is registered for the name.
        IncompletePersistenceBackendError: if it does not serve every concern.
    """
    _load_entry_points()
    factory = _FACTORIES.get(name)
    if factory is None:
        raise UnknownPersistenceBackendError(name, sorted(_FACTORIES))

    backend = factory(config)

    missing = [concern for concern in REQUIRED_CONCERNS if not callable(getattr(backend, concern, None))]
    if missing:
        raise IncompletePersistenceBackendError(name, missing)

    logger.info("persistence_backend_selected", backend=name)
    return backend


def _load_entry_points() -> None:
    """Register third-party backends advertised through the entry point group.

    Once per process. A plugin that fails to load is logged and skipped rather
    than taken as a reason to refuse startup: a broken third-party package must
    not stop the gateway, and the configured-but-missing case is already covered
    -- `create_backend` raises when the name it needs is absent.
    """
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True

    from importlib.metadata import entry_points

    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            factory = entry_point.load()
        except Exception as e:  # noqa: BLE001 -- a third-party import must not stop startup
            logger.warning("persistence_backend_plugin_failed", backend=entry_point.name, error=str(e))
            continue
        if entry_point.name in _FACTORIES:
            logger.warning(
                "persistence_backend_plugin_ignored",
                backend=entry_point.name,
                detail="a backend of this name is already registered; the plugin was not applied",
            )
            continue
        _FACTORIES[entry_point.name] = factory
        logger.info("persistence_backend_plugin_registered", backend=entry_point.name)


def _sqlite(config: dict[str, Any]) -> PersistenceBackend:
    from .backends.sqlite import SqliteBackend

    return SqliteBackend(config)


def _postgresql(config: dict[str, Any]) -> PersistenceBackend:
    from .backends.postgresql import PostgresqlBackend

    return PostgresqlBackend(config)


for _name, _factory in (("sqlite", _sqlite), ("postgresql", _postgresql)):
    register_backend_factory(_name, _factory)
