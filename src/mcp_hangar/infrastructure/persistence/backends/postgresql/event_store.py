"""PostgreSQL-based Event Store implementation.

Durable, multi-node-safe counterpart to `SQLiteEventStore`: the same
append-only log and optimistic-concurrency contract, but the version check
and the concurrency guarantee move from an in-process `threading.Lock` to the
database itself. On a shared Postgres instance two writers on two different
processes can race for real -- SQLite's application-level lock never had that
adversary.

Concurrency is enforced by a single atomic write to the stream's row in
`streams`: the INSERT (new stream, `ON CONFLICT (stream_id) DO NOTHING`) or
UPDATE (existing stream, `WHERE version = expected_version`) that advances a
stream's version writes the *new* version directly, and only if that write
actually changed a row do the event rows get appended -- all inside one
transaction. Two concurrent appenders to the same `stream_id` serialize on
that row's lock; the loser's conditional write affects zero rows and becomes
a `ConcurrencyError` before it has touched a single event row. This is
preferred over racing on the `(stream_id, stream_version)` unique constraint
and catching the violation: the constraint approach only tells a loser it
lost *after* it has already inserted (and must then unwind) events, and needs
driver-specific exception classes to recognise. The unique constraint stays
in the schema anyway, as a data-integrity backstop against anything that
writes to this table outside this class.

`global_position` comes from a `BIGSERIAL`. A rolled-back transaction leaves
a gap in that sequence -- expected, and harmless for
`read_all(from_position=...)`, which only needs strictly increasing values,
not contiguous ones. The sharper edge is that sequence *allocation* order is
not the same as commit order: two concurrent appenders can be handed
positions 5 and 6 and commit 6 first, so a `read_all` cursor that has already
advanced past 6 will not see 5 arrive right behind it. Projections built on
this stream should tolerate a small amount of reordering near the tail
rather than treat the cursor as linearizable.
"""

from collections.abc import Iterator
from datetime import datetime, UTC
import json
from typing import Any

from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.exceptions import CompactionError
from mcp_hangar.logging_config import get_logger

from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

logger = get_logger(__name__)

# Postgres already maintains an index for a PRIMARY KEY and for a UNIQUE
# constraint, so `global_position` (PK) and `(stream_id, stream_version)`
# (UNIQUE) need no separate `CREATE INDEX` -- unlike the SQLite schema this
# mirrors, which has no such implicit index and declares them explicitly.
_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {events_table} (
    global_position BIGSERIAL PRIMARY KEY,
    stream_id TEXT NOT NULL,
    stream_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data JSONB NOT NULL,
    metadata JSONB,
    created_at TEXT NOT NULL,
    UNIQUE(stream_id, stream_version)
);

CREATE TABLE IF NOT EXISTS {streams_table} (
    stream_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT -1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {snapshots_table} (
    stream_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    state_data JSONB NOT NULL,
    created_at TEXT NOT NULL
);
"""


class PostgresEventStore(IEventStore):
    """PostgreSQL-based event store with database-enforced optimistic concurrency.

    See the module docstring for how the concurrency check and
    `global_position` ordering are implemented -- both are meaningfully
    different from the SQLite version because this one has to survive real
    concurrent writers, not just concurrent threads.

    Schema (mirrors `SQLiteEventStore`, translated to Postgres types):
    - events: Main event table with global ordering.
    - streams: Tracks each stream's current version for concurrency control.
    - snapshots: Aggregate snapshots for bounded replay.
    """

    def __init__(
        self,
        connection_factory: IConnectionFactory,
        *,
        table_prefix: str = "",
        serializer: EventSerializer | None = None,
    ) -> None:
        """Initialize the PostgreSQL event store.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This store knows
                SQL; it deliberately does not know psycopg2, pooling, or how a
                connection is obtained.
            table_prefix: Optional prefix for table names, for sharing one
                database across multiple stores/tenants.
            serializer: Optional EventSerializer instance. Allows injecting an
                upcaster-aware serializer.
        """
        self._connections = connection_factory
        self._serializer = serializer or EventSerializer()
        self._events_table = f"{table_prefix}events" if table_prefix else "events"
        self._streams_table = f"{table_prefix}streams" if table_prefix else "streams"
        self._snapshots_table = f"{table_prefix}snapshots" if table_prefix else "snapshots"

    def initialize(self) -> None:
        """Create the events/streams/snapshots tables if they don't exist yet.

        Idempotent -- safe to call on every process start. Must be called
        before first use; unlike the SQLite version this does not open its
        own connection in `__init__`, because connections here come from a
        shared, possibly-lazy pool that construction should not reach into.
        """
        schema = _SCHEMA_TEMPLATE.format(
            events_table=self._events_table,
            streams_table=self._streams_table,
            snapshots_table=self._snapshots_table,
        )
        with self._connections.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()
        logger.info("postgres_event_store_initialized", events_table=self._events_table)

    @staticmethod
    def _as_json_text(value: Any) -> str:
        """Normalise a JSONB column's value back to the JSON text `EventSerializer` expects.

        A real psycopg2 connection auto-casts `jsonb` columns to Python
        objects (dict/list) when the json codec is registered, but
        `EventSerializer.deserialize` takes a JSON string (it does its own
        `json.loads`). A test double's cursor may hand back a plain string
        instead. Handling both keeps the contract satisfied either way.
        """
        if isinstance(value, str):
            return value
        return json.dumps(value)

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

        new_version = expected_version + len(events)
        timestamp = datetime.now(UTC).isoformat()

        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    # Atomically test the expected version and reserve the new
                    # one in a single statement -- see module docstring for why
                    # this, rather than the unique constraint, is the primary
                    # concurrency guard.
                    if expected_version == -1:
                        cur.execute(
                            f"""
                            INSERT INTO {self._streams_table} (stream_id, version, created_at, updated_at)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (stream_id) DO NOTHING
                            RETURNING stream_id
                            """,
                            (stream_id, new_version, timestamp, timestamp),
                        )
                    else:
                        cur.execute(
                            f"""
                            UPDATE {self._streams_table}
                            SET version = %s, updated_at = %s
                            WHERE stream_id = %s AND version = %s
                            RETURNING stream_id
                            """,
                            (new_version, timestamp, stream_id, expected_version),
                        )

                    if cur.fetchone() is None:
                        cur.execute(
                            f"SELECT version FROM {self._streams_table} WHERE stream_id = %s",
                            (stream_id,),
                        )
                        row = cur.fetchone()
                        actual_version = row[0] if row else -1
                        raise ConcurrencyError(stream_id, expected_version, actual_version)

                    for offset, event in enumerate(events, start=1):
                        event_type, data = self._serializer.serialize(event)
                        cur.execute(
                            f"""
                            INSERT INTO {self._events_table}
                            (stream_id, stream_version, event_type, data, created_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (stream_id, expected_version + offset, event_type, data, timestamp),
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
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_type, data FROM {self._events_table}
                WHERE stream_id = %s AND stream_version >= %s
                ORDER BY stream_version ASC
                """,
                (stream_id, from_version),
            )
            rows = cur.fetchall()
            # A SELECT still opens a transaction on the borrowed connection;
            # commit here so it isn't handed back to the pool "idle in
            # transaction" for whoever borrows it next (see
            # PostgresMetricsHistoryStore.query for the same pattern).
            conn.commit()

            events = [self._serializer.deserialize(event_type, self._as_json_text(data)) for event_type, data in rows]

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
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT global_position, stream_id, event_type, data
                FROM {self._events_table}
                WHERE global_position > %s
                ORDER BY global_position ASC
                LIMIT %s
                """,
                (from_position, limit),
            )

            # Fetch all rows first to allow releasing the connection back to
            # the pool before the caller starts consuming the iterator.
            rows = cur.fetchall()
            # Commit to close out the implicit transaction the SELECT opened
            # -- otherwise the connection goes back to the pool "idle in
            # transaction", and projections poll this method continuously.
            conn.commit()

        for global_position, stream_id, event_type, data in rows:
            event = self._serializer.deserialize(event_type, self._as_json_text(data))
            yield global_position, stream_id, event

    def get_stream_version(self, stream_id: str) -> int:
        """Get current version of a stream.

        Args:
            stream_id: Stream identifier.

        Returns:
            Current version, or -1 if stream doesn't exist.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT version FROM {self._streams_table} WHERE stream_id = %s",
                (stream_id,),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else -1

    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs in the store.

        Returns:
            List of stream identifiers.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT stream_id FROM {self._streams_table} ORDER BY stream_id")
            rows = [row[0] for row in cur.fetchall()]
            conn.commit()
            return rows

    def get_event_count(self) -> int:
        """Get total number of events in the store.

        Returns:
            Total event count.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self._events_table}")
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else 0

    def get_stream_count(self) -> int:
        """Get total number of streams.

        Returns:
            Total stream count.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self._streams_table}")
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else 0

    def list_streams(self, prefix: str = "") -> list[str]:
        """List all stream IDs, optionally filtered by prefix.

        Args:
            prefix: Optional prefix to filter streams.

        Returns:
            List of stream IDs matching the prefix.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            if prefix:
                cur.execute(
                    f"SELECT stream_id FROM {self._streams_table} WHERE stream_id LIKE %s ORDER BY stream_id",
                    (f"{prefix}%",),
                )
            else:
                cur.execute(f"SELECT stream_id FROM {self._streams_table} ORDER BY stream_id")
            rows = [row[0] for row in cur.fetchall()]
            conn.commit()
            return rows

    def save_snapshot(
        self,
        stream_id: str,
        version: int,
        state: dict[str, Any],
    ) -> None:
        """Save an aggregate snapshot, replacing any prior snapshot for the stream.

        Args:
            stream_id: Stream identifier (matches event stream).
            version: Stream version this snapshot represents.
            state: Serialized aggregate state (must be JSON-serializable).
        """
        timestamp = datetime.now(UTC).isoformat()
        state_json = json.dumps(state)

        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self._snapshots_table} (stream_id, version, state_data, created_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (stream_id) DO UPDATE SET
                            version = EXCLUDED.version,
                            state_data = EXCLUDED.state_data,
                            created_at = EXCLUDED.created_at
                        """,
                        (stream_id, version, state_json, timestamp),
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
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT version, state_data FROM {self._snapshots_table} WHERE stream_id = %s",
                (stream_id,),
            )
            row = cur.fetchone()
            conn.commit()
            if row is None:
                return None

            version, state_data = row
            state = state_data if isinstance(state_data, dict) else json.loads(state_data)
            return {"version": version, "state": state}

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

        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {self._events_table} WHERE stream_id = %s AND stream_version <= %s",
                        (stream_id, snapshot_version),
                    )
                    deleted = cur.rowcount
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                conn.rollback()
                logger.error("compact_stream_failed", stream_id=stream_id, error=str(e))
                raise

        from mcp_hangar.metrics import record_events_compacted

        record_events_compacted(stream_id, deleted)

        logger.info(
            "stream_compacted",
            stream_id=stream_id,
            snapshot_version=snapshot_version,
            events_deleted=deleted,
        )

        return int(deleted)
