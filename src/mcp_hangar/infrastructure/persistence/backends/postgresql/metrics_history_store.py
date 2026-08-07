"""PostgreSQL adapter for `IMetricsHistoryStore`.

The SQLite reference (`infrastructure.persistence.metrics_history_store`)
keeps one file per host, which is fine for a single-node install but not for
a fleet reporting into the same history endpoints. This adapter is the
multi-node counterpart: same table shape, same query semantics, reachable
from every gateway process instead of pinned to one host's disk.

`recorded_at` stays a Python float (unix epoch) rather than becoming a
`TIMESTAMPTZ` -- the two backends have to round-trip a `MetricPoint`
identically, and comparing a `datetime` against a float `from_ts`/`to_ts` on
one backend but not the other is exactly the kind of drift that turns "works
against SQLite in tests" into "wrong answers in production."
"""

from __future__ import annotations

import time

from mcp_hangar.domain.contracts.metrics_history import IMetricsHistoryStore
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory, postgres_ddl
from mcp_hangar.infrastructure.persistence.metrics_history_store import MetricPoint
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    mcp_server_id TEXT             NOT NULL,
    metric_name   TEXT             NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    recorded_at   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metric_snapshots_lookup
    ON metric_snapshots (mcp_server_id, metric_name, recorded_at);
"""


class PostgresMetricsHistoryStore(IMetricsHistoryStore):
    """Metric-snapshot time series backed by PostgreSQL, for a multi-node gateway.

    Every history endpoint reads through this once request routing can land
    on any node, so the snapshots a background worker records on one process
    have to be visible to a query served by another. There is no in-process
    lock here the way the SQLite reference needs one against `SQLITE_BUSY`:
    each call borrows its own connection from the shared pool and commits
    within it, so PostgreSQL's own transaction isolation is what serialises
    concurrent writers.

    `prune()` is not scheduled by this class -- same as the reference, the
    background worker (or an operator) decides when the retention window is
    swept.
    """

    def __init__(
        self,
        connection_factory: IConnectionFactory,
        retention_days: int = 7,
    ) -> None:
        """Initialize and create the table if it is missing.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This adapter knows
                SQL; it deliberately does not know psycopg2, pooling, or how a
                connection is obtained.
            retention_days: How many days of history `prune()` keeps. Mirrors
                the SQLite reference's default.
        """
        self._connections = connection_factory
        self._retention_days = retention_days
        with self._connections.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(postgres_ddl(_SCHEMA))
            conn.commit()

    def record_snapshot(self, points: list[MetricPoint]) -> None:
        """Persist a batch of metric data points.

        Args:
            points: List of `MetricPoint` instances to store.
        """
        if not points:
            return
        rows = [(p.mcp_server_id, p.metric_name, p.value, p.recorded_at) for p in points]
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO metric_snapshots (mcp_server_id, metric_name, value, recorded_at)
                VALUES (%s, %s, %s, %s)
                """,
                rows,
            )
            conn.commit()

    def query(
        self,
        mcp_server_id: str | None = None,
        metric_name: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 1000,
    ) -> list[MetricPoint]:
        """Query stored metric history.

        Args:
            mcp_server_id: Filter by mcp_server. `None` returns all mcp_servers.
            metric_name: Filter by metric name. `None` returns all metrics.
            from_ts: Start of time range (unix timestamp, inclusive).
            to_ts: End of time range (unix timestamp, inclusive).
            limit: Maximum number of rows to return (capped at 10 000).

        Returns:
            List of `MetricPoint` ordered by `recorded_at` ascending.
        """
        limit = min(max(1, limit), 10_000)
        conditions: list[str] = []
        params: list = []

        if mcp_server_id is not None:
            conditions.append("mcp_server_id = %s")
            params.append(mcp_server_id)
        if metric_name is not None:
            conditions.append("metric_name = %s")
            params.append(metric_name)
        if from_ts is not None:
            conditions.append("recorded_at >= %s")
            params.append(from_ts)
        if to_ts is not None:
            conditions.append("recorded_at <= %s")
            params.append(to_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT mcp_server_id, metric_name, value, recorded_at "
            f"FROM metric_snapshots {where} "
            f"ORDER BY recorded_at ASC "
            f"LIMIT %s"
        )
        params.append(limit)

        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # A `SELECT` still opens a transaction on the borrowed connection.
            # Ending it here -- same as the SQLite reference's `_conn()`, which
            # commits after every call including reads -- keeps a connection
            # handed back to the pool from sitting "idle in transaction" for
            # whoever borrows it next.
            conn.commit()

        return [MetricPoint(mcp_server_id=r[0], metric_name=r[1], value=r[2], recorded_at=r[3]) for r in rows]

    def prune(self) -> int:
        """Delete metric snapshots older than the retention window.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - self._retention_days * 86_400
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM metric_snapshots WHERE recorded_at < %s",
                (cutoff,),
            )
            deleted = cur.rowcount
            conn.commit()

        if deleted:
            logger.info("metrics_history_pruned", deleted=deleted, retention_days=self._retention_days)
        return deleted or 0
