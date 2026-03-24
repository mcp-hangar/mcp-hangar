"""Tests for the runtime provider store."""

from datetime import datetime
import threading
from unittest.mock import MagicMock

import pytest

from mcp_hangar.infrastructure.runtime_store import LoadMetadata, RuntimeProviderStore


def make_mock_provider(provider_id: str) -> MagicMock:
    """Create a mock provider for testing."""
    provider = MagicMock()
    provider.provider_id = provider_id
    return provider


def make_metadata(source: str = "registry:test", verified: bool = True) -> LoadMetadata:
    """Create test metadata."""
    return LoadMetadata(
        loaded_at=datetime.now(),
        loaded_by="test-user",
        source=source,
        verified=verified,
    )


class TestLoadMetadata:
    """Tests for LoadMetadata."""

    def test_lifetime_seconds(self):
        """Test lifetime calculation."""
        import time

        metadata = LoadMetadata(
            loaded_at=datetime.now(),
            loaded_by="test-user",
            source="registry:test",
            verified=True,
        )
        time.sleep(0.1)
        lifetime = metadata.lifetime_seconds()
        assert lifetime >= 0.1
        assert lifetime < 1.0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metadata = LoadMetadata(
            loaded_at=datetime.now(),
            loaded_by="test-user",
            source="registry:test",
            verified=True,
            server_id="test-server",
        )
        result = metadata.to_dict()

        assert "loaded_at" in result
        assert result["loaded_by"] == "test-user"
        assert result["source"] == "registry:test"
        assert result["verified"] is True
        assert result["ephemeral"] is True
        assert result["server_id"] == "test-server"
        assert "lifetime_seconds" in result


class TestRuntimeProviderStore:
    """Tests for RuntimeProviderStore."""

    def test_add_and_get(self):
        """Test adding and retrieving a provider."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()

        store.add(provider, metadata)
        result = store.get("test-provider")

        assert result is not None
        assert result[0] == provider
        assert result[1] == metadata

    def test_add_duplicate_raises_error(self):
        """Test adding duplicate provider raises error."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()

        store.add(provider, metadata)

        with pytest.raises(ValueError, match="already exists"):
            store.add(provider, metadata)

    def test_get_nonexistent(self):
        """Test getting nonexistent provider returns None."""
        store = RuntimeProviderStore()
        assert store.get("nonexistent") is None

    def test_get_provider(self):
        """Test getting just the provider."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()
        store.add(provider, metadata)

        result = store.get_provider("test-provider")

        assert result == provider

    def test_get_metadata(self):
        """Test getting just the metadata."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()
        store.add(provider, metadata)

        result = store.get_metadata("test-provider")

        assert result == metadata

    def test_remove(self):
        """Test removing a provider."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()
        store.add(provider, metadata)

        removed = store.remove("test-provider")

        assert removed == provider
        assert store.get("test-provider") is None

    def test_remove_nonexistent(self):
        """Test removing nonexistent provider returns None."""
        store = RuntimeProviderStore()
        assert store.remove("nonexistent") is None

    def test_exists(self):
        """Test checking if provider exists."""
        store = RuntimeProviderStore()
        provider = make_mock_provider("test-provider")
        metadata = make_metadata()
        store.add(provider, metadata)

        assert store.exists("test-provider") is True
        assert store.exists("nonexistent") is False

    def test_list_all(self):
        """Test listing all providers."""
        store = RuntimeProviderStore()
        provider1 = make_mock_provider("provider1")
        provider2 = make_mock_provider("provider2")
        store.add(provider1, make_metadata())
        store.add(provider2, make_metadata())

        result = store.list_all()

        assert len(result) == 2

    def test_list_ids(self):
        """Test listing all provider IDs."""
        store = RuntimeProviderStore()
        store.add(make_mock_provider("provider1"), make_metadata())
        store.add(make_mock_provider("provider2"), make_metadata())

        ids = store.list_ids()

        assert set(ids) == {"provider1", "provider2"}

    def test_count(self):
        """Test counting providers."""
        store = RuntimeProviderStore()
        assert store.count() == 0

        store.add(make_mock_provider("provider1"), make_metadata())
        assert store.count() == 1

        store.add(make_mock_provider("provider2"), make_metadata())
        assert store.count() == 2

    def test_clear(self):
        """Test clearing all providers."""
        store = RuntimeProviderStore()
        store.add(make_mock_provider("provider1"), make_metadata())
        store.add(make_mock_provider("provider2"), make_metadata())

        cleared = store.clear()

        assert len(cleared) == 2
        assert store.count() == 0

    def test_thread_safety(self):
        """Test thread safety of store operations."""
        store = RuntimeProviderStore()
        errors = []

        def add_providers(start: int):
            try:
                for i in range(10):
                    provider_id = f"provider_{start}_{i}"
                    provider = make_mock_provider(provider_id)
                    store.add(provider, make_metadata())
            except Exception as e:
                errors.append(e)

        def read_providers():
            try:
                for _ in range(50):
                    store.list_all()
                    store.count()
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t = threading.Thread(target=add_providers, args=(i,))
            threads.append(t)
        for _ in range(3):
            t = threading.Thread(target=read_providers)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert store.count() == 50

    def test_get_all_with_metadata(self):
        """Test getting all providers with metadata as dict."""
        store = RuntimeProviderStore()
        provider1 = make_mock_provider("provider1")
        provider2 = make_mock_provider("provider2")
        metadata1 = make_metadata(source="source1")
        metadata2 = make_metadata(source="source2")
        store.add(provider1, metadata1)
        store.add(provider2, metadata2)

        result = store.get_all_with_metadata()

        assert len(result) == 2
        assert "provider1" in result
        assert "provider2" in result
        assert result["provider1"][0] == provider1
        assert result["provider1"][1] == metadata1
