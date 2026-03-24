"""Unit tests for SQLiteMcpCatalogRepository.

All tests use in-memory SQLite (SQLiteConfig(path=':memory:')).
Tests cover CRUD operations, search/filter, builtin protection, and count.
"""

import pytest

from mcp_hangar.domain.model.catalog import McpProviderEntry
from mcp_hangar.infrastructure.catalog.sqlite_catalog_repository import SQLiteMcpCatalogRepository
from mcp_hangar.infrastructure.persistence.database_common import SQLiteConfig


def _make_repo() -> SQLiteMcpCatalogRepository:
    """Create an in-memory SQLiteMcpCatalogRepository for testing."""
    return SQLiteMcpCatalogRepository(SQLiteConfig(path=":memory:"))


def _make_entry(
    entry_id: str = "e1",
    name: str = "filesystem",
    description: str = "Filesystem access",
    mode: str = "subprocess",
    tags: list[str] | None = None,
    builtin: bool = False,
    image: str | None = None,
) -> McpProviderEntry:
    """Create a minimal McpProviderEntry for testing."""
    return McpProviderEntry(
        entry_id=entry_id,
        name=name,
        description=description,
        mode=mode,
        command=["uvx", f"mcp-server-{name}"],
        image=image,
        tags=tags or ["files"],
        verified=True,
        source="builtin" if builtin else "custom",
        required_env=[],
        builtin=builtin,
    )


class TestSQLiteMcpCatalogRepositoryAddGet:
    """Tests for add_entry and get_entry."""

    def test_add_and_get_entry_round_trip(self):
        """add_entry persists entry; get_entry retrieves it with correct fields."""
        repo = _make_repo()
        entry = _make_entry()
        repo.add_entry(entry)
        fetched = repo.get_entry("e1")
        assert fetched is not None
        assert fetched.entry_id == "e1"
        assert fetched.name == "filesystem"

    def test_get_entry_returns_none_for_missing(self):
        """get_entry returns None for unknown entry_id."""
        repo = _make_repo()
        assert repo.get_entry("nonexistent") is None

    def test_list_fields_deserialized_correctly(self):
        """command, tags, required_env are deserialized from JSON strings as Python lists."""
        repo = _make_repo()
        entry = McpProviderEntry(
            entry_id="e2",
            name="brave-search",
            description="Web search",
            mode="subprocess",
            command=["uvx", "mcp-server-brave-search"],
            image=None,
            tags=["search", "web"],
            verified=True,
            source="builtin",
            required_env=["BRAVE_API_KEY"],
            builtin=True,
        )
        repo.add_entry(entry)
        fetched = repo.get_entry("e2")
        assert fetched.command == ["uvx", "mcp-server-brave-search"]
        assert fetched.tags == ["search", "web"]
        assert fetched.required_env == ["BRAVE_API_KEY"]
        assert fetched.builtin is True

    def test_add_entry_replace_semantics(self):
        """Adding entry with existing entry_id replaces the row (INSERT OR REPLACE)."""
        repo = _make_repo()
        repo.add_entry(_make_entry(description="original"))
        repo.add_entry(_make_entry(description="updated"))
        fetched = repo.get_entry("e1")
        assert fetched.description == "updated"
        assert repo.count() == 1


class TestSQLiteMcpCatalogRepositoryList:
    """Tests for list_entries."""

    def test_list_entries_returns_all(self):
        """list_entries() without filters returns all entries."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1", "filesystem"))
        repo.add_entry(_make_entry("e2", "sqlite", "SQLite access"))
        entries = repo.list_entries()
        assert len(entries) == 2

    def test_list_entries_search_by_name(self):
        """list_entries(search='file') matches entries with 'file' in name."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1", "filesystem", "File access"))
        repo.add_entry(_make_entry("e2", "sqlite", "SQLite database"))
        results = repo.list_entries(search="file")
        assert len(results) == 1
        assert results[0].name == "filesystem"

    def test_list_entries_search_by_description(self):
        """list_entries(search=...) also matches on description field."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1", "brave-search", "Web search via Brave API"))
        repo.add_entry(_make_entry("e2", "filesystem", "Local file access"))
        results = repo.list_entries(search="Brave")
        assert len(results) == 1
        assert results[0].name == "brave-search"

    def test_list_entries_filter_by_tags(self):
        """list_entries(tags=['files']) returns only entries containing that tag."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1", "filesystem", tags=["files", "local"]))
        repo.add_entry(_make_entry("e2", "sqlite", description="DB", tags=["database"]))
        results = repo.list_entries(tags=["files"])
        assert len(results) == 1
        assert results[0].name == "filesystem"

    def test_list_entries_filter_by_multiple_tags_requires_all(self):
        """list_entries(tags=['files','local']) requires ALL tags to be present."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1", "filesystem", tags=["files", "local"]))
        repo.add_entry(_make_entry("e2", "archive", tags=["files"]))
        results = repo.list_entries(tags=["files", "local"])
        assert len(results) == 1
        assert results[0].name == "filesystem"

    def test_list_entries_empty_on_no_match(self):
        """list_entries with search that matches nothing returns empty list."""
        repo = _make_repo()
        repo.add_entry(_make_entry())
        assert repo.list_entries(search="NOMATCH") == []


class TestSQLiteMcpCatalogRepositoryRemove:
    """Tests for remove_entry."""

    def test_remove_entry_deletes_custom_entry(self):
        """remove_entry removes a custom (builtin=False) entry."""
        repo = _make_repo()
        repo.add_entry(_make_entry(builtin=False))
        repo.remove_entry("e1")
        assert repo.count() == 0

    def test_remove_entry_raises_value_error_for_builtin(self):
        """remove_entry raises ValueError when entry has builtin=True."""
        repo = _make_repo()
        repo.add_entry(_make_entry(builtin=True))
        with pytest.raises(ValueError, match="builtin"):
            repo.remove_entry("e1")

    def test_remove_entry_raises_key_error_for_missing(self):
        """remove_entry raises KeyError when entry_id does not exist."""
        repo = _make_repo()
        with pytest.raises(KeyError):
            repo.remove_entry("nonexistent")


class TestSQLiteMcpCatalogRepositoryCount:
    """Tests for count()."""

    def test_count_starts_at_zero(self):
        """Empty repo returns count of 0."""
        assert _make_repo().count() == 0

    def test_count_increments_on_add(self):
        """count() increments with each add_entry call."""
        repo = _make_repo()
        repo.add_entry(_make_entry("e1"))
        repo.add_entry(_make_entry("e2", "sqlite", "DB"))
        assert repo.count() == 2
