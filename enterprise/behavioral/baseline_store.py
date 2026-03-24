"""SQLite-backed BaselineStore for behavioral profiling -- BSL 1.1 licensed.

Stores aggregated network observations during the LEARNING phase and
persists behavioral mode per provider. Shares the same data/events.db
file as the event store.

See enterprise/LICENSE.BSL for license terms.
"""

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import threading
from typing import Any

import structlog

from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation

logger = structlog.get_logger(__name__)


class BaselineStore:
    """SQLite-backed storage for network observation baselines and mode state.

    Implements the IBaselineStore protocol defined in
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
            "baseline_store_initialized",
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
        """Initialize database schema for observation and mode tables."""
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(provider_id, host, port, protocol)
                );

                CREATE INDEX IF NOT EXISTS idx_provider_observations_provider
                ON provider_observations(provider_id);

                CREATE TABLE IF NOT EXISTS behavioral_mode (
                    provider_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'disabled',
                    learning_started_at TEXT,
                    learning_duration_hours INTEGER NOT NULL DEFAULT 72
                );
                """
            )
        finally:
            if not self._is_memory:
                conn.close()

    def record_observation(self, observation: NetworkObservation) -> None:
        """Store an observation for baseline building.

        Uses UPSERT: inserts a new row on first call for a
        ``(provider_id, host, port, protocol)`` tuple, increments
        ``observation_count`` and updates ``last_seen`` on subsequent calls.

        Args:
            observation: The network observation to store.

        Raises:
            sqlite3.Error: On database failure (logged, never silently swallowed).
        """
        now = datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO provider_observations
                        (provider_id, host, port, protocol, first_seen, last_seen, observation_count)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(provider_id, host, port, protocol) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        observation_count = observation_count + 1
                    """,
                    (
                        observation.provider_id,
                        observation.destination_host,
                        observation.destination_port,
                        observation.protocol,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "record_observation_failed",
                    provider_id=observation.provider_id,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def get_observations(self, provider_id: str) -> list[dict[str, Any]]:
        """Retrieve baseline observation records for a provider.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            List of observation records as dicts with keys:
            provider_id, host, port, protocol, first_seen, last_seen,
            observation_count.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT provider_id, host, port, protocol,
                           first_seen, last_seen, observation_count
                    FROM provider_observations
                    WHERE provider_id = ?
                    ORDER BY host, port
                    """,
                    (provider_id,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                if not self._is_memory:
                    conn.close()

    def get_mode(self, provider_id: str) -> BehavioralMode:
        """Get the persisted behavioral mode for a provider.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            Current persisted BehavioralMode.
            Returns ``BehavioralMode.DISABLED`` for unknown providers.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT mode FROM behavioral_mode WHERE provider_id = ?",
                    (provider_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return BehavioralMode.DISABLED
                return BehavioralMode(row["mode"])
            finally:
                if not self._is_memory:
                    conn.close()

    def set_mode(
        self,
        provider_id: str,
        mode: BehavioralMode,
        learning_duration_hours: int = 72,
    ) -> None:
        """Persist the behavioral mode with timing metadata.

        When entering LEARNING mode, sets ``learning_started_at`` to now.
        When switching to another mode, preserves existing
        ``learning_started_at``.

        Args:
            provider_id: Identifier of the provider.
            mode: New BehavioralMode to persist.
            learning_duration_hours: Duration of the learning phase in hours.
        """
        now = datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._connect()
            try:
                if mode == BehavioralMode.LEARNING:
                    # Set learning_started_at when entering LEARNING
                    conn.execute(
                        """
                        INSERT INTO behavioral_mode
                            (provider_id, mode, learning_started_at, learning_duration_hours)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(provider_id) DO UPDATE SET
                            mode = excluded.mode,
                            learning_started_at = excluded.learning_started_at,
                            learning_duration_hours = excluded.learning_duration_hours
                        """,
                        (provider_id, mode.value, now, learning_duration_hours),
                    )
                else:
                    # Preserve existing learning_started_at
                    conn.execute(
                        """
                        INSERT INTO behavioral_mode
                            (provider_id, mode, learning_duration_hours)
                        VALUES (?, ?, ?)
                        ON CONFLICT(provider_id) DO UPDATE SET
                            mode = excluded.mode
                        """,
                        (provider_id, mode.value, learning_duration_hours),
                    )
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "set_mode_failed",
                    provider_id=provider_id,
                    mode=mode.value,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()
