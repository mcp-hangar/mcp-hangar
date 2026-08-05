"""Saga State Store for persisting saga state to PostgreSQL.

Mirrors `infrastructure.persistence.saga_state_store.SagaStateStore` so that a
saga's checkpoint and idempotency behaviour is identical whether the process
is backed by a single SQLite file or a PostgreSQL cluster -- a saga recovering
after a restart or a failover must not care which one it landed on.
"""

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory, MigrationRunner
from mcp_hangar.domain.contracts.saga_state import ISagaStateStore

logger = structlog.get_logger(__name__)

SAGA_STORE_MIGRATIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "name": "create_saga_state_tables",
        "sql": """
            CREATE TABLE IF NOT EXISTS saga_state (
                saga_type TEXT NOT NULL,
                saga_id TEXT NOT NULL,
                state_data JSONB NOT NULL,
                last_event_position INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (saga_type, saga_id)
            );

            CREATE TABLE IF NOT EXISTS saga_idempotency (
                saga_type TEXT NOT NULL,
                event_position INTEGER NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (saga_type, event_position)
            );
        """,
    },
]


class PostgresSagaStateStore(ISagaStateStore):
    """Persists saga state to PostgreSQL for crash recovery.

    Uses the shared `IConnectionFactory` and `MigrationRunner` from
    `database_common` rather than opening a connection or a pool itself --
    one place holds the PostgreSQL-specific knowledge, and it is the factory.

    `state_data` is stored as JSONB (the SQLite reference stores it as TEXT)
    so it stays queryable from the database side, but the Python-facing
    contract is unchanged: callers pass and receive a plain dict, and psycopg2
    may hand JSONB columns back already decoded, so reads tolerate both a
    dict and a JSON string.

    Methods:
        checkpoint: Save saga state after successful handle().
        load: Retrieve the last saved state for a saga type.
        mark_processed: Record an event position as processed (idempotency).
        is_processed: Check if an event position was already processed.
    """

    def __init__(self, connection_factory: IConnectionFactory) -> None:
        """Initialize PostgresSagaStateStore.

        Args:
            connection_factory: Factory for database connections.
        """
        self._conn_factory = connection_factory
        runner = MigrationRunner(
            connection_factory,
            SAGA_STORE_MIGRATIONS,
            table_name="saga_state_migrations",
        )
        applied = runner.run()
        if applied > 0:
            logger.info("saga_state_store_migrations_applied", count=applied)

    def checkpoint(
        self,
        saga_type: str,
        saga_id: str,
        state_data: dict[str, Any],
        last_event_position: int,
    ) -> None:
        """Save saga state after successful handle().

        Uses INSERT ... ON CONFLICT DO UPDATE to overwrite previous state for
        the same saga_type + saga_id combination -- the PostgreSQL equivalent
        of the reference's INSERT OR REPLACE.

        Args:
            saga_type: The saga type identifier.
            saga_id: The saga instance identifier.
            state_data: Serialized saga state (will be JSON-encoded).
            last_event_position: The global event position processed.
        """
        with self._conn_factory.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO saga_state
                            (saga_type, saga_id, state_data, last_event_position, updated_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (saga_type, saga_id) DO UPDATE SET
                            state_data = EXCLUDED.state_data,
                            last_event_position = EXCLUDED.last_event_position,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            saga_type,
                            saga_id,
                            json.dumps(state_data),
                            last_event_position,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                conn.commit()
            except Exception:
                # `IConnectionFactory.get_connection()` returns a pooled
                # connection to the pool in `finally` regardless of
                # transaction state -- without an explicit rollback here, a
                # failed write leaves the connection in an aborted
                # transaction that poisons the *next* caller to borrow it
                # (any concurrent writer), not just this one.
                conn.rollback()
                raise
            logger.debug(
                "saga_state_checkpointed",
                saga_type=saga_type,
                saga_id=saga_id,
                last_event_position=last_event_position,
            )

    def load(self, saga_type: str) -> dict[str, Any] | None:
        """Load the last saved state for a saga type.

        Args:
            saga_type: The saga type identifier.

        Returns:
            Dict with "state_data" and "last_event_position", or None if not found.
        """
        with self._conn_factory.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT state_data, last_event_position FROM saga_state WHERE saga_type = %s",
                        (saga_type,),
                    )
                    row = cur.fetchone()
            except Exception:
                # See checkpoint() -- a pooled connection returned without a
                # rollback stays in an aborted transaction for whichever
                # concurrent writer borrows it next, even after a failed read.
                conn.rollback()
                raise

        if row is None:
            return None

        state_data = row[0]
        if isinstance(state_data, str):
            # psycopg2 decodes JSONB automatically in the common case, but
            # falls back to a raw string under some cursor/type configurations.
            state_data = json.loads(state_data)

        return {
            "state_data": state_data,
            "last_event_position": row[1],
        }

    def mark_processed(self, saga_type: str, event_position: int) -> None:
        """Record an event position as processed for idempotency.

        Uses INSERT ... ON CONFLICT DO NOTHING so duplicate calls are safe --
        the PostgreSQL equivalent of the reference's INSERT OR IGNORE.

        Args:
            saga_type: The saga type identifier.
            event_position: The global event position to mark.
        """
        with self._conn_factory.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO saga_idempotency (saga_type, event_position, processed_at) "
                        "VALUES (%s, %s, %s) ON CONFLICT (saga_type, event_position) DO NOTHING",
                        (
                            saga_type,
                            event_position,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                conn.commit()
            except Exception:
                # See checkpoint() -- a pooled connection returned without a
                # rollback stays in an aborted transaction for whichever
                # concurrent writer borrows it next.
                conn.rollback()
                raise

    def is_processed(self, saga_type: str, event_position: int) -> bool:
        """Check if an event position was already processed.

        Args:
            saga_type: The saga type identifier.
            event_position: The global event position to check.

        Returns:
            True if the position was already processed, False otherwise.
        """
        with self._conn_factory.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM saga_idempotency WHERE saga_type = %s AND event_position = %s",
                        (saga_type, event_position),
                    )
                    return cur.fetchone() is not None
            except Exception:
                # See checkpoint() -- a pooled connection returned without a
                # rollback stays in an aborted transaction for whichever
                # concurrent writer borrows it next, even after a failed read.
                conn.rollback()
                raise
