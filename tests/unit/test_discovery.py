"""Unit tests for Provider Discovery components."""

from datetime import datetime, timedelta, UTC

import pytest

from mcp_hangar.domain.discovery.conflict_resolver import ConflictResolution, ConflictResolver
from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider
from mcp_hangar.domain.discovery.discovery_service import DiscoveryService
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource


class TestDiscoveredProvider:
    """Tests for DiscoveredProvider value object."""

    def test_create_with_fingerprint(self):
        """Test factory method generates fingerprint."""
        provider = DiscoveredProvider.create(
            name="test-provider",
            source_type="docker",
            mode="http",
            connection_info={"host": "localhost", "port": 8080},
            metadata={"env": "test"},
        )

        assert provider.name == "test-provider"
        assert provider.source_type == "docker"
        assert provider.mode == "http"
        assert provider.fingerprint is not None
        assert len(provider.fingerprint) == 16  # SHA256 truncated

    def test_fingerprint_changes_with_config(self):
        """Test fingerprint changes when config changes."""
        provider1 = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={"host": "localhost", "port": 8080},
        )

        provider2 = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={"host": "localhost", "port": 9090},  # Different port
        )

        assert provider1.fingerprint != provider2.fingerprint

    def test_is_expired(self):
        """Test TTL expiration check."""
        # Create provider with 1 second TTL
        provider = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={},
            ttl_seconds=1,
        )

        assert not provider.is_expired()

        # Simulate time passing by creating with old timestamp
        old_time = datetime.now(UTC) - timedelta(seconds=2)
        expired_provider = DiscoveredProvider(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={},
            metadata={},
            fingerprint="abc123",
            discovered_at=old_time,
            last_seen_at=old_time,
            ttl_seconds=1,
        )

        assert expired_provider.is_expired()

    def test_with_updated_seen_time(self):
        """Test creating new instance with updated timestamp."""
        old_time = datetime.now(UTC) - timedelta(seconds=30)
        provider = DiscoveredProvider(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={},
            metadata={},
            fingerprint="abc123",
            discovered_at=old_time,
            last_seen_at=old_time,
            ttl_seconds=90,
        )

        updated = provider.with_updated_seen_time()

        # Original unchanged
        assert provider.last_seen_at == old_time
        # New has updated time
        assert updated.last_seen_at > old_time
        # Other fields preserved
        assert updated.discovered_at == provider.discovered_at
        assert updated.fingerprint == provider.fingerprint

    def test_has_changed(self):
        """Test change detection via fingerprint."""
        provider1 = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={"port": 8080},
        )

        # Same config
        provider2 = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={"port": 8080},
        )

        # Different config
        provider3 = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={"port": 9090},
        )

        assert not provider1.has_changed(provider2)
        assert provider1.has_changed(provider3)

    def test_to_dict_and_from_dict(self):
        """Test serialization round-trip."""
        provider = DiscoveredProvider.create(
            name="test",
            source_type="kubernetes",
            mode="sse",
            connection_info={"host": "10.0.0.1", "port": 8080},
            metadata={"namespace": "default"},
        )

        data = provider.to_dict()
        restored = DiscoveredProvider.from_dict(data)

        assert restored.name == provider.name
        assert restored.source_type == provider.source_type
        assert restored.mode == provider.mode
        assert restored.fingerprint == provider.fingerprint


class TestConflictResolver:
    """Tests for ConflictResolver."""

    def test_static_always_wins(self):
        """Test static config takes precedence."""
        resolver = ConflictResolver(static_providers={"my-static-provider"})

        provider = DiscoveredProvider.create(
            name="my-static-provider",
            source_type="kubernetes",
            mode="http",
            connection_info={},
        )

        result = resolver.resolve(provider)

        assert result.resolution == ConflictResolution.STATIC_WINS
        assert result.winner is None

    def test_new_provider_registered(self):
        """Test new provider is registered."""
        resolver = ConflictResolver()

        provider = DiscoveredProvider.create(
            name="new-provider",
            source_type="docker",
            mode="http",
            connection_info={},
        )

        result = resolver.resolve(provider)

        assert result.resolution == ConflictResolution.REGISTERED
        assert result.winner is not None

    def test_same_provider_unchanged(self):
        """Test same provider with same fingerprint returns unchanged."""
        resolver = ConflictResolver()

        provider = DiscoveredProvider.create(
            name="test-provider",
            source_type="docker",
            mode="http",
            connection_info={"port": 8080},
        )

        # First registration
        resolver.resolve(provider)
        resolver.register(provider)

        # Same provider again
        result = resolver.resolve(provider)

        assert result.resolution == ConflictResolution.UNCHANGED
        assert result.should_update_seen

    def test_config_change_detected(self):
        """Test config change triggers update."""
        resolver = ConflictResolver()

        provider1 = DiscoveredProvider.create(
            name="test-provider",
            source_type="docker",
            mode="http",
            connection_info={"port": 8080},
        )

        resolver.resolve(provider1)
        resolver.register(provider1)

        # Changed config
        provider2 = DiscoveredProvider.create(
            name="test-provider",
            source_type="docker",
            mode="http",
            connection_info={"port": 9090},  # Different port
        )

        result = resolver.resolve(provider2)

        assert result.resolution == ConflictResolution.UPDATED
        assert result.should_register

    def test_source_priority(self):
        """Test higher priority source wins."""
        resolver = ConflictResolver()

        # Register from filesystem (lower priority)
        provider1 = DiscoveredProvider.create(
            name="test-provider",
            source_type="filesystem",
            mode="http",
            connection_info={},
        )
        resolver.resolve(provider1)
        resolver.register(provider1)

        # Same name from kubernetes (higher priority)
        provider2 = DiscoveredProvider.create(
            name="test-provider",
            source_type="kubernetes",
            mode="http",
            connection_info={},
        )

        result = resolver.resolve(provider2)

        assert result.resolution == ConflictResolution.SOURCE_PRIORITY
        assert result.winner.source_type == "kubernetes"

    def test_lower_priority_rejected(self):
        """Test lower priority source is rejected."""
        resolver = ConflictResolver()

        # Register from kubernetes (higher priority)
        provider1 = DiscoveredProvider.create(
            name="test-provider",
            source_type="kubernetes",
            mode="http",
            connection_info={},
        )
        resolver.resolve(provider1)
        resolver.register(provider1)

        # Same name from filesystem (lower priority)
        provider2 = DiscoveredProvider.create(
            name="test-provider",
            source_type="filesystem",
            mode="http",
            connection_info={},
        )

        result = resolver.resolve(provider2)

        assert result.resolution == ConflictResolution.REJECTED
        assert result.winner is None


class MockDiscoverySource(DiscoverySource):
    """Mock discovery source for testing."""

    def __init__(
        self,
        source_type: str = "mock",
        providers: list = None,
        mode: DiscoveryMode = DiscoveryMode.ADDITIVE,
    ):
        super().__init__(mode)
        self._source_type = source_type
        self._providers = providers or []
        self._healthy = True

    @property
    def source_type(self) -> str:
        return self._source_type

    async def discover(self):
        return self._providers

    async def health_check(self) -> bool:
        return self._healthy

    def set_healthy(self, healthy: bool):
        self._healthy = healthy

    def set_providers(self, providers: list):
        self._providers = providers


class TestDiscoveryService:
    """Tests for DiscoveryService."""

    @pytest.mark.asyncio
    async def test_register_source(self):
        """Test source registration."""
        service = DiscoveryService()
        source = MockDiscoverySource("docker")

        service.register_source(source)

        assert service.get_source("docker") is source
        assert len(service.get_all_sources()) == 1

    @pytest.mark.asyncio
    async def test_discovery_cycle(self):
        """Test running a discovery cycle."""
        service = DiscoveryService()

        provider = DiscoveredProvider.create(
            name="test",
            source_type="docker",
            mode="http",
            connection_info={},
        )

        source = MockDiscoverySource("docker", providers=[provider])
        service.register_source(source)

        result = await service.run_discovery_cycle()

        assert result.discovered_count == 1
        assert result.source_results["docker"] == 1

    @pytest.mark.asyncio
    async def test_multiple_sources(self):
        """Test discovery from multiple sources."""
        service = DiscoveryService()

        provider1 = DiscoveredProvider.create(
            name="docker-provider",
            source_type="docker",
            mode="http",
            connection_info={},
        )

        provider2 = DiscoveredProvider.create(
            name="k8s-provider",
            source_type="kubernetes",
            mode="http",
            connection_info={},
        )

        source1 = MockDiscoverySource("docker", providers=[provider1])
        source2 = MockDiscoverySource("kubernetes", providers=[provider2])

        service.register_source(source1)
        service.register_source(source2)

        result = await service.run_discovery_cycle()

        assert result.discovered_count == 2
        assert result.source_results["docker"] == 1
        assert result.source_results["kubernetes"] == 1

    @pytest.mark.asyncio
    async def test_source_error_handling(self):
        """Test that source errors are handled gracefully."""
        service = DiscoveryService()

        class FailingSource(DiscoverySource):
            @property
            def source_type(self) -> str:
                return "failing"

            async def discover(self):
                raise Exception("Discovery failed")

            async def health_check(self) -> bool:
                return False

        source = FailingSource()
        service.register_source(source)

        result = await service.run_discovery_cycle()

        assert result.error_count == 1
        assert result.discovered_count == 0

    @pytest.mark.asyncio
    async def test_quarantine_management(self):
        """Test quarantine workflow."""
        service = DiscoveryService()

        provider = DiscoveredProvider.create(
            name="bad-provider",
            source_type="docker",
            mode="http",
            connection_info={},
        )

        service.quarantine(provider, "Failed validation")

        quarantined = service.get_quarantined()
        assert "bad-provider" in quarantined
        assert quarantined["bad-provider"][1] == "Failed validation"

        # Approve
        approved = service.approve_quarantined("bad-provider")
        assert approved is not None
        assert service.get_quarantined() == {}


class TestDiscoveryMode:
    """Tests for DiscoveryMode enum."""

    def test_additive_mode(self):
        """Test ADDITIVE mode string value."""
        assert str(DiscoveryMode.ADDITIVE) == "additive"

    def test_authoritative_mode(self):
        """Test AUTHORITATIVE mode string value."""
        assert str(DiscoveryMode.AUTHORITATIVE) == "authoritative"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
