"""PostgreSQL implementation of IKnowledgeBase."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ...logging_config import get_logger
from .contracts import (
    AuditEntry,
    IKnowledgeBase,
    KnowledgeBaseConfig,
    MetricEntry,
    ProviderStateEntry,
)

logger = get_logger(__name__)

# SQL Migrations for PostgreSQL
MIGRATIONS = [
    {
        "version": 1,
        "name": "initial_schema",
        "sql": """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_cache (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE(provider, tool, arguments_hash)
);

CREATE INDEX IF NOT EXISTS idx_tool_cache_lookup 
    ON tool_cache(provider, tool, arguments_hash);
CREATE INDEX IF NOT EXISTS idx_tool_cache_expires 
    ON tool_cache(expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    event_type TEXT NOT NULL,
    provider TEXT,
    tool TEXT,
    arguments JSONB,
    result_summary TEXT,
    duration_ms INTEGER,
    success BOOLEAN NOT NULL,
    error_message TEXT,
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp 
    ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_provider 
    ON audit_log(provider, timestamp DESC);

CREATE TABLE IF NOT EXISTS provider_state_history (
    id SERIAL PRIMARY KEY,
    provider_id TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_provider_state_provider 
    ON provider_state_history(provider_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS provider_metrics (
    id SERIAL PRIMARY KEY,
    provider_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    labels JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_provider_metrics_lookup 
    ON provider_metrics(provider_id, metric_name, timestamp DESC);
""",
    },
    {
        "version": 2,
        "name": "cleanup_function",
        "sql": """
CREATE OR REPLACE FUNCTION cleanup_expired_cache() RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM tool_cache WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
""",
    },
]


class PostgresKnowledgeBase(IKnowledgeBase):
    """PostgreSQL implementation of knowledge base."""

    def __init__(self, config: KnowledgeBaseConfig):
        self._config = config
        self._pool = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize connection pool and run migrations."""
        try:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self._config.dsn,
                min_size=1,
                max_size=self._config.pool_size,
                command_timeout=10,
            )

            # Run migrations
            await self._run_migrations()

            self._initialized = True
            logger.info(
                "postgres_kb_initialized",
                pool_size=self._config.pool_size,
            )
            return True

        except ImportError:
            logger.error("asyncpg_not_installed", hint="pip install asyncpg")
            return False
        except Exception as e:
            logger.error("postgres_kb_init_failed", error=str(e))
            return False

    async def _run_migrations(self) -> None:
        """Run pending migrations."""
        async with self._pool.acquire() as conn:
            # Get current version
            try:
                version = await conn.fetchval(
                    "SELECT MAX(version) FROM schema_migrations"
                )
            except Exception:
                version = 0

            current_version = version or 0

            for migration in MIGRATIONS:
                if migration["version"] <= current_version:
                    continue

                logger.info(
                    "applying_migration",
                    version=migration["version"],
                    name=migration["name"],
                )

                await conn.execute(migration["sql"])
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                    migration["version"],
                    migration["name"],
                )

            final_version = await conn.fetchval(
                "SELECT MAX(version) FROM schema_migrations"
            )
            logger.info("postgres_kb_schema_ready", version=final_version)

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._initialized = False
            logger.info("postgres_kb_closed")

    async def is_healthy(self) -> bool:
        """Check if database is reachable."""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    def _hash_arguments(self, arguments: dict) -> str:
        """Create hash of arguments for cache key."""
        serialized = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:32]

    # === Cache Operations ===

    async def cache_get(
        self, provider: str, tool: str, arguments: dict
    ) -> Optional[dict]:
        if not self._pool:
            return None

        args_hash = self._hash_arguments(arguments)

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT result FROM tool_cache
                    WHERE provider = $1 AND tool = $2 AND arguments_hash = $3
                      AND expires_at > NOW()
                    """,
                    provider,
                    tool,
                    args_hash,
                )
                if row:
                    logger.debug("cache_hit", provider=provider, tool=tool)
                    return json.loads(row["result"])
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
        if not self._pool:
            return False

        args_hash = self._hash_arguments(arguments)
        ttl = ttl_s or self._config.cache_ttl_s
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO tool_cache (provider, tool, arguments_hash, result, expires_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (provider, tool, arguments_hash)
                    DO UPDATE SET result = $4, expires_at = $5, created_at = NOW()
                    """,
                    provider,
                    tool,
                    args_hash,
                    json.dumps(result, default=str),
                    expires_at,
                )
                return True
        except Exception as e:
            logger.warning("cache_set_failed", error=str(e))
            return False

    async def cache_invalidate(
        self, provider: Optional[str] = None, tool: Optional[str] = None
    ) -> int:
        if not self._pool:
            return 0

        try:
            async with self._pool.acquire() as conn:
                if provider and tool:
                    result = await conn.execute(
                        "DELETE FROM tool_cache WHERE provider = $1 AND tool = $2",
                        provider,
                        tool,
                    )
                elif provider:
                    result = await conn.execute(
                        "DELETE FROM tool_cache WHERE provider = $1", provider
                    )
                else:
                    result = await conn.execute("DELETE FROM tool_cache")
                # Parse "DELETE N" to get count
                return int(result.split()[-1]) if result else 0
        except Exception as e:
            logger.warning("cache_invalidate_failed", error=str(e))
            return 0

    async def cache_cleanup(self) -> int:
        if not self._pool:
            return 0

        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT cleanup_expired_cache()")
                logger.info("cache_cleanup", deleted=result)
                return result or 0
        except Exception as e:
            logger.warning("cache_cleanup_failed", error=str(e))
            return 0

    # === Audit Operations ===

    async def audit_log(self, entry: AuditEntry) -> bool:
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_log
                        (event_type, provider, tool, arguments, result_summary,
                         duration_ms, success, error_message, correlation_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    entry.event_type,
                    entry.provider,
                    entry.tool,
                    json.dumps(entry.arguments, default=str) if entry.arguments else None,
                    entry.result_summary,
                    entry.duration_ms,
                    entry.success,
                    entry.error_message,
                    entry.correlation_id,
                )
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
        if not self._pool:
            return []

        try:
            conditions = []
            params = []
            param_idx = 1

            if provider:
                conditions.append(f"provider = ${param_idx}")
                params.append(provider)
                param_idx += 1
            if tool:
                conditions.append(f"tool = ${param_idx}")
                params.append(tool)
                param_idx += 1
            if success is not None:
                conditions.append(f"success = ${param_idx}")
                params.append(success)
                param_idx += 1
            if since:
                conditions.append(f"timestamp >= ${param_idx}")
                params.append(since)
                param_idx += 1

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT event_type, provider, tool, arguments, result_summary,
                           duration_ms, success, error_message, correlation_id, timestamp
                    FROM audit_log
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT ${param_idx}
                    """,
                    *params,
                )

                return [
                    AuditEntry(
                        event_type=row["event_type"],
                        provider=row["provider"],
                        tool=row["tool"],
                        arguments=json.loads(row["arguments"]) if row["arguments"] else None,
                        result_summary=row["result_summary"],
                        duration_ms=row["duration_ms"],
                        success=row["success"],
                        error_message=row["error_message"],
                        correlation_id=row["correlation_id"],
                        timestamp=row["timestamp"],
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.warning("audit_query_failed", error=str(e))
            return []

    async def audit_stats(self, hours: int = 24) -> dict:
        if not self._pool:
            return {}

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE success) as success_count,
                        COUNT(*) FILTER (WHERE NOT success) as error_count,
                        COUNT(DISTINCT provider) as providers,
                        COUNT(DISTINCT tool) as tools,
                        AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL) as avg_duration_ms
                    FROM audit_log
                    WHERE timestamp > NOW() - INTERVAL '{hours} hours'
                    """
                )
                return dict(row) if row else {}
        except Exception as e:
            logger.warning("audit_stats_failed", error=str(e))
            return {}

    # === Provider State Operations ===

    async def record_state_change(self, entry: ProviderStateEntry) -> bool:
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO provider_state_history
                        (provider_id, old_state, new_state, reason)
                    VALUES ($1, $2, $3, $4)
                    """,
                    entry.provider_id,
                    entry.old_state,
                    entry.new_state,
                    entry.reason,
                )
                return True
        except Exception as e:
            logger.warning("record_state_failed", error=str(e))
            return False

    async def get_state_history(
        self, provider_id: str, limit: int = 100
    ) -> list[ProviderStateEntry]:
        if not self._pool:
            return []

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT provider_id, old_state, new_state, reason, timestamp
                    FROM provider_state_history
                    WHERE provider_id = $1
                    ORDER BY timestamp DESC
                    LIMIT $2
                    """,
                    provider_id,
                    limit,
                )
                return [
                    ProviderStateEntry(
                        provider_id=row["provider_id"],
                        old_state=row["old_state"],
                        new_state=row["new_state"],
                        reason=row["reason"],
                        timestamp=row["timestamp"],
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.warning("get_state_history_failed", error=str(e))
            return []

    # === Metrics Operations ===

    async def record_metric(self, entry: MetricEntry) -> bool:
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO provider_metrics
                        (provider_id, metric_name, metric_value, labels)
                    VALUES ($1, $2, $3, $4)
                    """,
                    entry.provider_id,
                    entry.metric_name,
                    entry.metric_value,
                    json.dumps(entry.labels or {}),
                )
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
        if not self._pool:
            return []

        try:
            conditions = ["provider_id = $1"]
            params = [provider_id]
            param_idx = 2

            if metric_name:
                conditions.append(f"metric_name = ${param_idx}")
                params.append(metric_name)
                param_idx += 1
            if since:
                conditions.append(f"timestamp >= ${param_idx}")
                params.append(since)
                param_idx += 1

            params.append(limit)

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT provider_id, metric_name, metric_value, labels, timestamp
                    FROM provider_metrics
                    WHERE {' AND '.join(conditions)}
                    ORDER BY timestamp DESC
                    LIMIT ${param_idx}
                    """,
                    *params,
                )
                return [
                    MetricEntry(
                        provider_id=row["provider_id"],
                        metric_name=row["metric_name"],
                        metric_value=row["metric_value"],
                        labels=json.loads(row["labels"]) if row["labels"] else None,
                        timestamp=row["timestamp"],
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.warning("get_metrics_failed", error=str(e))
            return []

