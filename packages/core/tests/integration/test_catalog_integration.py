"""Integration tests for SQLiteMcpCatalogRepository + load_catalog_seed.

Uses real SQLite in-memory + real YAML seed file (data/catalog_seed.yaml).
No mocks on internal components. Tests idempotency and builtin entry protection.
"""

from pathlib import Path

import pytest

from mcp_hangar.domain.model.catalog import McpProviderEntry
from mcp_hangar.infrastructure.catalog.seed_loader import load_catalog_seed
from mcp_hangar.infrastructure.catalog.sqlite_catalog_repository import SQLiteMcpCatalogRepository
from mcp_hangar.infrastructure.persistence.database_common import SQLiteConfig


SEED_PATH = Path("data/catalog_seed.yaml")


@pytest.fixture
def repo() -> SQLiteMcpCatalogRepository:
    """Fresh in-memory SQLiteMcpCatalogRepository."""
    return SQLiteMcpCatalogRepository(SQLiteConfig(path=":memory:"))


class TestLoadCatalogSeedIntegration:
    """Integration tests for load_catalog_seed with real YAML file."""

    def test_seed_loads_five_builtin_entries(self, repo):
        """load_catalog_seed loads all 5 entries from data/catalog_seed.yaml."""
        if not SEED_PATH.exists():
            pytest.skip("data/catalog_seed.yaml not present -- run from repo root")
        loaded = load_catalog_seed(repo, SEED_PATH)
        assert loaded == 5

    def test_seed_entries_have_builtin_true(self, repo):
        """All seeded entries have builtin=True."""
        if not SEED_PATH.exists():
            pytest.skip("data/catalog_seed.yaml not present -- run from repo root")
        load_catalog_seed(repo, SEED_PATH)
        entries = repo.list_entries()
        assert all(e.builtin is True for e in entries)

    def test_seed_is_idempotent(self, repo):
        """Calling load_catalog_seed twice returns 0 on second call."""
        if not SEED_PATH.exists():
            pytest.skip("data/catalog_seed.yaml not present -- run from repo root")
        load_catalog_seed(repo, SEED_PATH)
        second = load_catalog_seed(repo, SEED_PATH)
        assert second == 0

    def test_seed_entries_count_unchanged_after_second_call(self, repo):
        """Catalog count remains at 5 after two seed calls (no duplicates)."""
        if not SEED_PATH.exists():
            pytest.skip("data/catalog_seed.yaml not present -- run from repo root")
        load_catalog_seed(repo, SEED_PATH)
        load_catalog_seed(repo, SEED_PATH)
        assert repo.count() == 5

    def test_seeded_entries_cannot_be_deleted(self, repo):
        """Builtin seeded entries raise ValueError on remove_entry."""
        if not SEED_PATH.exists():
            pytest.skip("data/catalog_seed.yaml not present -- run from repo root")
        load_catalog_seed(repo, SEED_PATH)
        entries = repo.list_entries()
        assert len(entries) > 0
        with pytest.raises(ValueError, match="builtin"):
            repo.remove_entry(entries[0].entry_id)

    def test_seed_skipped_when_file_missing(self, repo, tmp_path):
        """load_catalog_seed returns 0 when seed file does not exist."""
        nonexistent = tmp_path / "no_such_file.yaml"
        result = load_catalog_seed(repo, nonexistent)
        assert result == 0
        assert repo.count() == 0


class TestCatalogRepositoryCRUDIntegration:
    """Full CRUD vertical slice without mocks."""

    def test_full_crud_cycle(self, repo):
        """Add custom entry, retrieve it, list it, then remove it."""
        entry = McpProviderEntry(
            entry_id="int-e1",
            name="my-custom-provider",
            description="Custom integration test provider",
            mode="subprocess",
            command=["python", "-m", "custom_server"],
            image=None,
            tags=["custom", "test"],
            verified=False,
            source="custom",
            required_env=["CUSTOM_KEY"],
            builtin=False,
        )

        # Add
        repo.add_entry(entry)
        assert repo.count() == 1

        # Get
        fetched = repo.get_entry("int-e1")
        assert fetched is not None
        assert fetched.name == "my-custom-provider"
        assert fetched.tags == ["custom", "test"]
        assert fetched.required_env == ["CUSTOM_KEY"]

        # List with search
        results = repo.list_entries(search="custom")
        assert len(results) == 1

        # Remove
        repo.remove_entry("int-e1")
        assert repo.count() == 0
        assert repo.get_entry("int-e1") is None
