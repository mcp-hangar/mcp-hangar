"""Tests for the response truncation system."""

import json
import threading
import time

import pytest

from mcp_hangar.domain.contracts.response_cache import NullResponseCache
from mcp_hangar.domain.value_objects.truncation import ContinuationId, TruncationConfig
from mcp_hangar.infrastructure.truncation.manager import TruncationManager
from mcp_hangar.infrastructure.truncation.memory_cache import MemoryResponseCache
from mcp_hangar.server.tools.batch.models import CallResult


class TestTruncationConfig:
    """Tests for TruncationConfig value object."""

    def test_default_values(self):
        """Test TruncationConfig has sensible defaults."""
        config = TruncationConfig()
        assert config.enabled is False
        assert config.max_batch_size_bytes == 900_000
        assert config.min_per_response_bytes == 10_000
        assert config.cache_ttl_s == 300
        assert config.cache_driver == "memory"
        assert config.redis_url is None
        assert config.max_cache_entries == 10_000
        assert config.preserve_json_structure is True
        assert config.truncate_on_line_boundary is True

    def test_from_dict_empty(self):
        """Test from_dict with None returns defaults."""
        config = TruncationConfig.from_dict(None)
        assert config.enabled is False
        assert config.max_batch_size_bytes == 900_000

    def test_from_dict_with_values(self):
        """Test from_dict with custom values."""
        data = {
            "enabled": True,
            "max_batch_size_bytes": 500_000,
            "min_per_response_bytes": 5_000,
            "cache_ttl_s": 600,
            "cache_driver": "memory",
            "max_cache_entries": 5_000,
        }
        config = TruncationConfig.from_dict(data)
        assert config.enabled is True
        assert config.max_batch_size_bytes == 500_000
        assert config.min_per_response_bytes == 5_000
        assert config.cache_ttl_s == 600
        assert config.max_cache_entries == 5_000

    def test_validation_max_batch_size(self):
        """Test validation rejects non-positive max_batch_size_bytes."""
        with pytest.raises(ValueError, match="max_batch_size_bytes must be positive"):
            TruncationConfig(max_batch_size_bytes=0)

    def test_validation_min_per_response(self):
        """Test validation rejects non-positive min_per_response_bytes."""
        with pytest.raises(ValueError, match="min_per_response_bytes must be positive"):
            TruncationConfig(min_per_response_bytes=0)

    def test_validation_min_exceeds_max(self):
        """Test validation rejects min > max."""
        with pytest.raises(ValueError, match="min_per_response_bytes cannot exceed"):
            TruncationConfig(max_batch_size_bytes=1000, min_per_response_bytes=2000)

    def test_validation_cache_ttl(self):
        """Test validation rejects non-positive cache_ttl_s."""
        with pytest.raises(ValueError, match="cache_ttl_s must be positive"):
            TruncationConfig(cache_ttl_s=0)

    def test_validation_cache_driver(self):
        """Test validation rejects invalid cache_driver."""
        with pytest.raises(ValueError, match="cache_driver must be"):
            TruncationConfig(cache_driver="invalid")

    def test_validation_redis_requires_url(self):
        """Test validation requires redis_url when cache_driver is redis."""
        with pytest.raises(ValueError, match="redis_url is required"):
            TruncationConfig(cache_driver="redis", redis_url=None)

    def test_redis_with_url_is_valid(self):
        """Test redis driver with URL is accepted."""
        config = TruncationConfig(cache_driver="redis", redis_url="redis://localhost:6379")
        assert config.cache_driver == "redis"
        assert config.redis_url == "redis://localhost:6379"


class TestContinuationId:
    """Tests for ContinuationId value object."""

    def test_generate_format(self):
        """Test generated continuation IDs have correct format."""
        cont_id = ContinuationId.generate("batch123", 5)
        assert cont_id.value.startswith("cont_batch123_5_")
        assert len(cont_id.value) == len("cont_batch123_5_") + 8  # 8 hex chars

    def test_generate_unique(self):
        """Test generated continuation IDs are unique."""
        ids = [ContinuationId.generate("batch", 0).value for _ in range(100)]
        assert len(set(ids)) == 100

    def test_str_returns_value(self):
        """Test string representation."""
        cont_id = ContinuationId(value="cont_test_0_abcd1234")
        assert str(cont_id) == "cont_test_0_abcd1234"

    def test_validation_empty(self):
        """Test validation rejects empty value."""
        with pytest.raises(ValueError, match="cannot be empty"):
            ContinuationId(value="")

    def test_validation_invalid_prefix(self):
        """Test validation rejects invalid prefix."""
        with pytest.raises(ValueError, match="must start with 'cont_'"):
            ContinuationId(value="invalid_id")


class TestNullResponseCache:
    """Tests for NullResponseCache."""

    def test_store_is_noop(self):
        """Test store does nothing."""
        cache = NullResponseCache()
        cache.store("id", {"data": "test"}, 300)
        # No exception, no effect

    def test_retrieve_returns_not_found(self):
        """Test retrieve always returns not found."""
        cache = NullResponseCache()
        cache.store("id", {"data": "test"}, 300)
        result = cache.retrieve("id")
        assert result.found is False

    def test_delete_returns_false(self):
        """Test delete always returns False."""
        cache = NullResponseCache()
        assert cache.delete("any_id") is False

    def test_clear_expired_returns_zero(self):
        """Test clear_expired always returns 0."""
        cache = NullResponseCache()
        assert cache.clear_expired() == 0


class TestMemoryResponseCache:
    """Tests for MemoryResponseCache."""

    def test_init_with_defaults(self):
        """Test cache initialization with defaults."""
        cache = MemoryResponseCache()
        assert cache.max_entries == 10_000
        assert cache.default_ttl_s == 300
        assert cache.size() == 0

    def test_init_with_custom_values(self):
        """Test cache initialization with custom values."""
        cache = MemoryResponseCache(max_entries=100, default_ttl_s=60)
        assert cache.max_entries == 100
        assert cache.default_ttl_s == 60

    def test_init_rejects_invalid_max_entries(self):
        """Test cache rejects non-positive max_entries."""
        with pytest.raises(ValueError, match="max_entries must be positive"):
            MemoryResponseCache(max_entries=0)

    def test_init_rejects_invalid_ttl(self):
        """Test cache rejects non-positive default_ttl_s."""
        with pytest.raises(ValueError, match="default_ttl_s must be positive"):
            MemoryResponseCache(default_ttl_s=0)

    def test_store_and_retrieve(self):
        """Test basic store and retrieve."""
        cache = MemoryResponseCache()
        data = {"key": "value", "nested": {"a": 1}}
        cache.store("cont_test_0_abc", data, 300)

        result = cache.retrieve("cont_test_0_abc")
        assert result.found is True
        assert result.data == data
        assert result.complete is True
        assert result.has_more is False

    def test_retrieve_nonexistent(self):
        """Test retrieve returns not found for nonexistent ID."""
        cache = MemoryResponseCache()
        result = cache.retrieve("nonexistent")
        assert result.found is False

    def test_retrieve_with_offset_limit(self):
        """Test retrieve with offset and limit for pagination."""
        cache = MemoryResponseCache()
        data = {"large": "x" * 1000}
        cache.store("cont_test_0_abc", data, 300)

        # Get first 500 bytes
        result = cache.retrieve("cont_test_0_abc", offset=0, limit=500)
        assert result.found is True
        assert result.offset == 0
        assert result.has_more is True
        assert result.complete is False

    def test_retrieve_offset_past_end(self):
        """Test retrieve with offset past content."""
        cache = MemoryResponseCache()
        cache.store("cont_test_0_abc", {"small": "data"}, 300)

        result = cache.retrieve("cont_test_0_abc", offset=10000)
        assert result.found is True
        assert result.data is None
        assert result.complete is True

    def test_delete(self):
        """Test delete removes entry."""
        cache = MemoryResponseCache()
        cache.store("cont_test_0_abc", {"data": 1}, 300)
        assert cache.delete("cont_test_0_abc") is True
        assert cache.retrieve("cont_test_0_abc").found is False

    def test_delete_nonexistent(self):
        """Test delete returns False for nonexistent."""
        cache = MemoryResponseCache()
        assert cache.delete("nonexistent") is False

    def test_ttl_expiration(self):
        """Test entries expire after TTL."""
        cache = MemoryResponseCache(default_ttl_s=1)
        cache.store("cont_test_0_abc", {"data": 1}, 1)
        assert cache.retrieve("cont_test_0_abc").found is True
        time.sleep(1.1)
        assert cache.retrieve("cont_test_0_abc").found is False

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = MemoryResponseCache(max_entries=3)
        cache.store("cont_1", {"d": 1}, 300)
        cache.store("cont_2", {"d": 2}, 300)
        cache.store("cont_3", {"d": 3}, 300)

        # Access cont_1 to make it recently used
        cache.retrieve("cont_1")

        # Add new entry, should evict cont_2 (oldest)
        cache.store("cont_4", {"d": 4}, 300)

        assert cache.retrieve("cont_1").found is True
        assert cache.retrieve("cont_2").found is False  # Evicted
        assert cache.retrieve("cont_3").found is True
        assert cache.retrieve("cont_4").found is True

    def test_clear_expired(self):
        """Test clear_expired removes expired entries."""
        cache = MemoryResponseCache(default_ttl_s=1)
        cache.store("cont_1", {"d": 1}, 1)
        cache.store("cont_2", {"d": 2}, 1)
        time.sleep(1.1)
        cache.store("cont_3", {"d": 3}, 300)

        cleared = cache.clear_expired()
        assert cleared == 2
        assert cache.retrieve("cont_3").found is True

    def test_clear(self):
        """Test clear removes all entries."""
        cache = MemoryResponseCache()
        cache.store("cont_1", {"d": 1}, 300)
        cache.store("cont_2", {"d": 2}, 300)
        cleared = cache.clear()
        assert cleared == 2
        assert cache.size() == 0

    def test_thread_safety(self):
        """Test cache is thread-safe."""
        cache = MemoryResponseCache(max_entries=100)
        errors = []

        def writer(start: int):
            try:
                for i in range(50):
                    cache.store(f"cont_{start}_{i}", {"v": i}, 300)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    for i in range(10):
                        cache.retrieve(f"cont_0_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
        for _ in range(3):
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert cache.size() <= 100


class TestTruncationManager:
    """Tests for TruncationManager."""

    def create_manager(self, **config_kwargs) -> TruncationManager:
        """Create a manager with test configuration."""
        defaults = {
            "enabled": True,
            "max_batch_size_bytes": 1000,
            "min_per_response_bytes": 100,
            "cache_ttl_s": 300,
        }
        defaults.update(config_kwargs)
        config = TruncationConfig(**defaults)
        cache = MemoryResponseCache()
        return TruncationManager(config, cache)

    def create_result(self, index: int, data: dict) -> CallResult:
        """Create a CallResult for testing."""
        return CallResult(
            index=index,
            call_id=f"call_{index}",
            success=True,
            result=data,
            elapsed_ms=100.0,
        )

    def test_process_batch_disabled(self):
        """Test process_batch is no-op when disabled."""
        config = TruncationConfig(enabled=False)
        cache = MemoryResponseCache()
        manager = TruncationManager(config, cache)

        results = [self.create_result(0, {"large": "x" * 10000})]
        processed = manager.process_batch("batch1", results)

        assert processed == results
        assert processed[0].truncated is False

    def test_process_batch_no_truncation_needed(self):
        """Test process_batch returns results unchanged when under limit."""
        manager = self.create_manager(max_batch_size_bytes=10000)

        results = [
            self.create_result(0, {"small": "data"}),
            self.create_result(1, {"also": "small"}),
        ]
        processed = manager.process_batch("batch1", results)

        assert len(processed) == 2
        assert processed[0].truncated is False
        assert processed[1].truncated is False
        assert processed[0].continuation_id is None

    def test_process_batch_truncates_large_responses(self):
        """Test process_batch truncates when total exceeds limit."""
        manager = self.create_manager(max_batch_size_bytes=500, min_per_response_bytes=50)

        # Create results that together exceed 500 bytes
        results = [
            self.create_result(0, {"data": "x" * 300}),
            self.create_result(1, {"data": "y" * 300}),
        ]
        processed = manager.process_batch("batch1", results)

        # At least one should be truncated
        truncated_count = sum(1 for r in processed if r.truncated)
        assert truncated_count >= 1

        # Truncated results should have continuation_id
        for r in processed:
            if r.truncated:
                assert r.continuation_id is not None
                assert r.continuation_id.startswith("cont_")
                assert r.truncated_reason == "batch_budget_exceeded"
                assert r.original_size_bytes is not None

    def test_truncated_responses_are_cached(self):
        """Test full responses are cached when truncated."""
        manager = self.create_manager(max_batch_size_bytes=200, min_per_response_bytes=50)

        original_data = {"data": "x" * 200}
        results = [self.create_result(0, original_data)]
        processed = manager.process_batch("batch1", results)

        assert processed[0].truncated is True
        cont_id = processed[0].continuation_id

        # Verify cached
        cached = manager.cache.retrieve(cont_id)
        assert cached.found is True
        assert cached.data == original_data

    def test_budget_allocation_proportional(self):
        """Test budget is allocated proportionally."""
        manager = self.create_manager(max_batch_size_bytes=1000, min_per_response_bytes=100)

        # First result is 3x larger than second
        results = [
            self.create_result(0, {"data": "x" * 600}),
            self.create_result(1, {"data": "y" * 200}),
        ]
        processed = manager.process_batch("batch1", results)

        # Larger response should get larger budget
        if processed[0].truncated and processed[1].truncated:
            # Both truncated - check relative sizes
            size0 = len(json.dumps(processed[0].result or {}).encode())
            size1 = len(json.dumps(processed[1].result or {}).encode())
            # First should be >= second since it had proportionally more budget
            assert size0 >= size1

    def test_smart_truncate_preserves_json_structure(self):
        """Test smart truncation preserves JSON structure."""
        manager = self.create_manager(max_batch_size_bytes=200, preserve_json_structure=True)

        results = [
            self.create_result(
                0,
                {
                    "key1": "value1",
                    "key2": "value2",
                    "large": "x" * 500,
                },
            )
        ]
        processed = manager.process_batch("batch1", results)

        assert processed[0].truncated is True
        # Result should still be valid JSON
        result_data = processed[0].result
        assert isinstance(result_data, dict)
        # Keys should be preserved
        assert "key1" in result_data or "key2" in result_data or "large" in result_data

    def test_truncate_list_preserves_structure(self):
        """Test list truncation preserves structure."""
        manager = self.create_manager(max_batch_size_bytes=200, min_per_response_bytes=50)

        results = [self.create_result(0, {"items": ["item" + str(i) for i in range(100)]})]
        processed = manager.process_batch("batch1", results)

        if processed[0].truncated:
            result = processed[0].result
            assert isinstance(result, dict)
            assert "items" in result
            # Should be truncated list
            items = result["items"]
            assert isinstance(items, list)
            assert len(items) < 100

    def test_empty_batch(self):
        """Test process_batch handles empty batch."""
        manager = self.create_manager()
        processed = manager.process_batch("batch1", [])
        assert processed == []

    def test_none_results_unchanged(self):
        """Test results with None data are unchanged."""
        manager = self.create_manager(max_batch_size_bytes=100)

        results = [
            CallResult(
                index=0,
                call_id="call_0",
                success=False,
                result=None,
                error="Some error",
                error_type="TestError",
                elapsed_ms=50.0,
            )
        ]
        processed = manager.process_batch("batch1", results)

        assert processed[0].result is None
        assert processed[0].truncated is False


class TestCallResultContinuationId:
    """Tests for continuation_id field on CallResult."""

    def test_default_none(self):
        """Test continuation_id defaults to None."""
        result = CallResult(
            index=0,
            call_id="test",
            success=True,
        )
        assert result.continuation_id is None

    def test_can_set_continuation_id(self):
        """Test continuation_id can be set."""
        result = CallResult(
            index=0,
            call_id="test",
            success=True,
            continuation_id="cont_batch_0_abc12345",
        )
        assert result.continuation_id == "cont_batch_0_abc12345"
