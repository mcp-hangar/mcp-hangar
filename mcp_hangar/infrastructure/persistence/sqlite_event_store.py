"""SQLite-based Event Store implementation.

Provides durable event persistence suitable for single-node deployments.
For distributed systems, consider PostgreSQL or EventStoreDB.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.logging_config import get_logger

from .event_serializer import EventSerializer

logger = get_logger(__name__)


class SQLiteEventStore(IEventStore):
    """SQLite-based event store with optimistic concurrency.

    Thread-safe implementation suitable for single-node deployments.

    Features:
    - Append-only event storage
    - Optimistic concurrency control via version checks
    - Global ordering across all streams
    - Efficient stream reads with indexing

    Schema:
    - events: Main event table with global ordering
    - streams: Track stream versions for concurrency control
    """

    def __init__(self, db_path: str | Path = ":memory:", *, serializer: EventSerializer | None = None):
        """Initialize SQLite event store.

        Args:
            db_path: Path to SQLite database file.
                Use ":memory:" for in-memory store (testing).
            serializer: Optional EventSerializer instance. Allows injecting an upcaster-aware serializer.
        """
        self._db_path = str(db_path)
        self._serializer = serializer or EventSerializer()
        self._lock = threading.Lock()
        self._is_memory = self._db_path == ":memory:"

        # For in-memory database, keep a persistent connection
        # (each new connection to :memory: creates a NEW database)
        self._persistent_conn: sqlite3.Connection | None = None
        if self._is_memory:
            self._persistent_conn = self._create_connection()

        self._init_schema()

        logger.info(
            "sqlite_event_store_initialized",
            db_path=self._db_path,
            in_memory=self._is_memory,
        )

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._connect()
        try:
            conn.executescript(
                """
                -- Main events table
                CREATE TABLE IF NOT EXISTS events (
                    global_position INTEGER PRIMARY KEY AUTOINCREMENT,
                    stream_id TEXT NOT NULL,
                    stream_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(stream_id, stream_version)
                );

                -- Index for efficient stream reads
                CREATE INDEX IF NOT EXISTS idx_events_stream
                ON events(stream_id, stream_version);

                -- Index for global reads (projections)
                CREATE INDEX IF NOT EXISTS idx_events_global
                ON events(global_position);

                -- Stream version tracking for optimistic concurrency
                CREATE TABLE IF NOT EXISTS streams (
                    stream_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT -1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """
            )
        finally:
            if not self._is_memory:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        """Get database connection.

        For in-memory databases, returns the persistent connection.
        For file-based databases, creates a new connection.
        """
        if self._is_memory and self._persistent_conn:
            return self._persistent_conn
        return self._create_connection()

    def append(
        self,
        stream_id: str,
        events: list[DomainEvent],
        expected_version: int,
    ) -> int:
        """Append events to a stream with optimistic concurrency.

        Args:
            stream_id: Stream identifier (e.g., "provider:math").
            events: Events to append.
            expected_version: Expected current version (-1 for new stream).

        Returns:
            New stream version after append.

        Raises:
            ConcurrencyError: If version mismatch.
        """
        if not events:
            return expected_version

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                timestamp = datetime.now(timezone.utc).isoformat()

                # Check current version
                cursor.execute(
                    "SELECT version FROM streams WHERE stream_id = ?",
                    (stream_id,),
                )
                row = cursor.fetchone()
                current_version = row["version"] if row else -1

                if current_version != expected_version:
                    raise ConcurrencyError(stream_id, expected_version, current_version)

                # Append events
                new_version = current_version
                for event in events:
                    new_version += 1
                    event_type, data = self._serializer.serialize(event)

                    cursor.execute(
                        """
                        INSERT INTO events
                        (stream_id, stream_version, event_type, data, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (stream_id, new_version, event_type, data, timestamp),
                    )

                # Update or insert stream version
                if current_version == -1:
                    cursor.execute(
                        """
                        INSERT INTO streams (stream_id, version, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (stream_id, new_version, timestamp, timestamp),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE streams SET version = ?, updated_at = ?
                        WHERE stream_id = ?
                        """,
                        (new_version, timestamp, stream_id),
                    )

                conn.commit()

                logger.debug(
                    "events_appended",
                    stream_id=stream_id,
                    events_count=len(events),
                    new_version=new_version,
                )

                return new_version

            except ConcurrencyError:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                logger.error(
                    "event_append_failed",
                    stream_id=stream_id,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def read_stream(
        self,
        stream_id: str,
        from_version: int = 0,
    ) -> list[DomainEvent]:
        """Read events from a stream.

        Args:
            stream_id: Stream identifier.
            from_version: Start version (inclusive).

        Returns:
            List of events in order. Empty if stream doesn't exist.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                SELECT event_type, data FROM events
                WHERE stream_id = ? AND stream_version >= ?
                ORDER BY stream_version ASC
                """,
                (stream_id, from_version),
            )

            events = []
            for row in cursor.fetchall():
                event = self._serializer.deserialize(row["event_type"], row["data"])
                events.append(event)

            logger.debug(
                "stream_read",
                stream_id=stream_id,
                from_version=from_version,
                events_count=len(events),
            )

            return events
        finally:
            if not self._is_memory:
                conn.close()

    def read_all(
        self,
        from_position: int = 0,
        limit: int = 1000,
    ) -> Iterator[tuple[int, str, DomainEvent]]:
        """Read all events across streams (for projections).

        Args:
            from_position: Start position (exclusive).
            limit: Maximum events to return.

        Yields:
            Tuples of (global_position, stream_id, event).
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                SELECT global_position, stream_id, event_type, data
                FROM events
                WHERE global_position > ?
                ORDER BY global_position ASC
                LIMIT ?
                """,
                (from_position, limit),
            )

            # Fetch all rows first to allow closing connection
            rows = cursor.fetchall()
        finally:
            if not self._is_memory:
                conn.close()

        for row in rows:
            event = self._serializer.deserialize(row["event_type"], row["data"])
            yield row["global_position"], row["stream_id"], event

    def get_stream_version(self, stream_id: str) -> int:
        """Get current version of a stream.

        Args:
            stream_id: Stream identifier.

        Returns:
            Current version, or -1 if stream doesn't exist.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT version FROM streams WHERE stream_id = ?",
                (stream_id,),
            )
            row = cursor.fetchone()
            return row["version"] if row else -1
        finally:
            if not self._is_memory:
                conn.close()

    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs in the store.

        Returns:
            List of stream identifiers.
        """
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT stream_id FROM streams ORDER BY stream_id")
            return [row["stream_id"] for row in cursor.fetchall()]
        finally:
            if not self._is_memory:
                conn.close()

    def get_event_count(self) -> int:
        """Get total number of events in the store.

        Returns:
            Total event count.
        """
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT COUNT(*) as count FROM events")
            row = cursor.fetchone()
            return row["count"] if row else 0
        finally:
            if not self._is_memory:
                conn.close()

    def get_stream_count(self) -> int:
        """Get total number of streams.

        Returns:
            Total stream count.
        """
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT COUNT(*) as count FROM streams")
            row = cursor.fetchone()
            return row["count"] if row else 0
        finally:
            if not self._is_memory:
                conn.close()

    def list_streams(self, prefix: str = "") -> list[str]:
        """List all stream IDs, optionally filtered by prefix.

        Args:
            prefix: Optional prefix to filter streams.

        Returns:
            List of stream IDs matching the prefix.
        """
        conn = self._connect()
        try:
            if prefix:
                cursor = conn.execute(
                    "SELECT stream_id FROM streams WHERE stream_id LIKE ? ORDER BY stream_id",
                    (f"{prefix}%",),
                )
            else:
                cursor = conn.execute("SELECT stream_id FROM streams ORDER BY stream_id")
            return [row["stream_id"] for row in cursor.fetchall()]
        finally:
            if not self._is_memory:
                conn.close()
