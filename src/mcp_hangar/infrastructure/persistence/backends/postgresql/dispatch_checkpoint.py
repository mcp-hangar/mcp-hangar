"""PostgreSQL adapter for `IDispatchCheckpoint`.

The SQLite checkpoint lives in the same file as the events it tracks, on
purpose: a checkpoint that can drift from its log names a position that means
nothing. PostgreSQL has no equivalent "same file" guarantee, but the same
rule still applies in spirit -- this adapter must point at the same database
as the event store it is paired with, or the position it reports is a number
about a different log.
"""

from __future__ import annotations

from mcp_hangar.domain.contracts.dispatch_checkpoint import IDispatchCheckpoint
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    position BIGINT NOT NULL
);
"""


class PostgresDispatchCheckpoint(IDispatchCheckpoint):
    """Checkpoint stored in PostgreSQL, tracking delivery for a shared event log."""

    def __init__(self, connection_factory: IConnectionFactory) -> None:
        """Initialize and create the table if it is missing.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This adapter knows
                SQL; it deliberately does not know psycopg2, pooling, or how a
                connection is obtained.
        """
        self._connections = connection_factory
        with self._connections.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()

    def read(self) -> int:
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT position FROM dispatch_checkpoint WHERE id = 0")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def advance(self, position: int) -> None:
        # `GREATEST(excluded, existing)` in one statement rather than
        # read-then-write: two processes advancing concurrently must not let
        # the slower one move the mark backwards over delivery the faster one
        # already recorded.
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dispatch_checkpoint (id, position) VALUES (0, %s)
                ON CONFLICT (id) DO UPDATE SET position = GREATEST(dispatch_checkpoint.position, excluded.position)
                """,
                (position,),
            )
            conn.commit()
