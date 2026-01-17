"""Common database utilities for SQLite and PostgreSQL.

Provides shared connection management, schema migrations, and utilities
that can be reused across different stores (auth, events, knowledge base).
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any, Generator, Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SQLiteConfig:
    """SQLite database configuration.

    Attributes:
        path: Path to database file. Use ":memory:" for in-memory.
        enable_wal: Enable Write-Ahead Logging for better concurrency.
        busy_timeout_ms: Timeout for busy handler in milliseconds.
        foreign_keys: Enable foreign key constraints.
    """

    path: str = ":memory:"
    enable_wal: bool = True
    busy_timeout_ms: int = 5000
    foreign_keys: bool = True

    def __post_init__(self):
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)


@dataclass
class PostgresConfig:
    """PostgreSQL database configuration.

    Attributes:
        host: Database host.
        port: Database port.
        database: Database name.
        user: Database user.
        password: Database password.
        min_connections: Minimum pool connections.
        max_connections: Maximum pool connections.
    """

    host: str = "localhost"
    port: int = 5432
    database: str = "mcp_hangar"
    user: str = "mcp_hangar"
    password: str = ""
    min_connections: int = 2
    max_connections: int = 10


class IConnectionFactory(Protocol):
    """Protocol for database connection factories."""

    def get_connection(self) -> Any:
        """Get a database connection."""
        ...

    def close(self) -> None:
        """Close all connections."""
        ...


class SQLiteConnectionFactory:
    """Thread-safe SQLite connection factory.

    Uses thread-local storage to provide one connection per thread.
    For in-memory databases, uses a single persistent connection.
    """

    def __init__(self, config: SQLiteConfig):
        self._config = config
        self._local = threading.local()
        self._lock = threading.Lock()

        # For in-memory database, keep a persistent connection
        self._persistent_conn: sqlite3.Connection | None = None
        if config.path == ":memory:":
            self._persistent_conn = self._create_connection()

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection with proper settings."""
        conn = sqlite3.connect(
            self._config.path,
            check_same_thread=False,
            timeout=self._config.busy_timeout_ms / 1000,
        )
        conn.row_factory = sqlite3.Row

        if self._config.foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")

        if self._config.enable_wal and self._config.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")

        conn.execute(f"PRAGMA busy_timeout = {self._config.busy_timeout_ms}")

        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection for the current thread.

        Yields:
            sqlite3.Connection configured for use.
        """
        if self._persistent_conn is not None:
            yield self._persistent_conn
            return

        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = self._create_connection()

        yield self._local.connection

    def close(self) -> None:
        """Close all connections."""
        if self._persistent_conn:
            try:
                self._persistent_conn.commit()
            except Exception:
                pass
            self._persistent_conn.close()
            self._persistent_conn = None

        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.commit()
                self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._local.connection.close()
            self._local.connection = None


class PostgresConnectionFactory:
    """PostgreSQL connection factory with connection pooling.

    Uses psycopg2 ThreadedConnectionPool for thread-safe connections.
    """

    def __init__(self, config: PostgresConfig):
        self._config = config
        self._pool = None

    def _ensure_pool(self):
        """Lazily create connection pool."""
        if self._pool is None:
            try:
                import psycopg2  # noqa: F401
                from psycopg2 import pool
            except ImportError:
                raise ImportError("psycopg2 is required for PostgreSQL. " "Install with: pip install psycopg2-binary")

            self._pool = pool.ThreadedConnectionPool(
                minconn=self._config.min_connections,
                maxconn=self._config.max_connections,
                host=self._config.host,
                port=self._config.port,
                database=self._config.database,
                user=self._config.user,
                password=self._config.password,
            )

    @contextmanager
    def get_connection(self) -> Generator[Any, None, None]:
        """Get a database connection from the pool.

        Yields:
            psycopg2 connection.
        """
        self._ensure_pool()
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            self._pool.closeall()
            self._pool = None


class MigrationRunner:
    """Runs database migrations in order.

    Supports both SQLite and PostgreSQL via connection factory.
    """

    def __init__(
        self,
        connection_factory: IConnectionFactory,
        migrations: list[dict],
        table_name: str = "schema_migrations",
    ):
        """Initialize migration runner.

        Args:
            connection_factory: Factory for database connections.
            migrations: List of migration dicts with 'version', 'name', 'sql'.
            table_name: Name of migrations tracking table.
        """
        self._conn_factory = connection_factory
        self._migrations = sorted(migrations, key=lambda m: m["version"])
        self._table_name = table_name

    def run(self) -> int:
        """Run pending migrations.

        Returns:
            Number of migrations applied.
        """
        with self._conn_factory.get_connection() as conn:
            # Create migrations table
            self._ensure_migrations_table(conn)

            # Get current version
            current_version = self._get_current_version(conn)

            # Apply pending migrations
            applied = 0
            for migration in self._migrations:
                if migration["version"] > current_version:
                    self._apply_migration(conn, migration)
                    applied += 1

            return applied

    def _ensure_migrations_table(self, conn) -> None:
        """Create migrations tracking table if not exists."""
        cursor = conn.cursor()
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """
        )
        conn.commit()

    def _get_current_version(self, conn) -> int:
        """Get the current schema version."""
        cursor = conn.cursor()
        cursor.execute(f"SELECT MAX(version) FROM {self._table_name}")
        row = cursor.fetchone()
        return row[0] if row and row[0] else 0

    def _apply_migration(self, conn, migration: dict) -> None:
        """Apply a single migration."""
        cursor = conn.cursor()

        logger.info(
            "applying_migration",
            version=migration["version"],
            name=migration["name"],
        )

        # Execute migration SQL
        if hasattr(conn, "executescript"):
            # SQLite
            conn.executescript(migration["sql"])
        else:
            # PostgreSQL
            cursor.execute(migration["sql"])

        # Record migration
        cursor.execute(
            (
                f"INSERT INTO {self._table_name} (version, name, applied_at) VALUES (?, ?, ?)"
                if hasattr(conn, "executescript")
                else f"INSERT INTO {self._table_name} (version, name, applied_at) VALUES (%s, %s, %s)"
            ),
            (migration["version"], migration["name"], datetime.now(timezone.utc).isoformat()),
        )

        conn.commit()

        logger.info(
            "migration_applied",
            version=migration["version"],
            name=migration["name"],
        )


def create_connection_factory(
    driver: str,
    sqlite_config: SQLiteConfig | None = None,
    postgres_config: PostgresConfig | None = None,
) -> IConnectionFactory:
    """Create appropriate connection factory based on driver.

    Args:
        driver: Database driver ("sqlite" or "postgresql").
        sqlite_config: SQLite configuration (if driver is sqlite).
        postgres_config: PostgreSQL configuration (if driver is postgresql).

    Returns:
        Connection factory for the specified driver.

    Raises:
        ValueError: If unknown driver or missing config.
    """
    if driver == "sqlite":
        if sqlite_config is None:
            sqlite_config = SQLiteConfig()
        return SQLiteConnectionFactory(sqlite_config)

    elif driver in ("postgresql", "postgres"):
        if postgres_config is None:
            postgres_config = PostgresConfig()
        return PostgresConnectionFactory(postgres_config)

    else:
        raise ValueError(f"Unknown database driver: {driver}")
