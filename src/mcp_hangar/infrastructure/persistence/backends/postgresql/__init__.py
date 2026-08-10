"""The PostgreSQL storage backend: every adapter, for a multi-node install.

The answer for anything with more than one node, and the reason the persistence
layer became selectable at all: SQLite is a file, and a file is not a thing two
gateways can share.

`psycopg2` is known to the shared connection factory and nowhere else. Every
adapter here takes an `IConnectionFactory` and asks it for a connection; none
opens one, none builds a pool, and none carries a branch on the dialect. That is
what keeps the two backends genuinely separate implementations rather than one
implementation with two modes.

Selected whole, like every backend: `create_backend` refuses this one unless it
serves every concern. The gap that rule exists for was here -- the previous
PostgreSQL support covered API keys and roles and silently returned nothing for
tool-access policies.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class PostgresqlBackend:
    """Every persistence concern, served from one PostgreSQL database.

    Adapters are built lazily and cached: bootstrap asks for the same concern
    more than once, and a second call must not open a second pool.
    """

    #: The point of this backend. Several gateways connect to one database, so
    #: the lease, the log and the fleet are the same objects for all of them.
    shared_across_instances = True

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise from this backend's own configuration.

        Args:
            config: The `persistence.postgresql` block. Keys: `host`, `port`,
                `database`, `user`, `password`, `min_connections`,
                `max_connections`, `table_prefix`, `metrics_retention_days`.
                None of these mean anything to another backend, which is why the
                block is passed through untouched.
        """
        self._config = config
        self._table_prefix = str(config.get("table_prefix", ""))
        self._cache: dict[str, Any] = {}
        self._factory: Any = None

    def _connections(self) -> Any:
        """The one connection factory this backend hands to every adapter.

        Built once. Two factories would mean two pools against the same
        database, and adapters disagreeing about how many connections this
        process is allowed to hold.
        """
        if self._factory is None:
            from mcp_hangar.infrastructure.persistence.database_common import (
                PostgresConfig,
                PostgresConnectionFactory,
            )

            self._factory = PostgresConnectionFactory(
                PostgresConfig(
                    host=str(self._config.get("host", "localhost")),
                    port=int(self._config.get("port", 5432)),
                    database=str(self._config.get("database", "mcp_hangar")),
                    user=str(self._config.get("user", "")),
                    password=str(self._config.get("password", "")),
                    min_connections=int(self._config.get("min_connections", 2)),
                    max_connections=int(self._config.get("max_connections", 10)),
                )
            )
        return self._factory

    def _cached(self, key: str, build: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # -- the concerns ------------------------------------------------------

    def event_store(self) -> Any:
        def build() -> Any:
            from .event_store import PostgresEventStore

            store = PostgresEventStore(self._connections(), table_prefix=self._table_prefix)
            # Every other adapter in this backend creates its schema when it is
            # built. This one keeps that step as a separate method, and nothing
            # called it -- so a gateway on `persistence.backend: postgresql` ran
            # with no `events` table at all. Nothing said so until something
            # read the log: appends were the first write and the tailer the
            # first read, and the tailer reported `relation "events" does not
            # exist` every two seconds into a log nobody was watching.
            #
            # Called here rather than in the constructor because the store's own
            # docstring is right that construction should not reach into a
            # possibly-lazy pool; the backend is the thing that knows the pool
            # is ready. Found by deploying it (#790, phase 4.4).
            store.initialize()
            return store

        return self._cached("event_store", build)

    def dispatch_checkpoint(self) -> Any:
        def build() -> Any:
            from .dispatch_checkpoint import PostgresDispatchCheckpoint

            # The same database as the log it points into. A mark in one place
            # and its events in another can outlive them, and then claim
            # delivery of events that are gone.
            return PostgresDispatchCheckpoint(self._connections())

        return self._cached("dispatch_checkpoint", build)

    def config_repository(self) -> Any:
        def build() -> Any:
            from .config_repository import PostgresMcpServerConfigRepository

            return PostgresMcpServerConfigRepository(self._connections())

        return self._cached("config_repository", build)

    def audit_repository(self) -> Any:
        def build() -> Any:
            from .audit_repository import PostgresAuditRepository

            return PostgresAuditRepository(self._connections())

        return self._cached("audit_repository", build)

    def saga_state_store(self) -> Any:
        def build() -> Any:
            from .saga_state_store import PostgresSagaStateStore

            return PostgresSagaStateStore(self._connections())

        return self._cached("saga_state_store", build)

    def approval_repository(self) -> Any:
        def build() -> Any:
            from .approval_repository import PostgresApprovalRepository

            return PostgresApprovalRepository(self._connections())

        return self._cached("approval_repository", build)

    # The three below keep schema creation in a separate `initialize()` and are
    # called for it here, for exactly the reason `event_store` above is: the
    # legacy per-subsystem branches in `auth/bootstrap.py` call it themselves,
    # the one-storage branch hands the store straight out, and nothing else
    # ever did. The gateway then started, reached the auth bootstrap and died on
    # `relation "roles" does not exist` -- or, with no `role_assignments` to
    # trip it, on `tool_access_policies` a few lines later. Every auth-enabled
    # deployment on a selected backend, which is the configuration more than one
    # replica requires.

    def api_key_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore

            store = PostgresApiKeyStore(self._connections(), table_prefix=self._table_prefix)
            store.initialize()
            return store

        return self._cached("api_key_store", build)

    def role_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.auth.infrastructure.postgres_store import PostgresRoleStore

            store = PostgresRoleStore(self._connections(), table_prefix=self._table_prefix)
            store.initialize()
            return store

        return self._cached("role_store", build)

    def tool_access_policy_store(self) -> Any:
        def build() -> Any:
            from .tool_access_policy_store import PostgresToolAccessPolicyStore

            store = PostgresToolAccessPolicyStore(self._connections())
            store.initialize()
            return store

        return self._cached("tool_access_policy_store", build)

    def metrics_history_store(self) -> Any:
        def build() -> Any:
            from .metrics_history_store import PostgresMetricsHistoryStore

            return PostgresMetricsHistoryStore(
                self._connections(),
                retention_days=int(self._config.get("metrics_retention_days", 7)),
            )

        return self._cached("metrics_history_store", build)

    def management_lease(self) -> Any:
        def build() -> Any:
            from .management_lease import PostgresManagementLease

            return PostgresManagementLease(self._connections())

        return self._cached("management_lease", build)

    def close(self) -> None:
        """Release the pool. Safe to call when nothing was opened."""
        self._cache.clear()
        if self._factory is not None:
            closer = getattr(self._factory, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as e:  # noqa: BLE001 -- shutdown must not fail on a stubborn pool
                    logger.warning("persistence_close_failed", backend="postgresql", error=str(e))
            self._factory = None
