"""PostgreSQL adapter for `IAuditRepository`.

The audit log is the record of who did what to which entity, and it is meant
to survive the process that wrote it -- that is the whole reason it exists
instead of just logging. This adapter gives it a durable home when PostgreSQL
is the configured backend, mirroring `SQLiteAuditRepository` so both backends
answer the same identity-aware and time-range queries the rest of the system
depends on.

Requires: psycopg2 (installed by the `postgres` extra). This module never
imports it directly -- all connections come from the shared
`IConnectionFactory` (see `infrastructure.persistence.database_common`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any

import structlog

from mcp_hangar.domain.contracts.persistence import AuditAction, AuditEntry, IAuditRepository, PersistenceError
from mcp_hangar.domain.security.secrets import SecretsMask
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory

logger = structlog.get_logger(__name__)

# Old/new state are arbitrary application state, not audit metadata -- they can
# carry the same secrets the entity itself carries (API keys, tokens embedded
# in a config). Masked before it ever reaches SQL so a durable audit trail
# never becomes a second place secrets leak from, same as the SQLite reference.
_AUDIT_SECRETS_MASK = SecretsMask()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    old_state_json JSONB,
    new_state_json JSONB,
    metadata_json JSONB,
    correlation_id TEXT,
    caller_user_id TEXT,
    caller_agent_id TEXT,
    caller_session_id TEXT,
    caller_principal_type TEXT
);

CREATE INDEX IF NOT EXISTS idx_{table}_entity ON {table}(entity_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_{table}_timestamp ON {table}(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_{table}_correlation ON {table}(correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_{table}_action ON {table}(action, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_{table}_actor ON {table}(actor, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_{table}_caller_user ON {table}(caller_user_id, timestamp DESC)
    WHERE caller_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_{table}_caller_agent ON {table}(caller_agent_id, timestamp DESC)
    WHERE caller_agent_id IS NOT NULL;
"""

_SELECT_COLUMNS = """
    entity_id, entity_type, action, actor, timestamp,
    old_state_json, new_state_json, metadata_json, correlation_id,
    caller_user_id, caller_agent_id, caller_session_id, caller_principal_type
"""


def _decode_json(value, default=None):
    """Decode a JSONB column value.

    psycopg2 decodes JSONB columns to Python objects (dict/list) by default,
    but a raw or differently configured cursor may still hand back the JSON
    text -- decode defensively either way, same as the tool-access-policy
    store does for its own JSONB columns.
    """
    if not value:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresAuditRepository(IAuditRepository):
    """PostgreSQL-backed, append-only audit log.

    Timestamps are stored as the same ISO-8601 text `SQLiteAuditRepository`
    writes, not `TIMESTAMPTZ` -- comparisons and round-trips (via
    `datetime.fromisoformat`) then behave identically on both backends instead
    of picking up Postgres's own timestamp semantics.
    """

    def __init__(self, connection_factory: IConnectionFactory, table_prefix: str = "") -> None:
        """Initialize and create the table if it is missing.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This repository
                knows SQL; it deliberately does not know psycopg2, pooling, or
                how a connection is obtained.
            table_prefix: Optional prefix for the table name.
        """
        self._connections = connection_factory
        self._table = f"{table_prefix}audit_log" if table_prefix else "audit_log"
        with self._cursor() as (conn, cur):
            cur.execute(_SCHEMA.format(table=self._table))
            conn.commit()

    @contextmanager
    def _cursor(self) -> Generator[tuple[Any, Any], None, None]:
        """Borrow a pooled connection/cursor, rolling back on any failure.

        `IConnectionFactory.get_connection()` returns the connection to the
        pool in a bare `finally` regardless of transaction state (see
        `PostgresConnectionFactory.get_connection`). Without an explicit
        rollback here, a statement that raises mid-transaction (a bad
        INSERT, a dropped connection, a lock timeout) leaves the connection
        in Postgres's "current transaction is aborted" state, and it goes
        back into the pool exactly like that -- poisoning the *next*,
        unrelated caller who borrows it, for both reads and writes. Same
        concern `PostgresToolAccessPolicyStore` documents and guards against
        on its own writes; every operation here (reads included, since a
        failed SELECT aborts the transaction too) needs the same guard.
        """
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    yield conn, cur
            except Exception:
                conn.rollback()
                raise

    def _append_sync(self, entry: AuditEntry) -> None:
        with self._cursor() as (conn, cur):
            cur.execute(
                f"""
                INSERT INTO {self._table}
                (entity_id, entity_type, action, actor, timestamp,
                 old_state_json, new_state_json, metadata_json, correlation_id,
                 caller_user_id, caller_agent_id, caller_session_id, caller_principal_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.entity_id,
                    entry.entity_type,
                    entry.action.value,
                    entry.actor,
                    entry.timestamp.isoformat(),
                    json.dumps(_AUDIT_SECRETS_MASK.mask_dict(entry.old_state, recursive=True))
                    if entry.old_state
                    else None,
                    json.dumps(_AUDIT_SECRETS_MASK.mask_dict(entry.new_state, recursive=True))
                    if entry.new_state
                    else None,
                    json.dumps(entry.metadata) if entry.metadata else None,
                    entry.correlation_id,
                    entry.caller_user_id,
                    entry.caller_agent_id,
                    entry.caller_session_id,
                    entry.caller_principal_type,
                ),
            )
            conn.commit()

    async def append(self, entry: AuditEntry) -> None:
        """Append an audit entry.

        Args:
            entry: Audit entry to append

        Raises:
            PersistenceError: If append operation fails
        """
        try:
            # psycopg2 is a blocking driver -- run the round-trip in a worker
            # thread so this coroutine actually yields instead of stalling
            # the whole event loop (and every other in-flight request) for
            # the duration of the INSERT, the way the reference's aiosqlite
            # connection does natively.
            await asyncio.to_thread(self._append_sync, entry)

            logger.debug(
                "audit_appended",
                action=entry.action.value,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                actor=entry.actor,
            )

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_append_failed", error=str(e))
            raise PersistenceError(f"Failed to append audit entry: {e}") from e

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Optional entity type filter
            limit: Maximum entries to return
            offset: Number of entries to skip

        Returns:
            List of audit entries, newest first
        """
        try:
            return await asyncio.to_thread(self._get_by_entity_sync, entity_id, entity_type, limit, offset)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_get_by_entity_failed", error=str(e))
            raise PersistenceError(f"Failed to get audit entries by entity: {e}") from e

    def _get_by_entity_sync(self, entity_id: str, entity_type: str | None, limit: int, offset: int) -> list[AuditEntry]:
        with self._cursor() as (_conn, cur):
            if entity_type:
                cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM {self._table}
                    WHERE entity_id = %s AND entity_type = %s
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s
                    """,
                    (entity_id, entity_type, limit, offset),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM {self._table}
                    WHERE entity_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s
                    """,
                    (entity_id, limit, offset),
                )

            rows = cur.fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: str | None = None,
        action: AuditAction | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        """Get audit entries within a time range.

        Args:
            start: Start of time range (inclusive)
            end: End of time range (inclusive)
            entity_type: Optional entity type filter
            action: Optional action filter
            limit: Maximum entries to return

        Returns:
            List of audit entries, newest first
        """
        try:
            return await asyncio.to_thread(self._get_by_time_range_sync, start, end, entity_type, action, limit)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_get_by_time_range_failed", error=str(e))
            raise PersistenceError(f"Failed to get audit entries by time range: {e}") from e

    def _get_by_time_range_sync(
        self,
        start: datetime,
        end: datetime,
        entity_type: str | None,
        action: AuditAction | None,
        limit: int,
    ) -> list[AuditEntry]:
        with self._cursor() as (_conn, cur):
            query = f"""
                SELECT {_SELECT_COLUMNS}
                FROM {self._table}
                WHERE timestamp BETWEEN %s AND %s
            """
            params: list[str | int] = [start.isoformat(), end.isoformat()]

            if entity_type:
                query += " AND entity_type = %s"
                params.append(entity_type)

            if action:
                query += " AND action = %s"
                params.append(action.value)

            query += " ORDER BY timestamp DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_by_correlation_id(self, correlation_id: str) -> list[AuditEntry]:
        """Get all audit entries for a correlation ID.

        Useful for tracing distributed operations.

        Args:
            correlation_id: Correlation identifier

        Returns:
            List of related audit entries, ordered by timestamp
        """
        try:
            return await asyncio.to_thread(self._get_by_correlation_id_sync, correlation_id)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_get_by_correlation_failed", error=str(e))
            raise PersistenceError(f"Failed to get audit entries by correlation: {e}") from e

    def _get_by_correlation_id_sync(self, correlation_id: str) -> list[AuditEntry]:
        with self._cursor() as (_conn, cur):
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM {self._table}
                WHERE correlation_id = %s
                ORDER BY timestamp ASC
                """,
                (correlation_id,),
            )

            rows = cur.fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def count_by_entity(self, entity_id: str, entity_type: str | None = None) -> int:
        """Count audit entries for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Optional entity type filter

        Returns:
            Number of audit entries
        """
        try:
            return await asyncio.to_thread(self._count_by_entity_sync, entity_id, entity_type)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_count_by_entity_failed", error=str(e))
            raise PersistenceError(f"Failed to count audit entries: {e}") from e

    def _count_by_entity_sync(self, entity_id: str, entity_type: str | None) -> int:
        with self._cursor() as (_conn, cur):
            if entity_type:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {self._table}
                    WHERE entity_id = %s AND entity_type = %s
                    """,
                    (entity_id, entity_type),
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) FROM {self._table} WHERE entity_id = %s",
                    (entity_id,),
                )

            row = cur.fetchone()
            return row[0] if row else 0

    async def get_recent_actions(
        self,
        entity_type: str,
        action: AuditAction,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Get recent actions of a specific type.

        Useful for monitoring and dashboards.

        Args:
            entity_type: Entity type to filter
            action: Action type to filter
            limit: Maximum entries to return

        Returns:
            List of recent audit entries
        """
        try:
            return await asyncio.to_thread(self._get_recent_actions_sync, entity_type, action, limit)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_get_recent_actions_failed", error=str(e))
            raise PersistenceError(f"Failed to get recent actions: {e}") from e

    def _get_recent_actions_sync(self, entity_type: str, action: AuditAction, limit: int) -> list[AuditEntry]:
        with self._cursor() as (_conn, cur):
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM {self._table}
                WHERE entity_type = %s AND action = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (entity_type, action.value, limit),
            )

            rows = cur.fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_by_caller(
        self,
        caller_user_id: str,
        action: AuditAction | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries for a specific caller.

        Enables identity-aware audit queries (e.g. "what did user X do?").

        Args:
            caller_user_id: Caller user identifier
            action: Optional action filter
            limit: Maximum entries to return
            offset: Number of entries to skip

        Returns:
            List of audit entries, newest first
        """
        try:
            return await asyncio.to_thread(self._get_by_caller_sync, caller_user_id, action, limit, offset)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error("audit_get_by_caller_failed", error=str(e))
            raise PersistenceError(f"Failed to get audit entries by caller: {e}") from e

    def _get_by_caller_sync(
        self, caller_user_id: str, action: AuditAction | None, limit: int, offset: int
    ) -> list[AuditEntry]:
        with self._cursor() as (_conn, cur):
            query = f"""
                SELECT {_SELECT_COLUMNS}
                FROM {self._table}
                WHERE caller_user_id = %s
            """
            params: list[str | int] = [caller_user_id]

            if action:
                query += " AND action = %s"
                params.append(action.value)

            query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row) -> AuditEntry:
        """Convert a database row to an `AuditEntry`.

        Args:
            row: Database row (tuple), in `_SELECT_COLUMNS` order.

        Returns:
            AuditEntry instance
        """
        return AuditEntry(
            entity_id=row[0],
            entity_type=row[1],
            action=AuditAction(row[2]),
            actor=row[3],
            timestamp=datetime.fromisoformat(row[4]),
            old_state=_decode_json(row[5]),
            new_state=_decode_json(row[6]),
            metadata=_decode_json(row[7], default={}),
            correlation_id=row[8],
            caller_user_id=row[9],
            caller_agent_id=row[10],
            caller_session_id=row[11],
            caller_principal_type=row[12],
        )
