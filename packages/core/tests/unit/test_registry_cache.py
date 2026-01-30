"""Tests for the registry cache."""

import threading
import time

import pytest

from mcp_hangar.infrastructure.registry.cache import RegistryCache


class TestRegistryCache:
    """Tests for RegistryCache."""

    def test_init_with_defaults(self):
        """Test cache initialization with default values."""
        cache = RegistryCache()
        assert cache.ttl_seconds == RegistryCache.DEFAULT_TTL_SECONDS
        assert cache.max_entries == RegistryCache.DEFAULT_MAX_ENTRIES
        assert cache.size() == 0

    def test_init_with_custom_values(self):
        """Test cache initialization with custom values."""
        cache = RegistryCache(ttl_seconds=3600, max_entries=500)
        assert cache.ttl_seconds == 3600
        assert cache.max_entries == 500

    def test_init_rejects_invalid_ttl(self):
        """Test cache rejects non-positive TTL."""
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            RegistryCache(ttl_seconds=0)
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            RegistryCache(ttl_seconds=-1)

    def test_init_rejects_invalid_max_entries(self):
        """Test cache rejects non-positive max_entries."""
        with pytest.raises(ValueError, match="max_entries must be positive"):
            RegistryCache(max_entries=0)
        with pytest.raises(ValueError, match="max_entries must be positive"):
            RegistryCache(max_entries=-1)

    def test_set_and_get(self):
        """Test basic set and get operations."""
        cache = RegistryCache()
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_nonexistent_key(self):
        """Test get returns None for nonexistent key."""
        cache = RegistryCache()
        assert cache.get("nonexistent") is None

    def test_delete(self):
        """Test delete removes entry."""
        cache = RegistryCache()
        cache.set("key1", "value1")
        assert cache.delete("key1") is True
        assert cache.get("key1") is None

    def test_delete_nonexistent(self):
        """Test delete returns False for nonexistent key."""
        cache = RegistryCache()
        assert cache.delete("nonexistent") is False

    def test_contains(self):
        """Test contains check."""
        cache = RegistryCache()
        cache.set("key1", "value1")
        assert cache.contains("key1") is True
        assert cache.contains("nonexistent") is False

    def test_ttl_expiration(self):
        """Test entries expire after TTL."""
        cache = RegistryCache(ttl_seconds=1)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        time.sleep(1.1)
        assert cache.get("key1") is None

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = RegistryCache(max_entries=3)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        cache.get("key1")

        cache.set("key4", "value4")

        assert cache.get("key1") == "value1"
        assert cache.get("key2") is None
        assert cache.get("key3") == "value3"
        assert cache.get("key4") == "value4"

    def test_clear(self):
        """Test clear removes all entries."""
        cache = RegistryCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        count = cache.clear()
        assert count == 2
        assert cache.size() == 0

    def test_purge_expired(self):
        """Test purge_expired removes expired entries."""
        cache = RegistryCache(ttl_seconds=1)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        time.sleep(1.1)
        cache.set("key3", "value3")
        purged = cache.purge_expired()
        assert purged == 2
        assert cache.get("key3") == "value3"

    def test_thread_safety(self):
        """Test cache is thread-safe."""
        cache = RegistryCache(max_entries=100)
        errors = []

        def writer(start: int):
            try:
                for i in range(100):
                    cache.set(f"key_{start}_{i}", f"value_{start}_{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    for i in range(10):
                        cache.get(f"key_0_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
        for _ in range(3):
            t = threading.Thread(target=reader)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert cache.size() <= 100

    def test_update_existing_key(self):
        """Test updating an existing key."""
        cache = RegistryCache()
        cache.set("key1", "value1")
        cache.set("key1", "value2")
        assert cache.get("key1") == "value2"
        assert cache.size() == 1
