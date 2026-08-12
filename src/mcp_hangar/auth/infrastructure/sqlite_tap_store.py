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
    approval_list TEXT NOT NULL DEFAULT '[]',
    approval_timeout_seconds INTEGER,
    approval_channel TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, target_id)
);
"""

#: Columns added after the table shipped. ``CREATE TABLE IF NOT EXISTS`` is a
#: no-op against an existing database, so a deployment that has ever written a
#: policy keeps the old three-column table unless it is migrated here.
_ADDED_COLUMNS = (
    ("approval_list", "TEXT NOT NULL DEFAULT '[]'"),
    ("approval_timeout_seconds", "INTEGER"),
    ("approval_channel", "TEXT"),
)

#: The approval columns are nullable rather than carrying a SQL default: the
#: default lives on :class:`ToolAccessPolicy` and there is no second place for
#: it to drift. NULL reads back as "whatever the dataclass says today".


def _policy_from_row(row: sqlite3.Row) -> ToolAccessPolicy:
    """Rebuild a whole policy from a row, tolerating a pre-migration one.

    A row written before the approval columns existed reads them as absent or
    NULL. Absent means "this deployment never gated anything here", which is an
    empty approval list -- not a reason to fail the replay and start with no
    policy at all.
    """
    keys = row.keys()

    def _value(name: str):
        return row[name] if name in keys else None

    approval_raw = _value("approval_list")
    timeout = _value("approval_timeout_seconds")
    channel = _value("approval_channel")

    optional: dict[str, object] = {}
    if timeout is not None:
        optional["approval_timeout_seconds"] = timeout
    if channel is not None:
        optional["approval_channel"] = channel

    return ToolAccessPolicy(
        allow_list=tuple(json.loads(row["allow_list"])),
        deny_list=tuple(json.loads(row["deny_list"])),
        approval_list=tuple(json.loads(approval_raw)) if approval_raw else (),
        **optional,  # type: ignore[arg-type]
    )


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
            new_conn = sqlite3.connect(self._db_path, check_same_thread=False)
            new_conn.row_factory = sqlite3.Row
            new_conn.execute("PRAGMA journal_mode=WAL")
            new_conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = new_conn
        result: sqlite3.Connection = self._local.connection
        return result

    def _init_schema(self) -> None:
        """Create the tool_access_policies table, and widen an older one."""
        conn = self._get_connection()
        conn.executescript(TAP_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(tool_access_policies)")}
        for column, ddl in _ADDED_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE tool_access_policies ADD COLUMN {column} {ddl}")
                logger.info("sqlite_tap_store_column_added", column=column)
        conn.commit()
        logger.info("sqlite_tap_store_initialized", db_path=self._db_path)

    def set_policy(self, scope: str, target_id: str, policy: ToolAccessPolicy) -> None:
        """Persist a tool access policy (upsert).

        Args:
            scope: "provider", "group", or "member".
            target_id: Provider/group/member identifier.
            policy: The policy to persist, in full -- including the approval
                gate, which the store used to have no column for (#915).
        """
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO tool_access_policies (
                scope, target_id, allow_list, deny_list,
                approval_list, approval_timeout_seconds, approval_channel, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(scope, target_id) DO UPDATE SET
                allow_list = excluded.allow_list,
                deny_list = excluded.deny_list,
                approval_list = excluded.approval_list,
                approval_timeout_seconds = excluded.approval_timeout_seconds,
                approval_channel = excluded.approval_channel,
                updated_at = datetime('now')
            """,
            (
                scope,
                target_id,
                json.dumps(list(policy.allow_list)),
                json.dumps(list(policy.deny_list)),
                json.dumps(list(policy.approval_list)),
                policy.approval_timeout_seconds,
                policy.approval_channel,
            ),
        )
        conn.commit()
        logger.info(
            "tap_policy_set",
            scope=scope,
            target_id=target_id,
            has_approval_list=bool(policy.approval_list),
        )

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
            "SELECT * FROM tool_access_policies WHERE scope = ? AND target_id = ?",
            (scope, target_id),
        ).fetchone()
        if row is None:
            return None
        return _policy_from_row(row)

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

    def list_all_policies(self) -> list[tuple[str, str, ToolAccessPolicy]]:
        """Return all stored policies for startup replay.

        Returns:
            List of (scope, target_id, policy) tuples.
        """
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM tool_access_policies").fetchall()
        return [(row["scope"], row["target_id"], _policy_from_row(row)) for row in rows]

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # noqa: BLE001 -- best-effort checkpoint on close
                pass
            self._local.connection.close()
            self._local.connection = None
