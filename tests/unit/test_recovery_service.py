"""Tests for recovery service."""

import pytest

from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence import (
    Database,
    DatabaseConfig,
    RecoveryService,
    SQLiteAuditRepository,
    SQLiteMcpServerConfigRepository,
)


class TestRecoveryServiceInMemory:
    """Tests for recovery service with SQLite persistence."""

    @pytest.fixture
    def provider_repo(self) -> InMemoryMcpServerRepository:
        """Create provider repository."""
        return InMemoryMcpServerRepository()

    @pytest.fixture
    def database(self, tmp_path) -> Database:
        """Create test database (sync fixture)."""
        return Database(DatabaseConfig(path=str(tmp_path / "test.db")))

    @pytest.mark.asyncio
    async def test_recover_empty(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test recovery with no stored configurations."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_mcp_servers()

        assert recovered_ids == []
        assert provider_repo.count() == 0

    @pytest.mark.asyncio
    async def test_recover_single_mcp_server(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test recovering a single provider."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save a configuration
        config = McpServerConfigSnapshot(mcp_server_id="test-provider", mode="subprocess",
        command=["python", "-m", "test_server"],
        env={"TEST": "value"},
        idle_ttl_s=300,
        health_check_interval_s=60,
        max_consecutive_failures=3,
        description="Test provider",)
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_mcp_servers()

        assert recovered_ids == ["test-provider"]
        assert provider_repo.count() == 1

        provider = provider_repo.get("test-provider")
        assert provider is not None
        assert provider.mcp_server_id == "test-provider"
        assert provider.mode_str == "subprocess"

    @pytest.mark.asyncio
    async def test_recover_multiple_providers(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test recovering multiple providers."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save multiple configurations
        configs = [
            McpServerConfigSnapshot(mcp_server_id="provider-1", mode="subprocess",
            command=["cmd1"],),
            McpServerConfigSnapshot(mcp_server_id="provider-2", mode="docker",
            image="test-image:latest",),
            McpServerConfigSnapshot(mcp_server_id="provider-3", mode="remote",
            endpoint="http://localhost:8080",),
        ]

        for config in configs:
            await config_repo.save(config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_mcp_servers()

        assert len(recovered_ids) == 3
        assert set(recovered_ids) == {"provider-1", "provider-2", "provider-3"}
        assert provider_repo.count() == 3

    @pytest.mark.asyncio
    async def test_recover_skips_disabled(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test that disabled configurations are not recovered."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save and then disable a configuration
        config = McpServerConfigSnapshot(mcp_server_id="disabled-provider", mode="subprocess",
        command=["cmd"],)
        await config_repo.save(config)
        await config_repo.delete("disabled-provider")  # Soft delete

        # Save an enabled one
        enabled_config = McpServerConfigSnapshot(mcp_server_id="enabled-provider", mode="subprocess",
        command=["cmd"],)
        await config_repo.save(enabled_config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        recovered_ids = await service.recover_mcp_servers()

        assert recovered_ids == ["enabled-provider"]
        assert provider_repo.count() == 1

    @pytest.mark.asyncio
    async def test_recovery_status(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test recovery status reporting."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save configurations
        for i in range(3):
            config = McpServerConfigSnapshot(mcp_server_id=f"provider-{i}", mode="subprocess",
            command=["cmd"],)
            await config_repo.save(config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        # Before recovery
        status = await service.get_recovery_status()
        assert status["status"] == "not_run"

        # After recovery
        await service.recover_mcp_servers()
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
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test recovering a single specific provider."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        config = McpServerConfigSnapshot(mcp_server_id="specific-provider", mode="subprocess",
        command=["cmd"],)
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        result = await service.recover_single_mcp_server("specific-provider")

        assert result is True
        assert provider_repo.count() == 1

    @pytest.mark.asyncio
    async def test_save_mcp_server_config(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test saving provider configuration via recovery service."""
        from mcp_hangar.domain.model import McpServer

        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        # Create a provider
        provider = McpServer(mcp_server_id="new-provider", mode="subprocess",
        command=["python", "-m", "server"],
        description="Test provider",
        idle_ttl_s=300,)

        await service.save_mcp_server_config(provider)

        # Verify it was saved
        saved = await config_repo.get("new-provider")
        assert saved is not None
        assert saved.mcp_server_id == "new-provider"
        assert saved.mode == "subprocess"
        assert saved.description == "Test provider"

    @pytest.mark.asyncio
    async def test_delete_mcp_server_config(
        self,
        database: Database,
        provider_repo: InMemoryMcpServerRepository,
    ):
        """Test deleting provider configuration."""
        await database.initialize()
        config_repo = SQLiteMcpServerConfigRepository(database)
        audit_repo = SQLiteAuditRepository(database)

        # Save a configuration first
        config = McpServerConfigSnapshot(mcp_server_id="to-delete", mode="subprocess",
        command=["cmd"],)
        await config_repo.save(config)

        service = RecoveryService(
            database=database,
            mcp_server_repository=provider_repo,
            config_repository=config_repo,
            audit_repository=audit_repo,
        )

        result = await service.delete_mcp_server_config("to-delete")

        assert result is True
        assert await config_repo.exists("to-delete") is False
