"""Adapters for `IDispatchCheckpoint`.

The SQLite one lives in the same database file as the events it tracks. That is
deliberate: a checkpoint in a different file can be restored, copied or wiped
independently of the log it refers to, and then it names a position that means
nothing. One file, one truth about how far delivery got.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

from mcp_hangar.domain.contracts.dispatch_checkpoint import IDispatchCheckpoint
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    position INTEGER NOT NULL
);
"""


class InMemoryDispatchCheckpoint(IDispatchCheckpoint):
    """Non-durable checkpoint, for tests and for a non-durable event store.

    Paired with an in-memory store it is exactly as durable as the log it tracks,
    which is the only pairing that makes sense: a durable checkpoint over a
    volatile log would claim delivery of events that no longer exist.
    """

    def __init__(self) -> None:
        self._position = 0
        self._lock = threading.Lock()

    def read(self) -> int:
        with self._lock:
            return self._position

    def advance(self, position: int) -> None:
        with self._lock:
            self._position = max(self._position, position)


class SqliteDispatchCheckpoint(IDispatchCheckpoint):
    """Checkpoint stored beside the events, in the same database file."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize and create the table if it is missing.

        Args:
            db_path: The same path the event store was given.
        """
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._is_memory = self._db_path == ":memory:"
        # A `:memory:` database is per-connection, so a checkpoint opened that
        # way would never see the store's tables and vice versa. Callers that
        # want in-memory should use InMemoryDispatchCheckpoint; this guards the
        # mistake rather than silently tracking a different database.
        if self._is_memory:
            raise ValueError(
                "SqliteDispatchCheckpoint needs a file path: a ':memory:' database is "
                "per-connection, so this would track a different database from the store. "
                "Use InMemoryDispatchCheckpoint instead."
            )
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def read(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT position FROM dispatch_checkpoint WHERE id = 0").fetchone()
            return int(row[0]) if row else 0

    def advance(self, position: int) -> None:
        # `MAX(excluded, existing)` in one statement rather than read-then-write:
        # two processes advancing concurrently must not let the slower one move
        # the mark backwards over delivery the faster one already recorded.
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dispatch_checkpoint (id, position) VALUES (0, ?)
                ON CONFLICT(id) DO UPDATE SET position = MAX(position, excluded.position)
                """,
                (position,),
            )
            conn.commit()
