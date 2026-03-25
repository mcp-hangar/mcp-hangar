"""Tests for ResourceStore SQLite time-series storage.

Covers:
- DeviationType new resource variants exist
- ResourceSample frozen dataclass defined with all fields
- IResourceStore and IResourceMonitor protocols defined
- NullResourceMonitor no-op implementation
- ResourceStore CRUD: record_sample, get_samples, get_baseline, compute_and_store_baseline, prune
- Baseline computation requires minimum 10 samples
- Baseline uses mean + stddev calculation
"""

import pytest

from mcp_hangar.domain.value_objects.behavioral import (
    DeviationType,
    ResourceSample,
)
from mcp_hangar.domain.contracts.behavioral import (
    IResourceMonitor,
    IResourceStore,
    NullResourceMonitor,
)


class TestDeviationTypeResourceVariants:
    """DeviationType enum has 3 new RESOURCE_* variants."""

    def test_resource_cpu_spike_exists(self) -> None:
        assert hasattr(DeviationType, "RESOURCE_CPU_SPIKE")
        assert DeviationType.RESOURCE_CPU_SPIKE.value == "resource_cpu_spike"

    def test_resource_memory_spike_exists(self) -> None:
        assert hasattr(DeviationType, "RESOURCE_MEMORY_SPIKE")
        assert DeviationType.RESOURCE_MEMORY_SPIKE.value == "resource_memory_spike"

    def test_resource_network_io_spike_exists(self) -> None:
        assert hasattr(DeviationType, "RESOURCE_NETWORK_IO_SPIKE")
        assert DeviationType.RESOURCE_NETWORK_IO_SPIKE.value == "resource_network_io_spike"

    def test_resource_deviation_types_str(self) -> None:
        assert str(DeviationType.RESOURCE_CPU_SPIKE) == "resource_cpu_spike"
        assert str(DeviationType.RESOURCE_MEMORY_SPIKE) == "resource_memory_spike"
        assert str(DeviationType.RESOURCE_NETWORK_IO_SPIKE) == "resource_network_io_spike"


class TestResourceSample:
    """ResourceSample frozen dataclass."""

    def test_creation(self) -> None:
        s = ResourceSample(
            provider_id="math",
            sampled_at="2026-03-25T00:00:00",
            cpu_percent=25.5,
            memory_bytes=1024000,
            memory_limit_bytes=2048000,
            network_rx_bytes=100,
            network_tx_bytes=200,
        )
        assert s.provider_id == "math"
        assert s.sampled_at == "2026-03-25T00:00:00"
        assert s.cpu_percent == 25.5
        assert s.memory_bytes == 1024000
        assert s.memory_limit_bytes == 2048000
        assert s.network_rx_bytes == 100
        assert s.network_tx_bytes == 200

    def test_frozen(self) -> None:
        s = ResourceSample(
            provider_id="math",
            sampled_at="2026-03-25T00:00:00",
            cpu_percent=25.5,
            memory_bytes=1024000,
            memory_limit_bytes=2048000,
            network_rx_bytes=100,
            network_tx_bytes=200,
        )
        with pytest.raises(AttributeError):
            s.cpu_percent = 50.0  # type: ignore[misc]


class TestIResourceStoreProtocol:
    """IResourceStore protocol is defined and runtime_checkable."""

    def test_protocol_exists(self) -> None:
        assert hasattr(IResourceStore, "__protocol_attrs__") or hasattr(IResourceStore, "__abstractmethods__")

    def test_has_record_sample(self) -> None:
        assert hasattr(IResourceStore, "record_sample")

    def test_has_get_samples(self) -> None:
        assert hasattr(IResourceStore, "get_samples")

    def test_has_get_baseline(self) -> None:
        assert hasattr(IResourceStore, "get_baseline")

    def test_has_compute_and_store_baseline(self) -> None:
        assert hasattr(IResourceStore, "compute_and_store_baseline")

    def test_has_prune(self) -> None:
        assert hasattr(IResourceStore, "prune")


class TestIResourceMonitorProtocol:
    """IResourceMonitor protocol is defined and runtime_checkable."""

    def test_protocol_exists(self) -> None:
        assert hasattr(IResourceMonitor, "__protocol_attrs__") or hasattr(IResourceMonitor, "__abstractmethods__")

    def test_has_start(self) -> None:
        assert hasattr(IResourceMonitor, "start")

    def test_has_stop(self) -> None:
        assert hasattr(IResourceMonitor, "stop")


class TestNullResourceMonitor:
    """NullResourceMonitor no-op implementation."""

    def test_running_is_false(self) -> None:
        nm = NullResourceMonitor()
        assert nm.running is False

    def test_start_is_noop(self) -> None:
        nm = NullResourceMonitor()
        nm.start()  # Should not raise
        assert nm.running is False

    def test_stop_is_noop(self) -> None:
        nm = NullResourceMonitor()
        nm.stop()  # Should not raise


class TestResourceStoreRecordAndGet:
    """ResourceStore record_sample and get_samples."""

    @pytest.fixture()
    def store(self):
        from enterprise.behavioral.resource_store import ResourceStore

        return ResourceStore(db_path=":memory:")

    def test_record_and_get_single(self, store) -> None:
        sample = ResourceSample("math", "2026-03-25T00:00:00", 25.5, 1024000, 2048000, 100, 200)
        store.record_sample(sample)
        samples = store.get_samples("math")
        assert len(samples) == 1
        assert samples[0]["cpu_percent"] == 25.5
        assert samples[0]["memory_bytes"] == 1024000
        assert samples[0]["network_rx_bytes"] == 100
        assert samples[0]["network_tx_bytes"] == 200

    def test_get_samples_empty_for_unknown(self, store) -> None:
        assert store.get_samples("nonexistent") == []

    def test_get_samples_limit(self, store) -> None:
        for i in range(20):
            store.record_sample(ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 10.0, 100, 200, 50, 100))
        samples = store.get_samples("p", limit=5)
        assert len(samples) == 5

    def test_get_samples_ordered_desc(self, store) -> None:
        store.record_sample(ResourceSample("p", "2026-01-01T00:00:00", 10.0, 100, 200, 50, 100))
        store.record_sample(ResourceSample("p", "2026-01-01T00:05:00", 20.0, 200, 300, 60, 110))
        samples = store.get_samples("p")
        assert samples[0]["sampled_at"] >= samples[1]["sampled_at"]

    def test_get_samples_filters_by_provider(self, store) -> None:
        store.record_sample(ResourceSample("a", "2026-01-01T00:00:00", 10.0, 100, 200, 50, 100))
        store.record_sample(ResourceSample("b", "2026-01-01T00:00:00", 20.0, 200, 300, 60, 110))
        assert len(store.get_samples("a")) == 1
        assert len(store.get_samples("b")) == 1


class TestResourceStoreBaseline:
    """ResourceStore compute_and_store_baseline + get_baseline."""

    @pytest.fixture()
    def store(self):
        from enterprise.behavioral.resource_store import ResourceStore

        return ResourceStore(db_path=":memory:")

    def test_baseline_requires_min_10_samples(self, store) -> None:
        for i in range(9):
            store.record_sample(ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 10.0 + i, 100000, 200000, 50, 100))
        assert store.compute_and_store_baseline("p") is None

    def test_baseline_computed_with_10_samples(self, store) -> None:
        for i in range(10):
            store.record_sample(
                ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 10.0 + i, 100000 + i * 1000, 200000, 50 + i, 100 + i)
            )
        baseline = store.compute_and_store_baseline("p")
        assert baseline is not None
        assert baseline["sample_count"] == 10
        assert baseline["cpu_mean"] > 0
        assert baseline["cpu_stddev"] >= 0
        assert baseline["memory_mean"] > 0
        assert "computed_at" in baseline

    def test_get_baseline_none_before_compute(self, store) -> None:
        assert store.get_baseline("p") is None

    def test_get_baseline_returns_computed(self, store) -> None:
        for i in range(12):
            store.record_sample(ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 10.0 + i, 100000, 200000, 50, 100))
        store.compute_and_store_baseline("p")
        stored = store.get_baseline("p")
        assert stored is not None
        assert stored["sample_count"] == 12
        assert stored["cpu_mean"] > 0

    def test_baseline_upserts(self, store) -> None:
        for i in range(10):
            store.record_sample(ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 10.0, 100000, 200000, 50, 100))
        store.compute_and_store_baseline("p")
        # Add more samples and recompute
        for i in range(10, 20):
            store.record_sample(ResourceSample("p", f"2026-01-01T00:{i:02d}:00", 50.0, 500000, 200000, 150, 200))
        baseline = store.compute_and_store_baseline("p")
        assert baseline is not None
        assert baseline["sample_count"] == 20


class TestResourceStorePrune:
    """ResourceStore prune functionality."""

    @pytest.fixture()
    def store(self):
        from enterprise.behavioral.resource_store import ResourceStore

        return ResourceStore(db_path=":memory:")

    def test_prune_deletes_old_samples(self, store) -> None:
        # Insert an old sample
        store.record_sample(ResourceSample("p", "2020-01-01T00:00:00", 10.0, 100, 200, 50, 100))
        # Insert a recent sample
        store.record_sample(ResourceSample("p", "2099-01-01T00:00:00", 20.0, 200, 300, 60, 110))
        deleted = store.prune(retention_days=7)
        assert deleted >= 1
        samples = store.get_samples("p")
        assert len(samples) == 1
        assert samples[0]["cpu_percent"] == 20.0

    def test_prune_returns_zero_when_nothing_to_delete(self, store) -> None:
        store.record_sample(ResourceSample("p", "2099-01-01T00:00:00", 10.0, 100, 200, 50, 100))
        deleted = store.prune(retention_days=7)
        assert deleted == 0
