"""Saga State Store for persisting saga state to SQLite.

Provides durable persistence for EventTriggeredSaga state so that
in-progress recovery and failover state survives process restarts.
"""

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from .database_common import IConnectionFactory, MigrationRunner

logger = structlog.get_logger(__name__)

SAGA_STORE_MIGRATIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "name": "create_saga_state_tables",
        "sql": """
            CREATE TABLE IF NOT EXISTS saga_state (
                saga_type TEXT NOT NULL,
                saga_id TEXT NOT NULL,
                state_data TEXT NOT NULL,
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


class SagaStateStore:
    """Persists saga state to SQLite for crash recovery.

    Uses SQLiteConnectionFactory and MigrationRunner from database_common
    for consistent database access patterns.

    Methods:
        checkpoint: Save saga state after successful handle().
        load: Retrieve the last saved state for a saga type.
        mark_processed: Record an event position as processed (idempotency).
        is_processed: Check if an event position was already processed.
    """

    def __init__(self, connection_factory: IConnectionFactory) -> None:
        """Initialize SagaStateStore.

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

        Uses INSERT OR REPLACE to overwrite previous state for the
        same saga_type + saga_id combination.

        Args:
            saga_type: The saga type identifier.
            saga_id: The saga instance identifier.
            state_data: Serialized saga state (will be JSON-encoded).
            last_event_position: The global event position processed.
        """
        with self._conn_factory.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO saga_state "
                "(saga_type, saga_id, state_data, last_event_position, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    saga_type,
                    saga_id,
                    json.dumps(state_data),
                    last_event_position,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
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
            cursor = conn.execute(
                "SELECT state_data, last_event_position FROM saga_state WHERE saga_type = ?",
                (saga_type,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            "state_data": json.loads(row[0]),
            "last_event_position": row[1],
        }

    def mark_processed(self, saga_type: str, event_position: int) -> None:
        """Record an event position as processed for idempotency.

        Uses INSERT OR IGNORE so duplicate calls are safe.

        Args:
            saga_type: The saga type identifier.
            event_position: The global event position to mark.
        """
        with self._conn_factory.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO saga_idempotency (saga_type, event_position, processed_at) VALUES (?, ?, ?)",
                (
                    saga_type,
                    event_position,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def is_processed(self, saga_type: str, event_position: int) -> bool:
        """Check if an event position was already processed.

        Args:
            saga_type: The saga type identifier.
            event_position: The global event position to check.

        Returns:
            True if the position was already processed, False otherwise.
        """
        with self._conn_factory.get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM saga_idempotency WHERE saga_type = ? AND event_position = ?",
                (saga_type, event_position),
            )
            return cursor.fetchone() is not None


class NullSagaStateStore:
    """Null object implementation of saga state store.

    All methods are no-ops. Used when saga persistence is not configured.
    """

    def checkpoint(
        self,
        saga_type: str,
        saga_id: str,
        state_data: dict[str, Any],
        last_event_position: int,
    ) -> None:
        """No-op checkpoint."""

    def load(self, saga_type: str) -> dict[str, Any] | None:
        """Always returns None."""
        return None

    def mark_processed(self, saga_type: str, event_position: int) -> None:
        """No-op mark_processed."""

    def is_processed(self, saga_type: str, event_position: int) -> bool:
        """Always returns False."""
        return False
