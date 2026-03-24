"""SQLite-backed implementation of McpCatalogRepository.

Uses the sync SQLiteConnectionFactory + MigrationRunner pattern from
MetricsHistoryStore. JSON-serializes list fields (command, tags, required_env)
for storage, deserializes on read.

Thread-safety: SQLiteConnectionFactory provides one connection per thread.
Builtin entries (builtin=1 in DB) cannot be deleted.
"""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from ...domain.contracts.catalog import McpCatalogRepository
from ...domain.model.catalog import McpProviderEntry
from ...infrastructure.persistence.database_common import MigrationRunner, SQLiteConfig, SQLiteConnectionFactory
from ...logging_config import get_logger

logger = get_logger(__name__)

_MIGRATIONS: list[dict] = [
    {
        "version": 1,
        "name": "create_catalog_entries",
        "sql": """
            CREATE TABLE IF NOT EXISTS catalog_entries (
                entry_id     TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                description  TEXT NOT NULL,
                mode         TEXT NOT NULL,
                command      TEXT NOT NULL,
                image        TEXT,
                tags         TEXT NOT NULL,
                verified     INTEGER NOT NULL DEFAULT 0,
                source       TEXT NOT NULL DEFAULT 'custom',
                required_env TEXT NOT NULL,
                builtin      INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_catalog_entries_name
                ON catalog_entries (name);
        """,
    },
]


class SQLiteMcpCatalogRepository(McpCatalogRepository):
    """SQLite implementation of McpCatalogRepository.

    Uses MigrationRunner for schema management. All list fields (command,
    tags, required_env) are stored as JSON strings and deserialized on read.

    Builtin entries (builtin=1) cannot be removed — attempting to do so
    raises ValueError.

    Args:
        config: SQLite database configuration. Use SQLiteConfig(path=":memory:") for tests.
    """

    def __init__(self, config: SQLiteConfig) -> None:
        """Initialize the repository and run migrations.

        Args:
            config: SQLite configuration.
        """
        self._factory = SQLiteConnectionFactory(config)
        self._runner = MigrationRunner(
            connection_factory=self._factory,
            migrations=_MIGRATIONS,
        )
        self._runner.run()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing a committed connection.

        Yields:
            sqlite3.Connection with row_factory already set to sqlite3.Row
            (configured by SQLiteConnectionFactory).
        """
        with self._factory.get_connection() as conn:
            yield conn
            conn.commit()

    def add_entry(self, entry: McpProviderEntry) -> None:
        """Add or replace a catalog entry (INSERT OR REPLACE).

        List fields (command, tags, required_env) are JSON-serialized.

        Args:
            entry: McpProviderEntry to persist.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO catalog_entries
                    (entry_id, name, description, mode, command, image,
                     tags, verified, source, required_env, builtin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.name,
                    entry.description,
                    entry.mode,
                    json.dumps(entry.command),
                    entry.image,
                    json.dumps(entry.tags),
                    1 if entry.verified else 0,
                    entry.source,
                    json.dumps(entry.required_env),
                    1 if entry.builtin else 0,
                ),
            )
        logger.debug("catalog_entry_added", entry_id=entry.entry_id, name=entry.name)

    def get_entry(self, entry_id: str) -> McpProviderEntry | None:
        """Retrieve a single catalog entry by ID.

        Args:
            entry_id: UUID of the entry to retrieve.

        Returns:
            McpProviderEntry if found, None otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM catalog_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_entries(
        self,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> list[McpProviderEntry]:
        """List catalog entries with optional filtering.

        Search performs case-insensitive LIKE match on name and description.
        Tags filter requires ALL specified tags to be present in the entry's tags list.

        Args:
            search: Substring to search for in name or description.
            tags: Tags that must ALL be present in the entry.

        Returns:
            List of matching McpProviderEntry instances.
        """
        conditions: list[str] = []
        params: list = []

        if search:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            like_term = f"%{search}%"
            params.extend([like_term, like_term])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM catalog_entries {where} ORDER BY name ASC"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        entries = [self._row_to_entry(row) for row in rows]

        # Apply tags filter in Python (JSON contains check)
        if tags:
            entries = [e for e in entries if all(t in e.tags for t in tags)]

        return entries

    def remove_entry(self, entry_id: str) -> None:
        """Remove a catalog entry.

        Args:
            entry_id: UUID of the entry to remove.

        Raises:
            ValueError: If the entry is builtin (cannot be deleted).
            KeyError: If no entry with the given entry_id exists.
        """
        existing = self.get_entry(entry_id)
        if existing is None:
            raise KeyError(f"Catalog entry not found: {entry_id}")
        if existing.builtin:
            raise ValueError(f"Cannot delete builtin catalog entry: {entry_id}")

        with self._conn() as conn:
            conn.execute(
                "DELETE FROM catalog_entries WHERE entry_id = ?",
                (entry_id,),
            )
        logger.info("catalog_entry_removed", entry_id=entry_id)

    def count(self) -> int:
        """Count total catalog entries.

        Returns:
            Total number of entries in the catalog.
        """
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM catalog_entries").fetchone()
        return row[0]

    def _row_to_entry(self, row: sqlite3.Row) -> McpProviderEntry:
        """Map a sqlite3.Row to a McpProviderEntry.

        Deserializes JSON fields (command, tags, required_env) and converts
        integer flags (verified, builtin) back to booleans.

        Args:
            row: sqlite3.Row from the catalog_entries table.

        Returns:
            McpProviderEntry instance with all fields populated.
        """
        return McpProviderEntry(
            entry_id=row["entry_id"],
            name=row["name"],
            description=row["description"],
            mode=row["mode"],
            command=json.loads(row["command"]),
            image=row["image"],
            tags=json.loads(row["tags"]),
            verified=bool(row["verified"]),
            source=row["source"],
            required_env=json.loads(row["required_env"]),
            builtin=bool(row["builtin"]),
        )
