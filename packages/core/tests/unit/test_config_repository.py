"""Tests for persistence layer - provider config repository."""

import asyncio

import pytest

from mcp_hangar.domain.contracts.persistence import ProviderConfigSnapshot
from mcp_hangar.infrastructure.persistence import (
    Database,
    DatabaseConfig,
    InMemoryProviderConfigRepository,
    SQLiteProviderConfigRepository,
)


@pytest.fixture
def config_snapshot() -> ProviderConfigSnapshot:
    """Create a test provider configuration snapshot."""
    return ProviderConfigSnapshot(
        provider_id="test-provider",
        mode="subprocess",
        command=["python", "-m", "test_server"],
        env={"TEST_VAR": "value"},
        idle_ttl_s=300,
        health_check_interval_s=60,
        max_consecutive_failures=3,
        description="Test provider",
        enabled=True,
    )


class TestInMemoryProviderConfigRepository:
    """Tests for in-memory provider config repository."""

    @pytest.fixture
    def repo(self) -> InMemoryProviderConfigRepository:
        """Create repository instance."""
        return InMemoryProviderConfigRepository()

    @pytest.mark.asyncio
    async def test_save_and_get(
        self,
        repo: InMemoryProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test saving and retrieving a configuration."""
        await repo.save(config_snapshot)

        result = await repo.get(config_snapshot.provider_id)

        assert result is not None
        assert result.provider_id == config_snapshot.provider_id
        assert result.mode == config_snapshot.mode
        assert result.command == config_snapshot.command

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, repo: InMemoryProviderConfigRepository):
        """Test getting non-existent configuration returns None."""
        result = await repo.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all(self, repo: InMemoryProviderConfigRepository):
        """Test getting all configurations."""
        configs = [
            ProviderConfigSnapshot(provider_id="provider-1", mode="subprocess"),
            ProviderConfigSnapshot(provider_id="provider-2", mode="docker"),
            ProviderConfigSnapshot(provider_id="provider-3", mode="remote"),
        ]

        for config in configs:
            await repo.save(config)

        all_configs = await repo.get_all()

        assert len(all_configs) == 3
        ids = {c.provider_id for c in all_configs}
        assert ids == {"provider-1", "provider-2", "provider-3"}

    @pytest.mark.asyncio
    async def test_delete(
        self,
        repo: InMemoryProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test deleting a configuration."""
        await repo.save(config_snapshot)

        deleted = await repo.delete(config_snapshot.provider_id)

        assert deleted is True
        assert await repo.get(config_snapshot.provider_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, repo: InMemoryProviderConfigRepository):
        """Test deleting non-existent configuration returns False."""
        deleted = await repo.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_exists(
        self,
        repo: InMemoryProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test checking if configuration exists."""
        assert await repo.exists(config_snapshot.provider_id) is False

        await repo.save(config_snapshot)

        assert await repo.exists(config_snapshot.provider_id) is True

    @pytest.mark.asyncio
    async def test_update_preserves_created_at(
        self,
        repo: InMemoryProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test that updating preserves created_at timestamp."""
        await repo.save(config_snapshot)
        first = await repo.get(config_snapshot.provider_id)
        created_at = first.created_at

        # Wait a bit and update
        await asyncio.sleep(0.01)

        updated = ProviderConfigSnapshot(
            provider_id=config_snapshot.provider_id,
            mode="docker",  # Changed mode
            image="test-image",
        )
        await repo.save(updated)

        result = await repo.get(config_snapshot.provider_id)

        assert result.created_at == created_at
        assert result.updated_at > created_at
        assert result.mode == "docker"


class TestSQLiteProviderConfigRepository:
    """Tests for SQLite provider config repository."""

    @pytest.fixture
    def database(self, tmp_path) -> Database:
        """Create test database (sync fixture)."""
        return Database(DatabaseConfig(path=str(tmp_path / "test.db")))

    @pytest.fixture
    def repo(self, database: Database) -> SQLiteProviderConfigRepository:
        """Create repository instance."""
        return SQLiteProviderConfigRepository(database)

    @pytest.mark.asyncio
    async def test_save_and_get(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test saving and retrieving a configuration."""
        await database.initialize()
        await repo.save(config_snapshot)

        result = await repo.get(config_snapshot.provider_id)

        assert result is not None
        assert result.provider_id == config_snapshot.provider_id
        assert result.mode == config_snapshot.mode
        assert result.command == config_snapshot.command
        assert result.env == config_snapshot.env

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, database: Database, repo: SQLiteProviderConfigRepository):
        """Test getting non-existent configuration returns None."""
        await database.initialize()
        result = await repo.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_enabled_only(self, database: Database, repo: SQLiteProviderConfigRepository):
        """Test get_all returns only enabled configurations."""
        await database.initialize()
        enabled_config = ProviderConfigSnapshot(provider_id="enabled-provider", mode="subprocess", enabled=True)
        await repo.save(enabled_config)

        # Delete (soft delete)
        await repo.delete("enabled-provider")

        # Re-add another
        new_config = ProviderConfigSnapshot(provider_id="new-provider", mode="subprocess", enabled=True)
        await repo.save(new_config)

        all_configs = await repo.get_all()

        assert len(all_configs) == 1
        assert all_configs[0].provider_id == "new-provider"

    @pytest.mark.asyncio
    async def test_soft_delete(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test soft delete marks config as disabled."""
        await database.initialize()
        await repo.save(config_snapshot)

        deleted = await repo.delete(config_snapshot.provider_id)

        assert deleted is True
        # Should not be visible via normal get
        assert await repo.exists(config_snapshot.provider_id) is False

    @pytest.mark.asyncio
    async def test_hard_delete(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test hard delete permanently removes configuration."""
        await database.initialize()
        await repo.save(config_snapshot)

        deleted = await repo.hard_delete(config_snapshot.provider_id)

        assert deleted is True
        assert await repo.get(config_snapshot.provider_id) is None

    @pytest.mark.asyncio
    async def test_version_increment(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test version increments on update."""
        await database.initialize()
        await repo.save(config_snapshot)

        result1 = await repo.get_with_version(config_snapshot.provider_id)
        assert result1 is not None
        _, version1 = result1

        # Update
        updated = ProviderConfigSnapshot(
            provider_id=config_snapshot.provider_id,
            mode="docker",
        )
        await repo.save(updated)

        result2 = await repo.get_with_version(config_snapshot.provider_id)
        assert result2 is not None
        _, version2 = result2

        assert version2 == version1 + 1

    @pytest.mark.asyncio
    async def test_update_last_started(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test updating last_started_at timestamp."""
        await database.initialize()
        await repo.save(config_snapshot)

        await repo.update_last_started(config_snapshot.provider_id)

        # No error means success (we can't easily verify the timestamp)

    @pytest.mark.asyncio
    async def test_update_failure_count(
        self,
        database: Database,
        repo: SQLiteProviderConfigRepository,
        config_snapshot: ProviderConfigSnapshot,
    ):
        """Test updating consecutive failure count."""
        await database.initialize()
        await repo.save(config_snapshot)

        await repo.update_failure_count(config_snapshot.provider_id, 5)

        # No error means success
