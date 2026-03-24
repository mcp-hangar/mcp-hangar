"""Unit of Work implementation for transactional consistency.

Provides transaction management across multiple repositories,
ensuring atomic commits or rollbacks.
"""

from datetime import datetime, UTC
import json
from typing import Any

import aiosqlite

from ...domain.contracts.persistence import AuditAction, AuditEntry, PersistenceError, ProviderConfigSnapshot
from ...logging_config import get_logger
from .database import Database

logger = get_logger(__name__)


class TransactionalProviderConfigRepository:
    """Provider config repository that operates within a transaction.

    Uses a shared connection for transactional consistency.
    """

    def __init__(self, conn: aiosqlite.Connection):
        """Initialize with shared connection.

        Args:
            conn: SQLite connection (within transaction)
        """
        self._conn = conn

    async def save(self, config: ProviderConfigSnapshot) -> None:
        """Save provider configuration within transaction."""
        cursor = await self._conn.execute(
            "SELECT version FROM provider_configs WHERE provider_id = ?",
            (config.provider_id,),
        )
        row = await cursor.fetchone()

        config_json = json.dumps(config.to_dict())
        now = datetime.now(UTC).isoformat()

        if row is None:
            await self._conn.execute(
                """
                INSERT INTO provider_configs
                (provider_id, mode, config_json, enabled, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    config.provider_id,
                    config.mode,
                    config_json,
                    1 if config.enabled else 0,
                    now,
                    now,
                ),
            )
        else:
            await self._conn.execute(
                """
                UPDATE provider_configs
                SET mode = ?, config_json = ?, enabled = ?,
                    version = version + 1, updated_at = ?
                WHERE provider_id = ?
                """,
                (
                    config.mode,
                    config_json,
                    1 if config.enabled else 0,
                    now,
                    config.provider_id,
                ),
            )

    async def get(self, provider_id: str) -> ProviderConfigSnapshot | None:
        """Retrieve provider configuration within transaction."""
        cursor = await self._conn.execute(
            "SELECT config_json FROM provider_configs WHERE provider_id = ?",
            (provider_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        config_data = json.loads(row[0])
        return ProviderConfigSnapshot.from_dict(config_data)

    async def get_all(self) -> list[ProviderConfigSnapshot]:
        """Retrieve all provider configurations within transaction."""
        cursor = await self._conn.execute("SELECT config_json FROM provider_configs WHERE enabled = 1")
        rows = await cursor.fetchall()

        configs = []
        for row in rows:
            try:
                config_data = json.loads(row[0])
                configs.append(ProviderConfigSnapshot.from_dict(config_data))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("invalid_config_snapshot", error=str(e), raw_data=row[0][:100])
                continue

        return configs

    async def delete(self, provider_id: str) -> bool:
        """Delete provider configuration within transaction."""
        result = await self._conn.execute(
            """
            UPDATE provider_configs
            SET enabled = 0, updated_at = ?
            WHERE provider_id = ? AND enabled = 1
            """,
            (datetime.now(UTC).isoformat(), provider_id),
        )
        return result.rowcount > 0

    async def exists(self, provider_id: str) -> bool:
        """Check if provider exists within transaction."""
        cursor = await self._conn.execute(
            "SELECT 1 FROM provider_configs WHERE provider_id = ? AND enabled = 1",
            (provider_id,),
        )
        row = await cursor.fetchone()
        return row is not None


class TransactionalAuditRepository:
    """Audit repository that operates within a transaction.

    Uses a shared connection for transactional consistency.
    """

    def __init__(self, conn: aiosqlite.Connection):
        """Initialize with shared connection.

        Args:
            conn: SQLite connection (within transaction)
        """
        self._conn = conn

    async def append(self, entry: AuditEntry) -> None:
        """Append audit entry within transaction."""
        await self._conn.execute(
            """
            INSERT INTO audit_log
            (entity_id, entity_type, action, actor, timestamp,
             old_state_json, new_state_json, metadata_json, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entity_id,
                entry.entity_type,
                entry.action.value,
                entry.actor,
                entry.timestamp.isoformat(),
                json.dumps(entry.old_state) if entry.old_state else None,
                json.dumps(entry.new_state) if entry.new_state else None,
                json.dumps(entry.metadata) if entry.metadata else None,
                entry.correlation_id,
            ),
        )

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries within transaction (read-only operation)."""
        if entity_type:
            cursor = await self._conn.execute(
                """
                SELECT entity_id, entity_type, action, actor, timestamp,
                       old_state_json, new_state_json, metadata_json, correlation_id
                FROM audit_log
                WHERE entity_id = ? AND entity_type = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (entity_id, entity_type, limit, offset),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT entity_id, entity_type, action, actor, timestamp,
                       old_state_json, new_state_json, metadata_json, correlation_id
                FROM audit_log
                WHERE entity_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (entity_id, limit, offset),
            )

        rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: str | None = None,
        action: AuditAction | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        """Get audit entries by time range within transaction."""
        query = """
            SELECT entity_id, entity_type, action, actor, timestamp,
                   old_state_json, new_state_json, metadata_json, correlation_id
            FROM audit_log
            WHERE timestamp BETWEEN ? AND ?
        """
        params: list = [start.isoformat(), end.isoformat()]

        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)

        if action:
            query += " AND action = ?"
            params.append(action.value)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def get_by_correlation_id(self, correlation_id: str) -> list[AuditEntry]:
        """Get audit entries by correlation ID within transaction."""
        cursor = await self._conn.execute(
            """
            SELECT entity_id, entity_type, action, actor, timestamp,
                   old_state_json, new_state_json, metadata_json, correlation_id
            FROM audit_log
            WHERE correlation_id = ?
            ORDER BY timestamp ASC
            """,
            (correlation_id,),
        )

        rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row) -> AuditEntry:
        """Convert database row to AuditEntry."""
        return AuditEntry(
            entity_id=row[0],
            entity_type=row[1],
            action=AuditAction(row[2]),
            actor=row[3],
            timestamp=datetime.fromisoformat(row[4]),
            old_state=json.loads(row[5]) if row[5] else None,
            new_state=json.loads(row[6]) if row[6] else None,
            metadata=json.loads(row[7]) if row[7] else {},
            correlation_id=row[8],
        )


class SQLiteUnitOfWork:
    """SQLite implementation of Unit of Work pattern.

    Manages transactions across provider config and audit repositories,
    ensuring atomic commits or rollbacks.

    Usage:
        async with SQLiteUnitOfWork(database) as uow:
            await uow.providers.save(config)
            await uow.audit.append(entry)
            await uow.commit()
    """

    def __init__(self, database: Database):
        """Initialize with database connection.

        Args:
            database: Database instance for connections
        """
        self._db = database
        self._conn: aiosqlite.Connection | None = None
        self._providers: TransactionalProviderConfigRepository | None = None
        self._audit: TransactionalAuditRepository | None = None
        self._committed = False

    async def __aenter__(self) -> "SQLiteUnitOfWork":
        """Begin transaction."""
        self._conn = await aiosqlite.connect(
            self._db.config.path,
            timeout=self._db.config.timeout,
            isolation_level="DEFERRED",
        )

        # Configure connection
        await self._conn.execute(f"PRAGMA busy_timeout = {self._db.config.busy_timeout_ms}")
        await self._conn.execute("PRAGMA foreign_keys = ON")

        # Create transactional repositories
        self._providers = TransactionalProviderConfigRepository(self._conn)
        self._audit = TransactionalAuditRepository(self._conn)

        logger.debug("UnitOfWork: Transaction started")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """End transaction - commit on success, rollback on exception."""
        try:
            if exc_type is not None:
                # Exception occurred - rollback
                await self.rollback()
                logger.debug(f"UnitOfWork: Transaction rolled back due to {exc_type}")
            elif not self._committed:
                # No explicit commit - auto-commit
                await self.commit()
        finally:
            if self._conn:
                await self._conn.close()
                self._conn = None

    async def commit(self) -> None:
        """Explicitly commit the transaction."""
        if self._conn and not self._committed:
            await self._conn.commit()
            self._committed = True
            logger.debug("UnitOfWork: Transaction committed")

    async def rollback(self) -> None:
        """Explicitly rollback the transaction."""
        if self._conn:
            await self._conn.rollback()
            self._committed = True  # Prevent auto-commit
            logger.debug("UnitOfWork: Transaction rolled back")

    @property
    def providers(self) -> TransactionalProviderConfigRepository:
        """Access provider config repository within transaction."""
        if self._providers is None:
            raise PersistenceError("UnitOfWork not entered - use 'async with'")
        return self._providers

    @property
    def audit(self) -> TransactionalAuditRepository:
        """Access audit repository within transaction."""
        if self._audit is None:
            raise PersistenceError("UnitOfWork not entered - use 'async with'")
        return self._audit


class InMemoryUnitOfWork:
    """In-memory implementation of Unit of Work for testing.

    Provides transaction-like behavior with commit/rollback support.
    """

    def __init__(
        self,
        providers,  # InMemoryProviderConfigRepository
        audit,  # InMemoryAuditRepository
    ):
        """Initialize with in-memory repositories.

        Args:
            providers: In-memory provider config repository
            audit: In-memory audit repository
        """
        self._providers = providers
        self._audit = audit
        self._snapshot: dict[str, Any] | None = None

    async def __aenter__(self) -> "InMemoryUnitOfWork":
        """Begin transaction by taking snapshot."""
        # Take snapshot for potential rollback
        self._snapshot = {
            "providers": dict(self._providers._configs),
            "provider_versions": dict(self._providers._versions),
            "audit": list(self._audit._entries),
        }
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """End transaction - rollback on exception."""
        if exc_type is not None:
            await self.rollback()
        self._snapshot = None

    async def commit(self) -> None:
        """Commit - clear snapshot (changes already in memory)."""
        self._snapshot = None

    async def rollback(self) -> None:
        """Rollback to snapshot state."""
        if self._snapshot:
            self._providers._configs = self._snapshot["providers"]
            self._providers._versions = self._snapshot["provider_versions"]
            self._audit._entries = self._snapshot["audit"]

    @property
    def providers(self):
        """Access provider config repository."""
        return self._providers

    @property
    def audit(self):
        """Access audit repository."""
        return self._audit
