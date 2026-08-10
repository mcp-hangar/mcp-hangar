"""The SQLite storage backend: every adapter the gateway persists through.

Registered like any other backend rather than treated as the real one with
others bolted beside it. Its factory sits next to PostgreSQL's, both are built
the same way, and both are refused if they do not serve every concern.

This is the standalone answer: one process, one host, a file per database. It is
the default because it needs nothing installed and nothing configured, not
because it is the serious option -- a multi-node deployment wants PostgreSQL,
and the two are chosen whole, never mixed.

`sqlite3` is known here and in the adapters this composes. Nothing outside this
package learns which database is underneath.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


def _shutdown_loop():
    """A loop to await async `close()` methods on, during a sync shutdown."""
    from mcp_hangar.infrastructure.async_bridge import BackgroundLoop

    return BackgroundLoop()


#: Where the databases live when configuration says nothing.
DEFAULT_DATA_DIR = "data"


class SqliteBackend:
    """Every persistence concern, served from SQLite files under one directory.

    Adapters are built lazily and cached: bootstrap asks for the same concern
    more than once, and a second call must not open a second connection to the
    same file.
    """

    #: A file, opened by one process. Three replicas pointed at "sqlite" do not
    #: share anything: each gets its own file, its own lease -- which its own
    #: adapter always grants -- and its own fleet. Nothing collides, so nothing
    #: complains, and every replica looks healthy while the deployment has as
    #: many fleets as it has pods.
    shared_across_instances = False

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise from this backend's own configuration.

        Args:
            config: The `persistence` block's backend-specific settings. Keys:
                `data_dir` (default `data`), and optional per-database `path`
                overrides. No key here means anything to another backend -- that
                is the point of passing the dict through untouched.
        """
        self._config = config
        self._data_dir = Path(str(config.get("data_dir", DEFAULT_DATA_DIR)))
        self._cache: dict[str, Any] = {}
        # Created here rather than by each adapter: they open files, and a
        # missing directory would otherwise surface as `unable to open database
        # file` from whichever concern happened to be built first.
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str, default_file: str) -> str:
        """A configured path for one database, or a file under the data dir."""
        override = self._config.get(f"{name}_path")
        if override:
            return str(override)
        return str(self._data_dir / default_file)

    def _cached(self, key: str, build: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # -- the concerns ------------------------------------------------------

    def event_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

            return SQLiteEventStore(self._path_for("event_store", "events.db"))

        return self._cached("event_store", build)

    def dispatch_checkpoint(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.dispatch_checkpoint import SqliteDispatchCheckpoint

            # Deliberately the same file as the event store: the mark is only
            # meaningful against the log it points into, and splitting them
            # across databases invites a mark that outlives its events.
            return SqliteDispatchCheckpoint(self._path_for("event_store", "events.db"))

        return self._cached("dispatch_checkpoint", build)

    def _database(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

            return Database(DatabaseConfig(path=self._path_for("database", "mcp_hangar.db")))

        return self._cached("database", build)

    def config_repository(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository

            return SQLiteMcpServerConfigRepository(self._database())

        return self._cached("config_repository", build)

    def audit_repository(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.audit_repository import SQLiteAuditRepository

            return SQLiteAuditRepository(self._database())

        return self._cached("audit_repository", build)

    def saga_state_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.database_common import (
                SQLiteConfig,
                SQLiteConnectionFactory,
            )
            from mcp_hangar.infrastructure.persistence.saga_state_store import SagaStateStore

            factory = SQLiteConnectionFactory(SQLiteConfig(path=self._path_for("saga_state", "saga_state.db")))
            return SagaStateStore(factory)

        return self._cached("saga_state_store", build)

    def approval_repository(self) -> Any:
        def build() -> Any:
            from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository

            return SqliteApprovalRepository(self._database())

        return self._cached("approval_repository", build)

    # Both keep schema creation in `initialize()` rather than doing it on first
    # use, and the branch that hands them out never called it -- so an
    # auth-enabled gateway on a selected backend died at startup with
    # `no such table: roles`. Only these two need the call:
    # `SQLiteToolAccessPolicyStore` creates its schema in `__init__`, which is
    # why that one never showed the fault.

    def api_key_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

            store = SQLiteApiKeyStore(self._path_for("auth", "auth.db"))
            store.initialize()
            return store

        return self._cached("api_key_store", build)

    def role_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

            store = SQLiteRoleStore(self._path_for("auth", "auth.db"))
            store.initialize()
            return store

        return self._cached("role_store", build)

    def tool_access_policy_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore

            return SQLiteToolAccessPolicyStore(self._path_for("auth", "auth.db"))

        return self._cached("tool_access_policy_store", build)

    def metrics_history_store(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.database_common import SQLiteConfig
            from mcp_hangar.infrastructure.persistence.metrics_history_store import MetricsHistoryStore

            return MetricsHistoryStore(SQLiteConfig(path=self._path_for("metrics", "mcp_hangar.db")))

        return self._cached("metrics_history_store", build)

    def management_lease(self) -> Any:
        def build() -> Any:
            from mcp_hangar.infrastructure.persistence.sqlite_management_lease import SQLiteManagementLease

            # Same file as the fleet it gates. A lease in one file and the
            # servers it manages in another can be restored independently, and
            # a generation restored from an older copy is a fencing token that
            # fences the wrong tenure.
            return SQLiteManagementLease(self._path_for("lease", "mcp_hangar.db"))

        return self._cached("management_lease", build)

    def close(self) -> None:
        """Release whatever was opened. Safe to call when nothing was."""
        import inspect

        for name, adapter in self._cache.items():
            closer = getattr(adapter, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    # `Database.close` is async. Calling it and dropping the
                    # coroutine closed nothing and said so only as a
                    # RuntimeWarning, which nobody reads during shutdown.
                    _shutdown_loop().run(result, 10.0)
            except Exception as e:  # noqa: BLE001 -- shutdown must not fail on one stubborn handle
                logger.warning("persistence_close_failed", concern=name, error=str(e))
        self._cache.clear()
