"""SQLite-backed approval repository.

Follows the same async pattern as audit_repository.py.
"""

from datetime import datetime, timezone
from typing import Any, Protocol

from ..models import ApprovalRequest, ApprovalState


class ApprovalRepository(Protocol):
    """Port for approval request persistence."""

    async def save(self, request: ApprovalRequest) -> None: ...

    async def get(self, approval_id: str) -> ApprovalRequest | None: ...

    async def list_pending(
        self, provider_id: str | None = None
    ) -> list[ApprovalRequest]: ...

    async def list_by_state(
        self, state: ApprovalState, provider_id: str | None = None
    ) -> list[ApprovalRequest]: ...

    async def update_state(
        self,
        approval_id: str,
        state: ApprovalState,
        decided_by: str | None,
        decided_at: datetime | None,
        reason: str | None,
    ) -> None: ...


class SqliteApprovalRepository:
    """SQLite implementation of ApprovalRepository."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS approval_requests (
        approval_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        arguments_hash TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        channel TEXT NOT NULL DEFAULT 'dashboard',
        decided_by TEXT,
        decided_at TEXT,
        reason TEXT,
        correlation_id TEXT DEFAULT ''
    )
    """

    CREATE_INDEX_STATE_SQL = """
    CREATE INDEX IF NOT EXISTS idx_approval_state ON approval_requests (state)
    """

    CREATE_INDEX_EXPIRES_SQL = """
    CREATE INDEX IF NOT EXISTS idx_approval_expires ON approval_requests (expires_at)
    """

    def __init__(self, database: Any) -> None:
        """Initialize with a Database instance (same type as audit_repository)."""
        self._db = database
        self._initialized = False

    async def _ensure_table(self) -> None:
        if self._initialized:
            return
        async with self._db.transaction() as conn:
            await conn.execute(self.CREATE_TABLE_SQL)
            await conn.execute(self.CREATE_INDEX_STATE_SQL)
            await conn.execute(self.CREATE_INDEX_EXPIRES_SQL)
        self._initialized = True

    async def save(self, request: ApprovalRequest) -> None:
        await self._ensure_table()
        import json

        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO approval_requests
                (approval_id, provider_id, tool_name, arguments_json,
                 arguments_hash, requested_at, expires_at, state, channel,
                 decided_by, decided_at, reason, correlation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        await self._ensure_table()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_request(row)

    async def list_pending(
        self, provider_id: str | None = None
    ) -> list[ApprovalRequest]:
        return await self.list_by_state(ApprovalState.PENDING, provider_id)

    async def list_by_state(
        self, state: ApprovalState, provider_id: str | None = None
    ) -> list[ApprovalRequest]:
        await self._ensure_table()
        async with self._db.connection() as conn:
            if provider_id:
                cursor = await conn.execute(
                    "SELECT * FROM approval_requests WHERE state = ? AND provider_id = ? ORDER BY requested_at DESC",
                    (state.value, provider_id),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM approval_requests WHERE state = ? ORDER BY requested_at DESC",
                    (state.value,),
                )
            rows = await cursor.fetchall()
            return [self._row_to_request(row) for row in rows]

    async def update_state(
        self,
        approval_id: str,
        state: ApprovalState,
        decided_by: str | None,
        decided_at: datetime | None,
        reason: str | None,
    ) -> None:
        await self._ensure_table()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE approval_requests
                SET state = ?, decided_by = ?, decided_at = ?, reason = ?
                WHERE approval_id = ?
                """,
                (
                    state.value,
                    decided_by,
                    decided_at.isoformat() if decided_at else None,
                    reason,
                    approval_id,
                ),
            )

    @staticmethod
    def _row_to_request(row: Any) -> ApprovalRequest:
        import json

        # aiosqlite rows are tuples indexed by column position
        return ApprovalRequest(
            approval_id=row[0],
            provider_id=row[1],
            tool_name=row[2],
            arguments=json.loads(row[3]),
            arguments_hash=row[4],
            requested_at=datetime.fromisoformat(row[5]),
            expires_at=datetime.fromisoformat(row[6]),
            state=ApprovalState(row[7]),
            channel=row[8],
            decided_by=row[9],
            decided_at=datetime.fromisoformat(row[10]) if row[10] else None,
            reason=row[11],
            correlation_id=row[12] or "",
        )
