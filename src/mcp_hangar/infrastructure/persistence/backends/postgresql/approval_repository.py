"""PostgreSQL adapter for `ApprovalRepository`.

The approval gate is a security control, not a metrics sink: every row here
is one request for consent and, once decided, the record of who decided it
and why. Two things carried over from the SQLite reference on purpose rather
than by accident:

- `arguments` is stored in a `JSONB` column but always handed back as the
  same `dict` the SQLite TEXT+`json.dumps` round trip produces, so a caller
  cannot tell which backend answered.
- `update_state` on an `approval_id` that does not exist is a silent no-op,
  same as the SQLite `UPDATE ... WHERE approval_id = ?` it mirrors. It does
  not raise and does not report zero rows affected -- callers that need to
  know whether a decision landed must `get()` afterwards.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
import zlib

from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_COLUMNS = (
    "approval_id, provider_id, tool_name, arguments_json, arguments_hash, "
    "requested_at, expires_at, state, channel, decided_by, decided_at, "
    "reason, correlation_id, requested_by, tenant_id"
)

# Transaction-scoped advisory-lock key for `_ensure_table`. `CREATE TABLE IF
# NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` are not race-free under genuinely
# concurrent execution in Postgres: two processes can both see "missing" and
# collide on the DDL. The SQLite reference never needs this -- it is
# single-writer by construction (a fresh connection per call, closed after).
# A fixed, deterministic (non-hash-randomized) key derived from a stable
# string keeps this lock name stable across process restarts and Python
# versions, which is required for it to actually serialize anything.
_SCHEMA_LOCK_KEY = zlib.crc32(b"mcp_hangar.approval_requests.schema") & 0x7FFFFFFF


class PostgresApprovalRepository:
    """PostgreSQL-backed store for the approval gate's request/decision records.

    Exists so a gated tool call's pending approval, and the eventual decision
    on it, survive a process restart in a multi-node deployment the same way
    the SQLite backend makes them survive one on a single host. Table creation
    is lazy -- deferred to first use, like the reference -- rather than done in
    `__init__`, so constructing this object never touches the database.
    """

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS approval_requests (
        approval_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json JSONB NOT NULL,
        arguments_hash TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        channel TEXT NOT NULL DEFAULT 'dashboard',
        decided_by TEXT,
        decided_at TEXT,
        reason TEXT,
        correlation_id TEXT DEFAULT '',
        requested_by TEXT,
        tenant_id TEXT
    )
    """

    # Idempotent migration for tables created before requested_by / tenant_id
    # existed. Unlike SQLite's ALTER TABLE ADD COLUMN, Postgres supports
    # `IF NOT EXISTS` directly, so this needs no try/except swallow -- but the
    # reason it exists is the same one as the reference: a durable store from
    # an earlier version would otherwise silently drop the tenant binding the
    # resolve/list scoping depends on.
    MIGRATIONS_SQL = (
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS requested_by TEXT",
        "ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS tenant_id TEXT",
    )

    CREATE_INDEX_STATE_SQL = "CREATE INDEX IF NOT EXISTS idx_approval_state ON approval_requests (state)"

    CREATE_INDEX_EXPIRES_SQL = "CREATE INDEX IF NOT EXISTS idx_approval_expires ON approval_requests (expires_at)"

    def __init__(self, connection_factory: IConnectionFactory) -> None:
        """Initialize with a shared connection factory.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This adapter knows
                SQL; it deliberately does not know psycopg2, pooling, or how a
                connection is obtained.
        """
        self._connections = connection_factory
        self._initialized = False

    def _ensure_table(self) -> None:
        if self._initialized:
            return
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    # Transaction-scoped: released automatically on the
                    # commit below (or on the rollback in `except`), so a
                    # crash between acquiring it and committing cannot leave
                    # it held. Serializes concurrent first-boot schema setup
                    # across processes/nodes sharing this database.
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
                    cur.execute(self.CREATE_TABLE_SQL)
                    cur.execute(self.CREATE_INDEX_STATE_SQL)
                    cur.execute(self.CREATE_INDEX_EXPIRES_SQL)
                    for migration in self.MIGRATIONS_SQL:
                        cur.execute(migration)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self._initialized = True

    async def save(self, request: ApprovalRequest) -> None:
        self._ensure_table()
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO approval_requests
                        ({_COLUMNS})
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            request.approval_id,
                            request.provider_id,
                            request.tool_name,
                            json.dumps(request.arguments, default=str),
                            request.arguments_hash,
                            request.requested_at.isoformat(),
                            request.expires_at.isoformat(),
                            request.state.value,
                            request.channel,
                            request.decided_by,
                            request.decided_at.isoformat() if request.decided_at else None,
                            request.reason,
                            request.correlation_id,
                            request.requested_by,
                            request.tenant_id,
                        ),
                    )
                conn.commit()
            except Exception:
                # Without this, a failed INSERT (e.g. a duplicate
                # `approval_id`) leaves the pooled connection sitting in an
                # aborted transaction; the *next* caller to borrow it from
                # the pool would have every statement rejected with
                # "current transaction is aborted" until something rolls it
                # back. The SQLite reference never hits this because each
                # call opens a fresh connection and closes it afterwards.
                conn.rollback()
                raise

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        self._ensure_table()
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT {_COLUMNS} FROM approval_requests WHERE approval_id = %s",
                        (approval_id,),
                    )
                    row = cur.fetchone()
            finally:
                # A SELECT still opens a transaction under psycopg2's default
                # (non-autocommit) mode. Unlike the SQLite reference -- which
                # opens a brand-new connection per call and always closes it,
                # so there is nothing to leak -- this connection goes back to
                # a shared pool. Without ending the transaction here it would
                # be returned "idle in transaction": invisible to VACUUM,
                # holding a snapshot open indefinitely, for as long as the
                # connection happens to sit unborrowed in the pool.
                conn.rollback()
            if row is None:
                return None
            return self._row_to_request(row)

    async def list_pending(self, provider_id: str | None = None) -> list[ApprovalRequest]:
        return await self.list_by_state(ApprovalState.PENDING, provider_id)

    async def list_by_state(self, state: ApprovalState, provider_id: str | None = None) -> list[ApprovalRequest]:
        self._ensure_table()
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    if provider_id:
                        cur.execute(
                            f"SELECT {_COLUMNS} FROM approval_requests "
                            "WHERE state = %s AND provider_id = %s ORDER BY requested_at DESC",
                            (state.value, provider_id),
                        )
                    else:
                        cur.execute(
                            f"SELECT {_COLUMNS} FROM approval_requests WHERE state = %s ORDER BY requested_at DESC",
                            (state.value,),
                        )
                    rows = cur.fetchall()
            finally:
                # See `get()` -- ends the read transaction before the
                # connection goes back to the pool.
                conn.rollback()
            return [self._row_to_request(row) for row in rows]

    async def update_state(
        self,
        approval_id: str,
        state: ApprovalState,
        decided_by: str | None,
        decided_at: datetime | None,
        reason: str | None,
    ) -> None:
        self._ensure_table()
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE approval_requests
                        SET state = %s, decided_by = %s, decided_at = %s, reason = %s
                        WHERE approval_id = %s
                        """,
                        (
                            state.value,
                            decided_by,
                            decided_at.isoformat() if decided_at else None,
                            reason,
                            approval_id,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _row_to_request(row: Any) -> ApprovalRequest:
        # JSONB round-trips through psycopg2 as an already-parsed dict on a
        # live connection; a raw string (e.g. from a test double, or a driver
        # configured without the JSON adapter) is decoded the same as SQLite's
        # TEXT column would be. Either way the caller gets a dict.
        arguments = row[3]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        return ApprovalRequest(
            approval_id=row[0],
            provider_id=row[1],
            tool_name=row[2],
            arguments=arguments,
            arguments_hash=row[4],
            requested_at=datetime.fromisoformat(row[5]),
            expires_at=datetime.fromisoformat(row[6]),
            state=ApprovalState(row[7]),
            channel=row[8],
            decided_by=row[9],
            decided_at=datetime.fromisoformat(row[10]) if row[10] else None,
            reason=row[11],
            correlation_id=row[12] or "",
            requested_by=row[13] if len(row) > 13 else None,
            tenant_id=row[14] if len(row) > 14 else None,
        )
