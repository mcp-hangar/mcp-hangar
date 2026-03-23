"""McpCatalogRepository — abstract repository interface for the static MCP provider catalog.

Implementations:
- SQLiteMcpCatalogRepository (infrastructure/catalog/sqlite_catalog_repository.py)

Layer: Domain contracts — no external dependencies, no implementation.
"""

from abc import ABC, abstractmethod

from ..model.catalog import McpProviderEntry


class McpCatalogRepository(ABC):
    """Abstract repository for managing static MCP provider catalog entries.

    Implementations must be thread-safe. SQLite implementations should use
    SQLiteConnectionFactory from infrastructure/persistence/database_common.py.
    """

    @abstractmethod
    def list_entries(
        self,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> list[McpProviderEntry]:
        """List catalog entries with optional filtering.

        Args:
            search: Case-insensitive substring to match against name or description.
            tags: If provided, return only entries whose tags list contains ALL
                specified tags.

        Returns:
            List of matching McpProviderEntry instances.
        """
        ...

    @abstractmethod
    def get_entry(self, entry_id: str) -> McpProviderEntry | None:
        """Retrieve a single catalog entry by ID.

        Args:
            entry_id: UUID of the entry to retrieve.

        Returns:
            McpProviderEntry if found, None otherwise.
        """
        ...

    @abstractmethod
    def add_entry(self, entry: McpProviderEntry) -> None:
        """Add or replace a catalog entry.

        Uses INSERT OR REPLACE semantics — if entry_id already exists,
        the existing row is replaced.

        Args:
            entry: McpProviderEntry to persist.
        """
        ...

    @abstractmethod
    def remove_entry(self, entry_id: str) -> None:
        """Remove a catalog entry by ID.

        Args:
            entry_id: UUID of the entry to remove.

        Raises:
            ValueError: If the entry is a builtin entry (builtin=True).
            KeyError: If no entry with the given entry_id exists.
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """Count total catalog entries.

        Returns:
            Total number of entries in the catalog.
        """
        ...
