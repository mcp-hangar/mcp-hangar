"""Audit repository implementations.

Provides both in-memory and SQLite implementations of IAuditRepository.
Audit logs are append-only for integrity.
"""

from datetime import datetime
import json
import threading
from typing import List, Optional

from ...domain.contracts.persistence import (
    AuditAction,
    AuditEntry,
    IAuditRepository,
    PersistenceError,
)
from ...logging_config import get_logger
from .database import Database

logger = get_logger(__name__)


class InMemoryAuditRepository:
    """In-memory implementation of audit repository.

    Useful for testing and development. Data is lost on restart.
    Maintains append-only semantics.
    """

    def __init__(self, max_entries: int = 100000):
        """Initialize empty in-memory audit repository.

        Args:
            max_entries: Maximum entries to retain (oldest dropped when exceeded)
        """
        self._entries: List[AuditEntry] = []
        self._max_entries = max_entries
        self._lock = threading.RLock()

    async def append(self, entry: AuditEntry) -> None:
        """Append an audit entry."""
        with self._lock:
            self._entries.append(entry)

            # Prune old entries if exceeded
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]

            logger.debug(
                f"Audit: {entry.action.value} on {entry.entity_type}/{entry.entity_id} "
                f"by {entry.actor}"
            )

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEntry]:
        """Get audit entries for an entity."""
        with self._lock:
            filtered = [
                e
                for e in self._entries
                if e.entity_id == entity_id
                and (entity_type is None or e.entity_type == entity_type)
            ]
            # Return newest first
            filtered.sort(key=lambda e: e.timestamp, reverse=True)
            return filtered[offset : offset + limit]

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: Optional[str] = None,
        action: Optional[AuditAction] = None,
        limit: int = 1000,
    ) -> List[AuditEntry]:
        """Get audit entries within a time range."""
        with self._lock:
            filtered = [
                e
                for e in self._entries
                if start <= e.timestamp <= end
                and (entity_type is None or e.entity_type == entity_type)
                and (action is None or e.action == action)
            ]
            filtered.sort(key=lambda e: e.timestamp, reverse=True)
            return filtered[:limit]

    async def get_by_correlation_id(self, correlation_id: str) -> List[AuditEntry]:
        """Get all audit entries for a correlation ID."""
        with self._lock:
            filtered = [
                e for e in self._entries if e.correlation_id == correlation_id
            ]
            filtered.sort(key=lambda e: e.timestamp)
            return filtered

    def clear(self) -> None:
        """Clear all entries (for testing)."""
        with self._lock:
            self._entries.clear()


class SQLiteAuditRepository:
    """SQLite implementation of audit repository.

    Provides durable, append-only audit log storage with
    efficient querying capabilities.
    """

    def __init__(self, database: Database):
        """Initialize with database connection.

        Args:
            database: Database instance for connections
        """
        self._db = database

    async def append(self, entry: AuditEntry) -> None:
        """Append an audit entry.

        Args:
            entry: Audit entry to append

        Raises:
            PersistenceError: If append operation fails
        """
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_log 
                    (entity_id, entity_type, action, actor, timestamp,
                     old_state_json, new_state_json, metadata_json, correlation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.entity_id,
                        entry.entity_type,
                        entry.action.value,
                        entry.actor,
                        entry.timestamp.isoformat(),
                        json.dumps(entry.old_state) if entry.old_state else None,
                        json.dumps(entry.new_state) if entry.new_state else None,
                        json.dumps(entry.metadata) if entry.metadata else None,
                        entry.correlation_id,
                    ),
                )

            logger.debug(
                f"Audit: {entry.action.value} on {entry.entity_type}/{entry.entity_id} "
                f"by {entry.actor}"
            )

        except Exception as e:
            logger.error(f"Failed to append audit entry: {e}")
            raise PersistenceError(f"Failed to append audit entry: {e}") from e

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEntry]:
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
            async with self._db.connection() as conn:
                if entity_type:
                    cursor = await conn.execute(
                        """
                        SELECT entity_id, entity_type, action, actor, timestamp,
                               old_state_json, new_state_json, metadata_json, correlation_id
                        FROM audit_log
                        WHERE entity_id = ? AND entity_type = ?
                        ORDER BY timestamp DESC
                        LIMIT ? OFFSET ?
                        """,
                        (entity_id, entity_type, limit, offset),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT entity_id, entity_type, action, actor, timestamp,
                               old_state_json, new_state_json, metadata_json, correlation_id
                        FROM audit_log
                        WHERE entity_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ? OFFSET ?
                        """,
                        (entity_id, limit, offset),
                    )

                rows = await cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get audit entries by entity: {e}")
            raise PersistenceError(
                f"Failed to get audit entries by entity: {e}"
            ) from e

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: Optional[str] = None,
        action: Optional[AuditAction] = None,
        limit: int = 1000,
    ) -> List[AuditEntry]:
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
            async with self._db.connection() as conn:
                query = """
                    SELECT entity_id, entity_type, action, actor, timestamp,
                           old_state_json, new_state_json, metadata_json, correlation_id
                    FROM audit_log
                    WHERE timestamp BETWEEN ? AND ?
                """
                params: List = [start.isoformat(), end.isoformat()]

                if entity_type:
                    query += " AND entity_type = ?"
                    params.append(entity_type)

                if action:
                    query += " AND action = ?"
                    params.append(action.value)

                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                cursor = await conn.execute(query, params)
                rows = await cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get audit entries by time range: {e}")
            raise PersistenceError(
                f"Failed to get audit entries by time range: {e}"
            ) from e

    async def get_by_correlation_id(self, correlation_id: str) -> List[AuditEntry]:
        """Get all audit entries for a correlation ID.

        Args:
            correlation_id: Correlation identifier

        Returns:
            List of related audit entries, ordered by timestamp
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT entity_id, entity_type, action, actor, timestamp,
                           old_state_json, new_state_json, metadata_json, correlation_id
                    FROM audit_log
                    WHERE correlation_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (correlation_id,),
                )

                rows = await cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get audit entries by correlation: {e}")
            raise PersistenceError(
                f"Failed to get audit entries by correlation: {e}"
            ) from e

    async def count_by_entity(
        self, entity_id: str, entity_type: Optional[str] = None
    ) -> int:
        """Count audit entries for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Optional entity type filter

        Returns:
            Number of audit entries
        """
        try:
            async with self._db.connection() as conn:
                if entity_type:
                    cursor = await conn.execute(
                        """
                        SELECT COUNT(*) FROM audit_log
                        WHERE entity_id = ? AND entity_type = ?
                        """,
                        (entity_id, entity_type),
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE entity_id = ?",
                        (entity_id,),
                    )

                row = await cursor.fetchone()
                return row[0] if row else 0

        except Exception as e:
            logger.error(f"Failed to count audit entries: {e}")
            raise PersistenceError(f"Failed to count audit entries: {e}") from e

    async def get_recent_actions(
        self,
        entity_type: str,
        action: AuditAction,
        limit: int = 100,
    ) -> List[AuditEntry]:
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
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT entity_id, entity_type, action, actor, timestamp,
                           old_state_json, new_state_json, metadata_json, correlation_id
                    FROM audit_log
                    WHERE entity_type = ? AND action = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (entity_type, action.value, limit),
                )

                rows = await cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get recent actions: {e}")
            raise PersistenceError(f"Failed to get recent actions: {e}") from e

    def _row_to_entry(self, row) -> AuditEntry:
        """Convert database row to AuditEntry.

        Args:
            row: Database row (sqlite3.Row or tuple)

        Returns:
            AuditEntry instance
        """
        return AuditEntry(
            entity_id=row[0],
            entity_type=row[1],
            action=AuditAction(row[2]),
            actor=row[3],
            timestamp=datetime.fromisoformat(row[4]),
            old_state=json.loads(row[5]) if row[5] else None,
            new_state=json.loads(row[6]) if row[6] else None,
            metadata=json.loads(row[7]) if row[7] else {},
            correlation_id=row[8],
        )

