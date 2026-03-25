"""SQLite-backed SchemaTracker for tool schema drift detection -- BSL 1.1 licensed.

Stores SHA-256 fingerprints of tool schemas per provider and detects
ADDED/REMOVED/MODIFIED changes on subsequent provider startups. Shares the
same data/events.db file as the event store and BaselineStore.

See enterprise/LICENSE.BSL for license terms.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from mcp_hangar.domain.model.tool_catalog import ToolSchema

logger = structlog.get_logger(__name__)


def compute_schema_hash(name: str, input_schema: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of a tool's identity.

    Hashes only ``name`` + ``input_schema`` (not ``description``), because
    description changes are cosmetic and should not trigger drift alerts.

    Args:
        name: Tool name.
        input_schema: JSON Schema dict describing tool inputs.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    canonical = json.dumps(
        {"name": name, "input_schema": input_schema},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SchemaTracker:
    """SQLite-backed storage for tool schema snapshots and drift detection.

    Follows the same connection/locking pattern as
    ``enterprise.behavioral.baseline_store.BaselineStore``.

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
            "schema_tracker_initialized",
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
        """Initialize database schema for provider schema snapshots."""
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_schema_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    schema_hash TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    UNIQUE(provider_id, tool_name)
                );

                CREATE INDEX IF NOT EXISTS idx_schema_snapshots_provider
                ON provider_schema_snapshots(provider_id);
                """
            )
        finally:
            if not self._is_memory:
                conn.close()

    def check_and_store(
        self,
        provider_id: str,
        tools: list[ToolSchema],
    ) -> list[dict[str, Any]]:
        """Compare current tool schemas against stored snapshot and persist.

        On first call for a provider (no stored records), stores the current
        snapshot and returns an empty list (SC45-4: first-seen baseline).

        On subsequent calls, computes ADDED/REMOVED/MODIFIED changes, updates
        the stored snapshot, and returns the list of changes.

        Args:
            provider_id: Identifier of the provider.
            tools: Current list of tool schemas from the provider.

        Returns:
            List of change dicts, each with keys:
                - ``tool_name``: Name of the tool.
                - ``change_type``: One of ``"added"``, ``"removed"``, ``"modified"``.
                - ``old_hash``: Previous hash (None for added).
                - ``new_hash``: Current hash (None for removed).
            Empty list if this is the first time seeing the provider.

        Raises:
            sqlite3.Error: On database failure (logged, rolled back, re-raised).
        """
        now = datetime.now(UTC).isoformat()

        # Build current map: tool_name -> hash
        current_map: dict[str, str] = {tool.name: compute_schema_hash(tool.name, tool.input_schema) for tool in tools}

        with self._lock:
            conn = self._connect()
            try:
                # Read stored snapshot for this provider
                cursor = conn.execute(
                    "SELECT tool_name, schema_hash FROM provider_schema_snapshots WHERE provider_id = ?",
                    (provider_id,),
                )
                stored_rows = cursor.fetchall()
                stored_map: dict[str, str] = {row["tool_name"]: row["schema_hash"] for row in stored_rows}

                is_first_seen = len(stored_map) == 0

                changes: list[dict[str, Any]] = []

                if not is_first_seen:
                    current_names = set(current_map.keys())
                    stored_names = set(stored_map.keys())

                    # ADDED: in current but not in stored
                    for name in sorted(current_names - stored_names):
                        changes.append(
                            {
                                "tool_name": name,
                                "change_type": "added",
                                "old_hash": None,
                                "new_hash": current_map[name],
                            }
                        )

                    # REMOVED: in stored but not in current
                    for name in sorted(stored_names - current_names):
                        changes.append(
                            {
                                "tool_name": name,
                                "change_type": "removed",
                                "old_hash": stored_map[name],
                                "new_hash": None,
                            }
                        )

                    # MODIFIED: in both but different hash
                    for name in sorted(current_names & stored_names):
                        if stored_map[name] != current_map[name]:
                            changes.append(
                                {
                                    "tool_name": name,
                                    "change_type": "modified",
                                    "old_hash": stored_map[name],
                                    "new_hash": current_map[name],
                                }
                            )

                # UPSERT all current tools
                for tool_name, schema_hash in current_map.items():
                    conn.execute(
                        """
                        INSERT INTO provider_schema_snapshots
                            (provider_id, tool_name, schema_hash, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(provider_id, tool_name) DO UPDATE SET
                            schema_hash = excluded.schema_hash,
                            last_seen = excluded.last_seen
                        """,
                        (provider_id, tool_name, schema_hash, now, now),
                    )

                # DELETE removed tools (only when not first-seen)
                if not is_first_seen:
                    removed_names = set(stored_map.keys()) - set(current_map.keys())
                    for name in removed_names:
                        conn.execute(
                            "DELETE FROM provider_schema_snapshots WHERE provider_id = ? AND tool_name = ?",
                            (provider_id, name),
                        )

                conn.commit()
                return changes

            except Exception as e:  # noqa: BLE001 -- infra-boundary: rollback and propagate on any DB error
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001, S110 -- best-effort rollback
                    pass
                logger.error(
                    "check_and_store_failed",
                    provider_id=provider_id,
                    error=str(e),
                )
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def get_snapshot(self, provider_id: str) -> list[dict[str, Any]]:
        """Retrieve stored schema snapshot records for a provider.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            List of snapshot records as dicts with keys:
            provider_id, tool_name, schema_hash, first_seen, last_seen.
        """
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT provider_id, tool_name, schema_hash, first_seen, last_seen
                    FROM provider_schema_snapshots
                    WHERE provider_id = ?
                    ORDER BY tool_name
                    """,
                    (provider_id,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                if not self._is_memory:
                    conn.close()
