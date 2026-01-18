"""Tests for recovery service."""

import pytest

from mcp_hangar.domain.contracts.persistence import ProviderConfigSnapshot
from mcp_hangar.domain.repository import InMemoryProviderRepository
from mcp_hangar.infrastructure.persistence import (
    Database,
    DatabaseConfig,
    RecoveryService,
    SQLiteAuditRepository,
    SQLiteProviderConfigRepository,
)


class TestRecoveryServiceInMemory:
    """Tests for recovery service with SQLite persistence."""

    @pytest.fixture
    def provider_repo(self) -> InMemoryProviderRepository:
        """Create provider repository."""
        return InMemoryProviderRepository()

    @pytest.fixture
    def database(self, tmp_path) -> Database:
        """Create test database (sync fixture)."""
        return Database(DatabaseConfig(path=str(tmp_path / "test.db")))

    @pytest.mark.asyncio
    async def test_recover_empty(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test recovery with no stored configurations."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_providers()

        assert recovered_ids == []
        assert provider_repo.count() == 0

    @pytest.mark.asyncio
    async def test_recover_single_provider(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test recovering a single provider."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save a configuration
        config = ProviderConfigSnapshot(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test_server"],
            env={"TEST": "value"},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            description="Test provider",
        )
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_providers()

        assert recovered_ids == ["test-provider"]
        assert provider_repo.count() == 1

        provider = provider_repo.get("test-provider")
        assert provider is not None
        assert provider.provider_id == "test-provider"
        assert provider.mode_str == "subprocess"

    @pytest.mark.asyncio
    async def test_recover_multiple_providers(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test recovering multiple providers."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save multiple configurations
        configs = [
            ProviderConfigSnapshot(
                provider_id="provider-1",
                mode="subprocess",
                command=["cmd1"],
            ),
            ProviderConfigSnapshot(
                provider_id="provider-2",
                mode="docker",
                image="test-image:latest",
            ),
            ProviderConfigSnapshot(
                provider_id="provider-3",
                mode="remote",
                endpoint="http://localhost:8080",
            ),
        ]

        for config in configs:
            await config_repo.save(config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_providers()

        assert len(recovered_ids) == 3
        assert set(recovered_ids) == {"provider-1", "provider-2", "provider-3"}
        assert provider_repo.count() == 3

    @pytest.mark.asyncio
    async def test_recover_skips_disabled(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test that disabled configurations are not recovered."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save and then disable a configuration
        config = ProviderConfigSnapshot(
            provider_id="disabled-provider",
            mode="subprocess",
            command=["cmd"],
        )
        await config_repo.save(config)
        await config_repo.delete("disabled-provider")  # Soft delete

        # Save an enabled one
        enabled_config = ProviderConfigSnapshot(
            provider_id="enabled-provider",
            mode="subprocess",
            command=["cmd"],
        )
        await config_repo.save(enabled_config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_providers()

        assert recovered_ids == ["enabled-provider"]
        assert provider_repo.count() == 1

    @pytest.mark.asyncio
    async def test_recovery_status(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test recovery status reporting."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save configurations
        for i in range(3):
            config = ProviderConfigSnapshot(
                provider_id=f"provider-{i}",
                mode="subprocess",
                command=["cmd"],
            )
            await config_repo.save(config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        # Before recovery
        status = await service.get_recovery_status()
        assert status["status"] == "not_run"

        # After recovery
        await service.recover_providers()
        status = await service.get_recovery_status()

        assert status["status"] == "completed"
        assert status["recovered_count"] == 3
        assert status["failed_count"] == 0
        assert len(status["recovered_ids"]) == 3
        assert status["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_recover_single_provider_method(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test recovering a single specific provider."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        config = ProviderConfigSnapshot(
            provider_id="specific-provider",
            mode="subprocess",
            command=["cmd"],
        )
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        result = await service.recover_single_provider("specific-provider")

        assert result is True
        assert provider_repo.count() == 1

    @pytest.mark.asyncio
    async def test_save_provider_config(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test saving provider configuration via recovery service."""
        from mcp_hangar.domain.model import Provider

        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        # Create a provider
        provider = Provider(
            provider_id="new-provider",
            mode="subprocess",
            command=["python", "-m", "server"],
            description="Test provider",
            idle_ttl_s=300,
        )

        await service.save_provider_config(provider)

        # Verify it was saved
        saved = await config_repo.get("new-provider")
        assert saved is not None
        assert saved.provider_id == "new-provider"
        assert saved.mode == "subprocess"
        assert saved.description == "Test provider"

    @pytest.mark.asyncio
    async def test_delete_provider_config(
        self,
        database: Database,
        provider_repo: InMemoryProviderRepository,
    ):
        """Test deleting provider configuration."""
        await database.initialize()
        config_repo = SQLiteProviderConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save a configuration first
        config = ProviderConfigSnapshot(
            provider_id="to-delete",
            mode="subprocess",
            command=["cmd"],
        )
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            provider_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        result = await service.delete_provider_config("to-delete")

        assert result is True
        assert await config_repo.exists("to-delete") is False
