"""SQLite implementation of IKnowledgeBase."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ...logging_config import get_logger
from .contracts import AuditEntry, IKnowledgeBase, KnowledgeBaseConfig, MetricEntry, ProviderStateEntry

logger = get_logger(__name__)

# SQL Migrations for SQLite
MIGRATIONS = [
    {
        "version": 1,
        "name": "initial_schema",
        "sql": """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    UNIQUE(provider, tool, arguments_hash)
);

CREATE INDEX IF NOT EXISTS idx_tool_cache_lookup
    ON tool_cache(provider, tool, arguments_hash);
CREATE INDEX IF NOT EXISTS idx_tool_cache_expires
    ON tool_cache(expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    provider TEXT,
    tool TEXT,
    arguments TEXT,
    result_summary TEXT,
    duration_ms INTEGER,
    success INTEGER NOT NULL,
    error_message TEXT,
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_provider
    ON audit_log(provider, timestamp DESC);

CREATE TABLE IF NOT EXISTS provider_state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now')),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_provider_state_provider
    ON provider_state_history(provider_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS provider_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now')),
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    labels TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_provider_metrics_lookup
    ON provider_metrics(provider_id, metric_name, timestamp DESC);
""",
    },
]


class SQLiteKnowledgeBase(IKnowledgeBase):
    """SQLite implementation of knowledge base."""

    def __init__(self, config: KnowledgeBaseConfig):
        self._config = config
        self._db_path = self._parse_dsn(config.dsn)
        self._initialized = False

    def _parse_dsn(self, dsn: str) -> str:
        """Parse DSN to get database path."""
        if dsn.startswith("sqlite://"):
            return dsn.replace("sqlite://", "")
        elif dsn.startswith("sqlite:///"):
            return dsn.replace("sqlite:///", "")
        elif dsn.endswith(".db"):
            return dsn
        else:
            # Default to data directory
            return "data/knowledge_base.db"

    async def initialize(self) -> bool:
        """Create database and run migrations."""
        try:
            # Ensure directory exists
            db_path = Path(self._db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            # Run migrations
            async with aiosqlite.connect(self._db_path) as db:
                await self._run_migrations(db)

            self._initialized = True
            logger.info("sqlite_kb_initialized", path=self._db_path)
            return True

        except Exception as e:
            logger.error("sqlite_kb_init_failed", error=str(e))
            return False

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        """Run pending migrations."""
        # Get current version
        try:
            async with db.execute("SELECT MAX(version) FROM schema_migrations") as cursor:
                row = await cursor.fetchone()
                current_version = row[0] if row and row[0] else 0
        except (aiosqlite.OperationalError, aiosqlite.DatabaseError):
            # Table doesn't exist yet - this is first run
            current_version = 0

        for migration in MIGRATIONS:
            if migration["version"] <= current_version:
                continue

            logger.info(
                "applying_migration",
                version=migration["version"],
                name=migration["name"],
            )

            await db.executescript(migration["sql"])
            await db.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration["version"], migration["name"]),
            )
            await db.commit()

        # Get final version
        async with db.execute("SELECT MAX(version) FROM schema_migrations") as cursor:
            row = await cursor.fetchone()
            final_version = row[0] if row and row[0] else 0

        logger.info("sqlite_kb_schema_ready", version=final_version)

    async def close(self) -> None:
        """No persistent connections to close for SQLite."""
        self._initialized = False
        logger.info("sqlite_kb_closed")

    async def is_healthy(self) -> bool:
        """Check if database is accessible."""
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            return True
        except (aiosqlite.Error, OSError) as e:
            logger.debug("sqlite_health_check_failed", error=str(e))
            return False

    def _hash_arguments(self, arguments: dict) -> str:
        """Create hash of arguments for cache key."""
        serialized = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:32]

    # === Cache Operations ===

    async def cache_get(self, provider: str, tool: str, arguments: dict) -> Optional[dict]:
        args_hash = self._hash_arguments(arguments)
        now = datetime.now(timezone.utc).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    """
                    SELECT result FROM tool_cache
                    WHERE provider = ? AND tool = ? AND arguments_hash = ?
                      AND expires_at > ?
                    """,
                    (provider, tool, args_hash, now),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        logger.debug("cache_hit", provider=provider, tool=tool)
                        return json.loads(row[0])
                    return None
        except Exception as e:
            logger.warning("cache_get_failed", error=str(e))
            return None

    async def cache_set(
        self,
        provider: str,
        tool: str,
        arguments: dict,
        result: Any,
        ttl_s: Optional[int] = None,
    ) -> bool:
        args_hash = self._hash_arguments(arguments)
        ttl = ttl_s or self._config.cache_ttl_s
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO tool_cache (provider, tool, arguments_hash, result, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (provider, tool, arguments_hash)
                    DO UPDATE SET result = excluded.result,
                                  expires_at = excluded.expires_at,
                                  created_at = datetime('now')
                    """,
                    (provider, tool, args_hash, json.dumps(result, default=str), expires_at),
                )
                await db.commit()
                return True
        except Exception as e:
            logger.warning("cache_set_failed", error=str(e))
            return False

    async def cache_invalidate(self, provider: Optional[str] = None, tool: Optional[str] = None) -> int:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                if provider and tool:
                    cursor = await db.execute(
                        "DELETE FROM tool_cache WHERE provider = ? AND tool = ?",
                        (provider, tool),
                    )
                elif provider:
                    cursor = await db.execute("DELETE FROM tool_cache WHERE provider = ?", (provider,))
                else:
                    cursor = await db.execute("DELETE FROM tool_cache")
                await db.commit()
                return cursor.rowcount
        except Exception as e:
            logger.warning("cache_invalidate_failed", error=str(e))
            return 0

    async def cache_cleanup(self) -> int:
        now = datetime.now(timezone.utc).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute("DELETE FROM tool_cache WHERE expires_at < ?", (now,))
                await db.commit()
                logger.info("cache_cleanup", deleted=cursor.rowcount)
                return cursor.rowcount
        except Exception as e:
            logger.warning("cache_cleanup_failed", error=str(e))
            return 0

    # === Audit Operations ===

    async def audit_log(self, entry: AuditEntry) -> bool:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO audit_log
                        (event_type, provider, tool, arguments, result_summary,
                         duration_ms, success, error_message, correlation_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.event_type,
                        entry.provider,
                        entry.tool,
                        json.dumps(entry.arguments, default=str) if entry.arguments else None,
                        entry.result_summary,
                        entry.duration_ms,
                        1 if entry.success else 0,
                        entry.error_message,
                        entry.correlation_id,
                    ),
                )
                await db.commit()
                return True
        except Exception as e:
            logger.warning("audit_log_failed", error=str(e))
            return False

    async def audit_query(
        self,
        provider: Optional[str] = None,
        tool: Optional[str] = None,
        success: Optional[bool] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        try:
            conditions = []
            params = []

            if provider:
                conditions.append("provider = ?")
                params.append(provider)
            if tool:
                conditions.append("tool = ?")
                params.append(tool)
            if success is not None:
                conditions.append("success = ?")
                params.append(1 if success else 0)
            if since:
                conditions.append("timestamp >= ?")
                params.append(since.isoformat())

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    f"""
                    SELECT event_type, provider, tool, arguments, result_summary,
                           duration_ms, success, error_message, correlation_id, timestamp
                    FROM audit_log
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()

                    return [
                        AuditEntry(
                            event_type=row[0],
                            provider=row[1],
                            tool=row[2],
                            arguments=json.loads(row[3]) if row[3] else None,
                            result_summary=row[4],
                            duration_ms=row[5],
                            success=bool(row[6]),
                            error_message=row[7],
                            correlation_id=row[8],
                            timestamp=datetime.fromisoformat(row[9]) if row[9] else None,
                        )
                        for row in rows
                    ]
        except Exception as e:
            logger.warning("audit_query_failed", error=str(e))
            return []

    async def audit_stats(self, hours: int = 24) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as error_count,
                        COUNT(DISTINCT provider) as providers,
                        COUNT(DISTINCT tool) as tools,
                        AVG(duration_ms) as avg_duration_ms
                    FROM audit_log
                    WHERE timestamp > ?
                    """,
                    (since,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return {
                            "total": row[0] or 0,
                            "success_count": row[1] or 0,
                            "error_count": row[2] or 0,
                            "providers": row[3] or 0,
                            "tools": row[4] or 0,
                            "avg_duration_ms": row[5],
                        }
                    return {}
        except Exception as e:
            logger.warning("audit_stats_failed", error=str(e))
            return {}

    # === Provider State Operations ===

    async def record_state_change(self, entry: ProviderStateEntry) -> bool:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO provider_state_history
                        (provider_id, old_state, new_state, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entry.provider_id, entry.old_state, entry.new_state, entry.reason),
                )
                await db.commit()
                return True
        except Exception as e:
            logger.warning("record_state_failed", error=str(e))
            return False

    async def get_state_history(self, provider_id: str, limit: int = 100) -> list[ProviderStateEntry]:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    """
                    SELECT provider_id, old_state, new_state, reason, timestamp
                    FROM provider_state_history
                    WHERE provider_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (provider_id, limit),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        ProviderStateEntry(
                            provider_id=row[0],
                            old_state=row[1],
                            new_state=row[2],
                            reason=row[3],
                            timestamp=datetime.fromisoformat(row[4]) if row[4] else None,
                        )
                        for row in rows
                    ]
        except Exception as e:
            logger.warning("get_state_history_failed", error=str(e))
            return []

    # === Metrics Operations ===

    async def record_metric(self, entry: MetricEntry) -> bool:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO provider_metrics
                        (provider_id, metric_name, metric_value, labels)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        entry.provider_id,
                        entry.metric_name,
                        entry.metric_value,
                        json.dumps(entry.labels or {}),
                    ),
                )
                await db.commit()
                return True
        except Exception as e:
            logger.warning("record_metric_failed", error=str(e))
            return False

    async def get_metrics(
        self,
        provider_id: str,
        metric_name: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[MetricEntry]:
        try:
            conditions = ["provider_id = ?"]
            params = [provider_id]

            if metric_name:
                conditions.append("metric_name = ?")
                params.append(metric_name)
            if since:
                conditions.append("timestamp >= ?")
                params.append(since.isoformat())

            params.append(limit)

            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    f"""
                    SELECT provider_id, metric_name, metric_value, labels, timestamp
                    FROM provider_metrics
                    WHERE {" AND ".join(conditions)}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        MetricEntry(
                            provider_id=row[0],
                            metric_name=row[1],
                            metric_value=row[2],
                            labels=json.loads(row[3]) if row[3] else None,
                            timestamp=datetime.fromisoformat(row[4]) if row[4] else None,
                        )
                        for row in rows
                    ]
        except Exception as e:
            logger.warning("get_metrics_failed", error=str(e))
            return []
