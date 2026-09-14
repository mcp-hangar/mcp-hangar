"""SQLite-based Event Store implementation.

Provides durable event persistence suitable for single-node deployments.
For distributed systems, consider PostgreSQL or EventStoreDB.
"""

from collections.abc import Iterator
from datetime import datetime, UTC
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, ClassVar

from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.exceptions import CompactionError
from mcp_hangar.logging_config import get_logger

from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

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

    #: Every append goes through `self._lock` and SQLite admits one writer at a
    #: time, so a row with a higher `AUTOINCREMENT` position also committed
    #: later. That is what makes the inherited position-based `read_since` sound
    #: here and unsound on PostgreSQL, where two appenders commit concurrently
    #: and out of allocation order. It is also the reason this backend is the
    #: standalone one: the property comes from there being a single writer,
    #: which is the same thing as there being a single node.
    positions_are_commit_ordered: ClassVar[bool] = True

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
        """Initialize database schema, under the lock like every other use of the shared connection."""
        with self._lock:
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

                -- Aggregate snapshots for bounded replay
                CREATE TABLE IF NOT EXISTS snapshots (
                    stream_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    state_data TEXT NOT NULL,
                    created_at TEXT NOT NULL
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

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Run one read and return every row it produced.

        A `:memory:` store has one connection for its whole life, because each
        new connection to `:memory:` is a new, empty database. Every thread
        therefore shares it, so this read takes `self._lock`, the same lock the
        appends take. Two reasons, and either alone is enough:

        * One `sqlite3.Connection` is not safe to drive from two threads at
          once. From Python 3.12 the connection's statement cache can hand the
          same prepared statement to both, and one resets it under the other:
          the reader gets `InterfaceError: bad parameter or other API misuse`,
          a short row, or `NULL` where a value was stored.
        * A connection has a single transaction. A read running inside another
          thread's `BEGIN IMMEDIATE` sees rows that `_append` may still roll
          back, and would return events that were never stored.

        The rows are fetched inside the lock and returned as a list: a live
        cursor would be read after the lock was released, which is the same
        unserialized use one step later.

        A file-backed store opens a connection per call, which no other thread
        touches, so it needs no lock.
        """
        if self._persistent_conn is not None:
            with self._lock:
                return self._persistent_conn.execute(sql, params).fetchall()

        conn = self._create_connection()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

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
        return self._append(stream_id, events, expected_version)

    def append_at_end(self, stream_id: str, events: list[DomainEvent]) -> int:
        """Append after whatever the stream holds, reading its version inside the write.

        See `_append`. The version is read in the same write transaction that
        appends the rows, so no other writer can move the stream in between.
        That holds for another thread and for another process opening the same
        file. A batch that claimed no version cannot conflict.
        """
        if not events:
            return self.get_stream_version(stream_id)
        return self._append(stream_id, events, None)

    def _append(self, stream_id: str, events: list[DomainEvent], expected_version: int | None) -> int:
        """Read the stream's version, check it if one was claimed, and write, all in one transaction.

        `BEGIN IMMEDIATE` takes SQLite's write lock before the version is read.
        `self._lock` only serializes the threads of this process, but the file
        can be open in another one. The version SELECT used to run outside any
        transaction, so another process could commit between the read and the
        insert. `expected_version=None` means "append at the end": the version
        just read is the one to write after.
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.cursor()
                timestamp = datetime.now(UTC).isoformat()

                # Check current version
                cursor.execute(
                    "SELECT version FROM streams WHERE stream_id = ?",
                    (stream_id,),
                )
                row = cursor.fetchone()
                current_version = row["version"] if row else -1

                if expected_version is not None and current_version != expected_version:
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
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
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
        rows = self._query(
            """
            SELECT event_type, data FROM events
            WHERE stream_id = ? AND stream_version >= ?
            ORDER BY stream_version ASC
            """,
            (stream_id, from_version),
        )

        events = [self._serializer.deserialize(row["event_type"], row["data"]) for row in rows]

        logger.debug(
            "stream_read",
            stream_id=stream_id,
            from_version=from_version,
            events_count=len(events),
        )

        return events

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
        # Every row is fetched before the first yield: the caller consumes this
        # iterator after the read returns, and `_query` holds the shared
        # connection's lock only for the read itself.
        rows = self._query(
            """
            SELECT global_position, stream_id, event_type, data
            FROM events
            WHERE global_position > ?
            ORDER BY global_position ASC
            LIMIT ?
            """,
            (from_position, limit),
        )

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
        rows = self._query(
            "SELECT version FROM streams WHERE stream_id = ?",
            (stream_id,),
        )
        return int(rows[0]["version"]) if rows else -1

    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs in the store.

        Returns:
            List of stream identifiers.
        """
        return [row["stream_id"] for row in self._query("SELECT stream_id FROM streams ORDER BY stream_id")]

    def get_event_count(self) -> int:
        """Get total number of events in the store.

        Returns:
            Total event count.
        """
        rows = self._query("SELECT COUNT(*) as count FROM events")
        return int(rows[0]["count"]) if rows else 0

    def get_stream_count(self) -> int:
        """Get total number of streams.

        Returns:
            Total stream count.
        """
        rows = self._query("SELECT COUNT(*) as count FROM streams")
        return int(rows[0]["count"]) if rows else 0

    def list_streams(self, prefix: str = "") -> list[str]:
        """List all stream IDs, optionally filtered by prefix.

        Args:
            prefix: Optional prefix to filter streams.

        Returns:
            List of stream IDs matching the prefix.
        """
        if prefix:
            rows = self._query(
                "SELECT stream_id FROM streams WHERE stream_id LIKE ? ORDER BY stream_id",
                (f"{prefix}%",),
            )
        else:
            rows = self._query("SELECT stream_id FROM streams ORDER BY stream_id")
        return [row["stream_id"] for row in rows]

    def save_snapshot(
        self,
        stream_id: str,
        version: int,
        state: dict[str, Any],
    ) -> None:
        """Save aggregate snapshot inside lock scope for version consistency.

        Args:
            stream_id: Stream identifier (matches event stream).
            version: Stream version this snapshot represents.
            state: Serialized aggregate state (must be JSON-serializable).
        """
        with self._lock:
            conn = self._connect()
            try:
                timestamp = datetime.now(UTC).isoformat()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO snapshots
                    (stream_id, version, state_data, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (stream_id, version, json.dumps(state), timestamp),
                )
                conn.commit()
                logger.debug(
                    "snapshot_saved",
                    stream_id=stream_id,
                    version=version,
                )
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                conn.rollback()
                logger.error("snapshot_save_failed", stream_id=stream_id, error=str(e))
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def load_snapshot(
        self,
        stream_id: str,
    ) -> dict[str, Any] | None:
        """Load latest snapshot for a stream.

        Args:
            stream_id: Stream identifier.

        Returns:
            Dict with "version" and "state" keys, or None if no snapshot exists.
        """
        rows = self._query(
            "SELECT version, state_data FROM snapshots WHERE stream_id = ?",
            (stream_id,),
        )
        if not rows:
            return None
        return {
            "version": rows[0]["version"],
            "state": json.loads(rows[0]["state_data"]),
        }

    def compact_stream(self, stream_id: str) -> int:
        """Delete events that precede the latest snapshot for a stream.

        Args:
            stream_id: Identifier of the stream to compact.

        Returns:
            Number of events deleted.

        Raises:
            CompactionError: When no snapshot exists for the stream.
        """
        snapshot = self.load_snapshot(stream_id)
        if snapshot is None:
            raise CompactionError(stream_id, "no snapshot exists; create a snapshot before compacting")

        snapshot_version: int = snapshot["version"]

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM events WHERE stream_id = ? AND stream_version <= ?",
                    (stream_id, snapshot_version),
                )
                deleted = cursor.rowcount
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                conn.rollback()
                logger.error("compact_stream_failed", stream_id=stream_id, error=str(e))
                raise
            finally:
                if not self._is_memory:
                    conn.close()

        from mcp_hangar.metrics import record_events_compacted

        record_events_compacted(stream_id, deleted)

        logger.info(
            "stream_compacted",
            stream_id=stream_id,
            snapshot_version=snapshot_version,
            events_deleted=deleted,
        )

        return deleted
