"""Database connection management for SQLite persistence.

Provides async-compatible database access with connection pooling,
migrations, and health checking.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import asyncio

import aiosqlite

from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DatabaseConfig:
    """Configuration for database connection.

    Attributes:
        path: Path to SQLite database file. Use ":memory:" for in-memory.
        timeout: Connection timeout in seconds.
        isolation_level: SQLite isolation level.
        check_same_thread: Whether to enforce same-thread access.
        enable_wal: Enable Write-Ahead Logging for better concurrency.
        busy_timeout_ms: Timeout for busy handler in milliseconds.
    """

    path: str = "data/mcp_hangar.db"
    timeout: float = 30.0
    isolation_level: Optional[str] = "DEFERRED"
    check_same_thread: bool = False
    enable_wal: bool = True
    busy_timeout_ms: int = 5000

    def __post_init__(self):
        # Ensure data directory exists for file-based database
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)


class Database:
    """Async SQLite database wrapper with connection pooling.

    Provides:
    - Async connection management
    - Automatic migrations
    - Connection health checking
    - Thread-safe connection pool
    """

    def __init__(self, config: Optional[DatabaseConfig] = None):
        """Initialize database with configuration.

        Args:
            config: Database configuration. Defaults to file-based DB.
        """
        self._config = config or DatabaseConfig()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._migrations_applied = False

    @property
    def config(self) -> DatabaseConfig:
        """Get current database configuration."""
        return self._config

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection.

        Yields:
            Async SQLite connection

        Example:
            async with db.connection() as conn:
                await conn.execute("SELECT * FROM providers")
        """
        conn = await aiosqlite.connect(
            self._config.path,
            timeout=self._config.timeout,
            isolation_level=self._config.isolation_level,
        )
        try:
            # Configure connection
            await conn.execute(f"PRAGMA busy_timeout = {self._config.busy_timeout_ms}")

            if self._config.enable_wal and self._config.path != ":memory:":
                await conn.execute("PRAGMA journal_mode = WAL")

            # Enable foreign keys
            await conn.execute("PRAGMA foreign_keys = ON")

            conn.row_factory = aiosqlite.Row
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection within a transaction.

        Automatically commits on success, rolls back on exception.

        Yields:
            Async SQLite connection
        """
        async with self.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def initialize(self) -> None:
        """Initialize database and run migrations.

        Creates tables and applies any pending migrations.
        Safe to call multiple times - idempotent.
        """
        async with self._lock:
            if self._initialized:
                return

            await self._apply_migrations()
            self._initialized = True
            logger.info(f"Database initialized: {self._config.path}")

    async def _apply_migrations(self) -> None:
        """Apply database migrations."""
        async with self.connection() as conn:
            # Create migrations tracking table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # Get applied migrations
            cursor = await conn.execute("SELECT name FROM _migrations")
            applied = {row[0] for row in await cursor.fetchall()}

            # Apply pending migrations in order
            for migration_name, migration_sql in MIGRATIONS:
                if migration_name not in applied:
                    logger.info(f"Applying migration: {migration_name}")
                    await conn.executescript(migration_sql)
                    await conn.execute(
                        "INSERT INTO _migrations (name) VALUES (?)",
                        (migration_name,),
                    )

            await conn.commit()
            self._migrations_applied = True

    async def health_check(self) -> Dict[str, Any]:
        """Check database health.

        Returns:
            Dictionary with health status and metrics
        """
        try:
            async with self.connection() as conn:
                # Basic connectivity check
                cursor = await conn.execute("SELECT 1")
                await cursor.fetchone()

                # Get database stats
                cursor = await conn.execute("SELECT COUNT(*) FROM provider_configs")
                provider_count = (await cursor.fetchone())[0]

                cursor = await conn.execute("SELECT COUNT(*) FROM audit_log")
                audit_count = (await cursor.fetchone())[0]

                return {
                    "status": "healthy",
                    "database_path": self._config.path,
                    "initialized": self._initialized,
                    "migrations_applied": self._migrations_applied,
                    "provider_count": provider_count,
                    "audit_entries": audit_count,
                }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "database_path": self._config.path,
            }

    async def close(self) -> None:
        """Close database resources.

        For SQLite with aiosqlite, connections are managed per-operation,
        but this method ensures clean shutdown.
        """
        self._initialized = False
        logger.info("Database closed")


# Database migrations - applied in order
MIGRATIONS: List[Tuple[str, str]] = [
    (
        "001_initial_schema",
        """
        -- Provider configurations table
        CREATE TABLE IF NOT EXISTS provider_configs (
            provider_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            config_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Index for listing enabled providers
        CREATE INDEX IF NOT EXISTS idx_provider_configs_enabled
            ON provider_configs(enabled);

        -- Audit log table
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            old_state_json TEXT,
            new_state_json TEXT,
            metadata_json TEXT,
            correlation_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Indexes for audit log queries
        CREATE INDEX IF NOT EXISTS idx_audit_log_entity
            ON audit_log(entity_id, entity_type);
        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
            ON audit_log(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_log_correlation
            ON audit_log(correlation_id) WHERE correlation_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_audit_log_action
            ON audit_log(action, timestamp DESC);
        """,
    ),
    (
        "002_add_provider_metadata",
        """
        -- Add metadata column to provider_configs
        ALTER TABLE provider_configs ADD COLUMN metadata_json TEXT;

        -- Add last_started_at for recovery prioritization
        ALTER TABLE provider_configs ADD COLUMN last_started_at TEXT;

        -- Add failure_count for recovery decisions
        ALTER TABLE provider_configs ADD COLUMN consecutive_failures INTEGER DEFAULT 0;
        """,
    ),
    (
        "003_add_audit_indexes",
        """
        -- Composite index for time-range queries with filters
        CREATE INDEX IF NOT EXISTS idx_audit_log_time_entity
            ON audit_log(timestamp DESC, entity_type);

        -- Index for actor-based queries (who did what)
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor
            ON audit_log(actor, timestamp DESC);
        """,
    ),
]


# Singleton database instance
_database: Optional[Database] = None
_database_lock = threading.Lock()


def get_database(config: Optional[DatabaseConfig] = None) -> Database:
    """Get or create the global database instance.

    Args:
        config: Optional configuration. Only used when creating new instance.

    Returns:
        Database instance
    """
    global _database
    with _database_lock:
        if _database is None:
            _database = Database(config)
        return _database


def set_database(database: Database) -> None:
    """Set the global database instance.

    Useful for testing with custom configurations.

    Args:
        database: Database instance to use
    """
    global _database
    with _database_lock:
        _database = database


async def initialize_database(config: Optional[DatabaseConfig] = None) -> Database:
    """Initialize and return the database.

    Convenience function that gets the database and initializes it.

    Args:
        config: Optional database configuration

    Returns:
        Initialized database instance
    """
    db = get_database(config)
    await db.initialize()
    return db

