"""SQLite-based persistent storage for tool access policies.

Mirrors the SQLiteRoleStore pattern: thread-local connections,
WAL mode, one connection opened per operation (context manager auto-commit).
"""

import json
import sqlite3
import threading
from pathlib import Path

import structlog

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

logger = structlog.get_logger(__name__)

TAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_access_policies (
    scope       TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    allow_list  TEXT NOT NULL DEFAULT '[]',
    deny_list   TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, target_id)
);
"""


class SQLiteToolAccessPolicyStore:
    """SQLite-based store for tool access policies.

    Provides durable persistence for ToolAccessPolicy objects keyed by
    (scope, target_id). On startup, call list_all_policies() and feed
    results into ToolAccessResolver to rebuild the in-memory cache.

    Thread-safe via thread-local connections (same model as SQLiteRoleStore).
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
        return self._local.connection

    def _init_schema(self) -> None:
        """Create the tool_access_policies table if it does not exist."""
        conn = self._get_connection()
        conn.executescript(TAP_SCHEMA)
        conn.commit()
        logger.info("sqlite_tap_store_initialized", db_path=self._db_path)

    def set_policy(
        self,
        scope: str,
        target_id: str,
        allow_list: list[str],
        deny_list: list[str],
    ) -> None:
        """Persist a tool access policy (upsert).

        Args:
            scope: "provider", "group", or "member".
            target_id: Provider/group/member identifier.
            allow_list: Allowed tool patterns.
            deny_list: Denied tool patterns.
        """
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO tool_access_policies (scope, target_id, allow_list, deny_list, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(scope, target_id) DO UPDATE SET
                allow_list = excluded.allow_list,
                deny_list = excluded.deny_list,
                updated_at = datetime('now')
            """,
            (scope, target_id, json.dumps(allow_list), json.dumps(deny_list)),
        )
        conn.commit()
        logger.info("tap_policy_set", scope=scope, target_id=target_id)

    def get_policy(self, scope: str, target_id: str) -> ToolAccessPolicy | None:
        """Retrieve a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.

        Returns:
            ToolAccessPolicy if found, None otherwise.
        """
        conn = self._get_connection()
        row = conn.execute(
            "SELECT allow_list, deny_list FROM tool_access_policies WHERE scope = ? AND target_id = ?",
            (scope, target_id),
        ).fetchone()
        if row is None:
            return None
        return ToolAccessPolicy(
            allow_list=tuple(json.loads(row["allow_list"])),
            deny_list=tuple(json.loads(row["deny_list"])),
        )

    def clear_policy(self, scope: str, target_id: str) -> None:
        """Remove a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.
        """
        conn = self._get_connection()
        conn.execute(
            "DELETE FROM tool_access_policies WHERE scope = ? AND target_id = ?",
            (scope, target_id),
        )
        conn.commit()
        logger.info("tap_policy_cleared", scope=scope, target_id=target_id)

    def list_all_policies(self) -> list[tuple[str, str, list[str], list[str]]]:
        """Return all stored policies for startup replay.

        Returns:
            List of (scope, target_id, allow_list, deny_list) tuples.
        """
        conn = self._get_connection()
        rows = conn.execute("SELECT scope, target_id, allow_list, deny_list FROM tool_access_policies").fetchall()
        return [
            (
                row["scope"],
                row["target_id"],
                json.loads(row["allow_list"]),
                json.loads(row["deny_list"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # noqa: BLE001 -- best-effort checkpoint on close
                pass
            self._local.connection.close()
            self._local.connection = None
