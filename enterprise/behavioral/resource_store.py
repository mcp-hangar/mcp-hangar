"""SQLite-backed ResourceStore for resource usage profiling -- BSL 1.1 licensed.

Stores time-series CPU, memory, and network I/O samples from provider
containers. Computes statistical baselines (mean + stddev) for deviation
detection during ENFORCING mode. Shares the same data directory as
the event store and baseline store.

See enterprise/LICENSE.BSL for license terms.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import threading
from typing import Any

import structlog

from mcp_hangar.domain.value_objects.behavioral import ResourceSample

logger = structlog.get_logger(__name__)

_MIN_BASELINE_SAMPLES = 10
"""Minimum number of samples required to compute a meaningful baseline."""


class ResourceStore:
    """SQLite-backed storage for resource usage samples and baselines.

    Implements the IResourceStore protocol defined in
    ``mcp_hangar.domain.contracts.behavioral``.

    Thread-safe via ``threading.Lock`` on all SQLite operations.

    Args:
        db_path: Path to SQLite database file.
            Use ``":memory:"`` for in-memory store (testing).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._is_memory = self._db_path == ":memory:"

        # For in-memory database, keep a persistent connection
        # (each new connection to :memory: creates a NEW database)
        self._persistent_conn: sqlite3.Connection | None = None
        if self._is_memory:
            self._persistent_conn = self._create_connection()

        self._init_schema()

        logger.info(
            "resource_store_initialized",
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
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _connect(self) -> sqlite3.Connection:
        """Get database connection.

        For in-memory databases, returns the persistent connection.
        For file-based databases, creates a new connection.
        """
        if self._is_memory and self._persistent_conn is not None:
            return self._persistent_conn
        return self._create_connection()

    def _init_schema(self) -> None:
        """Initialize database schema for resource samples and baselines."""
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    cpu_percent REAL NOT NULL,
                    memory_bytes INTEGER NOT NULL,
                    memory_limit_bytes INTEGER NOT NULL DEFAULT 0,
                    network_rx_bytes INTEGER NOT NULL DEFAULT 0,
                    network_tx_bytes INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_resource_samples_provider_time
                ON resource_samples(provider_id, sampled_at);

                CREATE TABLE IF NOT EXISTS resource_baselines (
                    provider_id TEXT PRIMARY KEY,
                    cpu_mean REAL NOT NULL,
                    cpu_stddev REAL NOT NULL,
                    memory_mean REAL NOT NULL,
                    memory_stddev REAL NOT NULL,
                    network_rx_mean REAL NOT NULL,
                    network_rx_stddev REAL NOT NULL,
                    network_tx_mean REAL NOT NULL,
                    network_tx_stddev REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    computed_at TEXT NOT NULL
                );
                """
            )
        finally:
            if not self._is_memory:
                conn.close()

    def record_sample(self, sample: ResourceSample) -> None:
        """Persist a resource usage sample.

        Args:
            sample: The resource sample to store.

        Raises:
            sqlite3.Error: On database failure (logged, never silently swallowed).
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO resource_samples
                        (provider_id, sampled_at, cpu_percent, memory_bytes,
                         memory_limit_bytes, network_rx_bytes, network_tx_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sample.provider_id,
                        sample.sampled_at,
                        sample.cpu_percent,
                        sample.memory_bytes,
                        sample.memory_limit_bytes,
                        sample.network_rx_bytes,
                        sample.network_tx_bytes,
                    ),
                )
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "record_sample_failed",
                    provider_id=sample.provider_id,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def get_samples(self, provider_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent resource samples for a provider.

        Args:
            provider_id: Identifier of the provider.
            limit: Maximum number of samples to return (most recent first).

        Returns:
            List of sample records as dicts, ordered by sampled_at descending.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT provider_id, sampled_at, cpu_percent, memory_bytes,
                           memory_limit_bytes, network_rx_bytes, network_tx_bytes
                    FROM resource_samples
                    WHERE provider_id = ?
                    ORDER BY sampled_at DESC
                    LIMIT ?
                    """,
                    (provider_id, limit),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                if not self._is_memory:
                    conn.close()

    def get_baseline(self, provider_id: str) -> dict[str, Any] | None:
        """Retrieve the computed resource baseline for a provider.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            Baseline dict with mean/stddev statistics, or None if not computed.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT provider_id, cpu_mean, cpu_stddev,
                           memory_mean, memory_stddev,
                           network_rx_mean, network_rx_stddev,
                           network_tx_mean, network_tx_stddev,
                           sample_count, computed_at
                    FROM resource_baselines
                    WHERE provider_id = ?
                    """,
                    (provider_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return dict(row)
            finally:
                if not self._is_memory:
                    conn.close()

    def compute_and_store_baseline(self, provider_id: str) -> dict[str, Any] | None:
        """Compute and persist a resource baseline from accumulated samples.

        Requires at least 10 samples to produce a meaningful baseline.
        Uses mean + population stddev for CPU, memory, and network metrics.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            Baseline dict with mean/stddev statistics, or None if insufficient data.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT cpu_percent, memory_bytes,
                           network_rx_bytes, network_tx_bytes
                    FROM resource_samples
                    WHERE provider_id = ?
                    """,
                    (provider_id,),
                )
                rows = cursor.fetchall()

                if len(rows) < _MIN_BASELINE_SAMPLES:
                    return None

                cpu_values = [row["cpu_percent"] for row in rows]
                memory_values = [float(row["memory_bytes"]) for row in rows]
                rx_values = [float(row["network_rx_bytes"]) for row in rows]
                tx_values = [float(row["network_tx_bytes"]) for row in rows]

                def _mean(values: list[float]) -> float:
                    return sum(values) / len(values)

                def _stddev(values: list[float], mean: float) -> float:
                    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

                cpu_mean = _mean(cpu_values)
                memory_mean = _mean(memory_values)
                rx_mean = _mean(rx_values)
                tx_mean = _mean(tx_values)

                now = datetime.now(UTC).isoformat()
                baseline = {
                    "provider_id": provider_id,
                    "cpu_mean": cpu_mean,
                    "cpu_stddev": _stddev(cpu_values, cpu_mean),
                    "memory_mean": memory_mean,
                    "memory_stddev": _stddev(memory_values, memory_mean),
                    "network_rx_mean": rx_mean,
                    "network_rx_stddev": _stddev(rx_values, rx_mean),
                    "network_tx_mean": tx_mean,
                    "network_tx_stddev": _stddev(tx_values, tx_mean),
                    "sample_count": len(rows),
                    "computed_at": now,
                }

                conn.execute(
                    """
                    INSERT INTO resource_baselines
                        (provider_id, cpu_mean, cpu_stddev,
                         memory_mean, memory_stddev,
                         network_rx_mean, network_rx_stddev,
                         network_tx_mean, network_tx_stddev,
                         sample_count, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                        cpu_mean = excluded.cpu_mean,
                        cpu_stddev = excluded.cpu_stddev,
                        memory_mean = excluded.memory_mean,
                        memory_stddev = excluded.memory_stddev,
                        network_rx_mean = excluded.network_rx_mean,
                        network_rx_stddev = excluded.network_rx_stddev,
                        network_tx_mean = excluded.network_tx_mean,
                        network_tx_stddev = excluded.network_tx_stddev,
                        sample_count = excluded.sample_count,
                        computed_at = excluded.computed_at
                    """,
                    (
                        provider_id,
                        baseline["cpu_mean"],
                        baseline["cpu_stddev"],
                        baseline["memory_mean"],
                        baseline["memory_stddev"],
                        baseline["network_rx_mean"],
                        baseline["network_rx_stddev"],
                        baseline["network_tx_mean"],
                        baseline["network_tx_stddev"],
                        baseline["sample_count"],
                        baseline["computed_at"],
                    ),
                )
                conn.commit()

                return baseline
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "compute_baseline_failed",
                    provider_id=provider_id,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def prune(self, retention_days: int = 7) -> int:
        """Delete resource samples older than retention period.

        Args:
            retention_days: Number of days to retain samples (default 7).

        Returns:
            Number of rows deleted.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM resource_samples WHERE sampled_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return cursor.rowcount
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "prune_samples_failed",
                    retention_days=retention_days,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()
