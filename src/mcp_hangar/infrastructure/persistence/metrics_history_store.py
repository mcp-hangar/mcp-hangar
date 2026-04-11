"""Metrics history store for time-series metric snapshots.

Records per-provider metric snapshots every 60 seconds into SQLite,
supports querying by provider, metric name, and time range, and prunes
old data based on a configurable retention window.
"""

import time
from contextlib import contextmanager
from collections.abc import Generator
from dataclasses import dataclass

from .database_common import MigrationRunner, SQLiteConfig, SQLiteConnectionFactory
from ...logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_MIGRATIONS: list[dict] = [
    {
        "version": 1,
        "name": "create_metric_snapshots",
        "sql": """
            CREATE TABLE IF NOT EXISTS metric_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT    NOT NULL,
                metric_name TEXT    NOT NULL,
                value       REAL    NOT NULL,
                recorded_at REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metric_snapshots_lookup
                ON metric_snapshots (provider_id, metric_name, recorded_at);
        """,
    },
]

# ---------------------------------------------------------------------------
# Data object
# ---------------------------------------------------------------------------


@dataclass
class MetricPoint:
    """A single time-series data point.

    Attributes:
        provider_id: Provider this metric belongs to.
        metric_name: Name of the metric (e.g. ``tool_calls_total``).
        value: Numeric value.
        recorded_at: Unix timestamp (seconds since epoch) when the snapshot was taken.
    """

    provider_id: str
    metric_name: str
    value: float
    recorded_at: float


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MetricsHistoryStore:
    """SQLite-backed store for per-provider metric snapshots.

    Provides:
    - :meth:`record_snapshot` — persist a batch of :class:`MetricPoint` rows.
    - :meth:`query` — retrieve points filtered by provider, metric, and time range.
    - :meth:`prune` — delete rows older than the retention window.

    Thread-safety: relies on :class:`SQLiteConnectionFactory` which uses
    thread-local connections (one connection per thread).  All writes are
    serialised by a ``threading.Lock`` to avoid ``SQLITE_BUSY`` under bursts.

    Args:
        config: SQLite configuration.  Defaults to in-memory (useful for tests).
        retention_days: How many days of history to keep.  Prune is not run
            automatically — callers (or the background worker) must call
            :meth:`prune` periodically.
    """

    def __init__(
        self,
        config: SQLiteConfig | None = None,
        retention_days: int = 7,
    ) -> None:
        self._config = config or SQLiteConfig(path=":memory:")
        self._retention_days = retention_days
        self._factory = SQLiteConnectionFactory(self._config)
        self._runner = MigrationRunner(
            connection_factory=self._factory,
            migrations=_MIGRATIONS,
        )
        self._runner.run()

    @contextmanager
    def _conn(self) -> Generator:
        with self._factory.get_connection() as conn:
            yield conn
            conn.commit()

    def record_snapshot(self, points: list[MetricPoint]) -> None:
        """Persist a batch of metric data points.

        Args:
            points: List of :class:`MetricPoint` instances to store.
        """
        if not points:
            return
        rows = [(p.provider_id, p.metric_name, p.value, p.recorded_at) for p in points]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO metric_snapshots (provider_id, metric_name, value, recorded_at) VALUES (?, ?, ?, ?)",
                rows,
            )

    def query(
        self,
        provider_id: str | None = None,
        metric_name: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 1000,
    ) -> list[MetricPoint]:
        """Query stored metric history.

        Args:
            provider_id: Filter by provider.  ``None`` returns all providers.
            metric_name: Filter by metric name.  ``None`` returns all metrics.
            from_ts: Start of time range (unix timestamp, inclusive).
            to_ts: End of time range (unix timestamp, inclusive).
            limit: Maximum number of rows to return (capped at 10 000).

        Returns:
            List of :class:`MetricPoint` ordered by ``recorded_at`` ascending.
        """
        limit = min(max(1, limit), 10_000)
        conditions: list[str] = []
        params: list = []

        if provider_id is not None:
            conditions.append("provider_id = ?")
            params.append(provider_id)
        if metric_name is not None:
            conditions.append("metric_name = ?")
            params.append(metric_name)
        if from_ts is not None:
            conditions.append("recorded_at >= ?")
            params.append(from_ts)
        if to_ts is not None:
            conditions.append("recorded_at <= ?")
            params.append(to_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT provider_id, metric_name, value, recorded_at "
            f"FROM metric_snapshots {where} "
            f"ORDER BY recorded_at ASC "
            f"LIMIT ?"
        )
        params.append(limit)

        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()

        return [MetricPoint(provider_id=r[0], metric_name=r[1], value=r[2], recorded_at=r[3]) for r in rows]

    def prune(self) -> int:
        """Delete metric snapshots older than the retention window.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - self._retention_days * 86_400
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM metric_snapshots WHERE recorded_at < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount

        if deleted:
            logger.info("metrics_history_pruned", deleted=deleted, retention_days=self._retention_days)
        return deleted


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_metrics_history_store: MetricsHistoryStore | None = None


def get_metrics_history_store() -> MetricsHistoryStore:
    """Get the global :class:`MetricsHistoryStore` instance.

    Creates a default in-memory instance on first call.  Production bootstrap
    should call :func:`set_metrics_history_store` with a file-backed instance
    before this is first called.
    """
    global _metrics_history_store
    if _metrics_history_store is None:
        _metrics_history_store = MetricsHistoryStore()
    return _metrics_history_store


def set_metrics_history_store(store: MetricsHistoryStore) -> None:
    """Override the global :class:`MetricsHistoryStore` (bootstrap / testing)."""
    global _metrics_history_store
    _metrics_history_store = store
