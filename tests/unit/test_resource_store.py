"""Unit tests for enterprise ResourceStore -- CRUD, baseline computation, pruning.

Covers:
- record_sample inserts a row, get_samples returns it
- get_samples ordering (most recent first) and limit parameter
- get_samples for unknown provider returns empty list
- compute_and_store_baseline with insufficient samples returns None
- compute_and_store_baseline with 10+ samples returns baseline with mean/stddev
- Baseline cpu_mean and cpu_stddev validated against known values
- get_baseline returns None for unknown provider
- get_baseline returns stored baseline after compute
- prune removes old samples, preserves recent ones, returns correct count
- Provider isolation (samples from different providers do not cross)

All tests use real in-memory SQLite ResourceStore -- no mocking.
"""

from datetime import UTC, datetime, timedelta

from enterprise.behavioral.resource_store import ResourceStore
from mcp_hangar.domain.value_objects.behavioral import ResourceSample


def _make_sample(
    provider_id: str = "math",
    sampled_at: str = "2026-03-25T12:00:00",
    cpu: float = 10.0,
    mem: int = 1_000_000,
    mem_limit: int = 2_000_000,
    rx: int = 200,
    tx: int = 300,
) -> ResourceSample:
    """Factory for ResourceSample with sensible defaults."""
    return ResourceSample(
        provider_id=provider_id,
        sampled_at=sampled_at,
        cpu_percent=cpu,
        memory_bytes=mem,
        memory_limit_bytes=mem_limit,
        network_rx_bytes=rx,
        network_tx_bytes=tx,
    )


class TestResourceStoreCRUD:
    """Tests for record_sample and get_samples operations."""

    def test_record_and_get_single_sample(self) -> None:
        """Record one sample, get_samples returns it with correct fields."""
        store = ResourceStore(":memory:")
        sample = _make_sample(provider_id="math", sampled_at="2026-03-25T12:00:00")
        store.record_sample(sample)

        rows = store.get_samples("math")
        assert len(rows) == 1
        row = rows[0]
        assert row["provider_id"] == "math"
        assert row["sampled_at"] == "2026-03-25T12:00:00"
        assert row["cpu_percent"] == 10.0
        assert row["memory_bytes"] == 1_000_000
        assert row["memory_limit_bytes"] == 2_000_000
        assert row["network_rx_bytes"] == 200
        assert row["network_tx_bytes"] == 300

    def test_get_samples_ordered_by_time_desc(self) -> None:
        """Record 3 samples with different timestamps, verify descending order."""
        store = ResourceStore(":memory:")
        timestamps = [
            "2026-03-25T10:00:00",
            "2026-03-25T12:00:00",
            "2026-03-25T11:00:00",
        ]
        for ts in timestamps:
            store.record_sample(_make_sample(sampled_at=ts))

        rows = store.get_samples("math")
        assert len(rows) == 3
        assert rows[0]["sampled_at"] == "2026-03-25T12:00:00"
        assert rows[1]["sampled_at"] == "2026-03-25T11:00:00"
        assert rows[2]["sampled_at"] == "2026-03-25T10:00:00"

    def test_get_samples_respects_limit(self) -> None:
        """Record 5 samples, get_samples(limit=2) returns only 2."""
        store = ResourceStore(":memory:")
        for i in range(5):
            store.record_sample(_make_sample(sampled_at=f"2026-03-25T{10 + i:02d}:00:00"))

        rows = store.get_samples("math", limit=2)
        assert len(rows) == 2

    def test_get_samples_unknown_provider_empty(self) -> None:
        """get_samples for unknown provider returns empty list."""
        store = ResourceStore(":memory:")
        rows = store.get_samples("nonexistent")
        assert rows == []

    def test_provider_isolation(self) -> None:
        """Samples from different providers do not cross."""
        store = ResourceStore(":memory:")
        store.record_sample(_make_sample(provider_id="alpha", sampled_at="2026-03-25T12:00:00"))
        store.record_sample(_make_sample(provider_id="beta", sampled_at="2026-03-25T13:00:00"))

        alpha_rows = store.get_samples("alpha")
        beta_rows = store.get_samples("beta")
        assert len(alpha_rows) == 1
        assert len(beta_rows) == 1
        assert alpha_rows[0]["provider_id"] == "alpha"
        assert beta_rows[0]["provider_id"] == "beta"


class TestResourceStoreBaseline:
    """Tests for compute_and_store_baseline and get_baseline operations."""

    def test_compute_baseline_insufficient_samples(self) -> None:
        """Record 5 samples, compute returns None (need >= 10)."""
        store = ResourceStore(":memory:")
        for i in range(5):
            store.record_sample(_make_sample(sampled_at=f"2026-03-25T{10 + i:02d}:00:00"))

        result = store.compute_and_store_baseline("math")
        assert result is None

    def test_compute_baseline_with_sufficient_samples(self) -> None:
        """Record 15 samples with known values, verify baseline returned."""
        store = ResourceStore(":memory:")
        for i in range(15):
            store.record_sample(
                _make_sample(
                    sampled_at=f"2026-03-25T{10 + i:02d}:00:00",
                    cpu=20.0 + i,
                    mem=500_000 + i * 1000,
                )
            )

        result = store.compute_and_store_baseline("math")
        assert result is not None
        assert result["provider_id"] == "math"
        assert result["sample_count"] == 15
        assert "cpu_mean" in result
        assert "cpu_stddev" in result
        assert "memory_mean" in result
        assert "memory_stddev" in result
        assert "computed_at" in result

    def test_baseline_cpu_mean_correct(self) -> None:
        """10 samples all with cpu=25.0 -> cpu_mean=25.0, cpu_stddev=0.0."""
        store = ResourceStore(":memory:")
        for i in range(10):
            store.record_sample(_make_sample(sampled_at=f"2026-03-25T{10 + i:02d}:00:00", cpu=25.0))

        result = store.compute_and_store_baseline("math")
        assert result is not None
        assert result["cpu_mean"] == 25.0
        assert result["cpu_stddev"] == 0.0

    def test_baseline_stddev_correct(self) -> None:
        """Verify stddev is population stddev for known values.

        10 samples with cpu=[10, 20, 30, 40, 10, 20, 30, 40, 10, 20]
        mean = (10+20+30+40+10+20+30+40+10+20)/10 = 230/10 = 23.0
        variance = ((10-23)^2 + (20-23)^2 + (30-23)^2 + (40-23)^2 +
                    (10-23)^2 + (20-23)^2 + (30-23)^2 + (40-23)^2 +
                    (10-23)^2 + (20-23)^2) / 10
                 = (169 + 9 + 49 + 289 + 169 + 9 + 49 + 289 + 169 + 9) / 10
                 = 1210 / 10 = 121.0
        stddev = sqrt(121.0) = 11.0
        """
        store = ResourceStore(":memory:")
        cpu_values = [10, 20, 30, 40, 10, 20, 30, 40, 10, 20]
        for i, cpu_val in enumerate(cpu_values):
            store.record_sample(
                _make_sample(
                    sampled_at=f"2026-03-25T{10 + i:02d}:00:00",
                    cpu=float(cpu_val),
                )
            )

        result = store.compute_and_store_baseline("math")
        assert result is not None
        assert result["cpu_mean"] == 23.0
        assert result["cpu_stddev"] == 11.0

    def test_get_baseline_none_for_unknown(self) -> None:
        """get_baseline for unknown provider returns None."""
        store = ResourceStore(":memory:")
        assert store.get_baseline("nonexistent") is None

    def test_get_baseline_after_compute(self) -> None:
        """compute_and_store_baseline then get_baseline returns same data."""
        store = ResourceStore(":memory:")
        for i in range(10):
            store.record_sample(_make_sample(sampled_at=f"2026-03-25T{10 + i:02d}:00:00", cpu=25.0))

        computed = store.compute_and_store_baseline("math")
        assert computed is not None

        retrieved = store.get_baseline("math")
        assert retrieved is not None
        assert retrieved["cpu_mean"] == computed["cpu_mean"]
        assert retrieved["cpu_stddev"] == computed["cpu_stddev"]
        assert retrieved["sample_count"] == computed["sample_count"]


class TestResourceStorePrune:
    """Tests for prune operation."""

    def test_prune_removes_old_samples(self) -> None:
        """Record samples with old timestamps (30 days ago), prune removes them."""
        store = ResourceStore(":memory:")
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        store.record_sample(_make_sample(sampled_at=old_ts))

        deleted = store.prune(retention_days=7)
        assert deleted == 1
        assert store.get_samples("math") == []

    def test_prune_preserves_recent_samples(self) -> None:
        """Record recent samples, prune does not delete them."""
        store = ResourceStore(":memory:")
        recent_ts = datetime.now(UTC).isoformat()
        store.record_sample(_make_sample(sampled_at=recent_ts))

        deleted = store.prune(retention_days=7)
        assert deleted == 0
        assert len(store.get_samples("math")) == 1

    def test_prune_returns_correct_count(self) -> None:
        """Prune returns correct count of deleted rows."""
        store = ResourceStore(":memory:")
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        recent_ts = datetime.now(UTC).isoformat()

        # 3 old, 2 recent
        for i in range(3):
            ts = (datetime.now(UTC) - timedelta(days=30, seconds=i)).isoformat()
            store.record_sample(_make_sample(sampled_at=ts))
        for i in range(2):
            ts = datetime.now(UTC).isoformat()
            store.record_sample(_make_sample(sampled_at=ts))

        deleted = store.prune(retention_days=7)
        assert deleted == 3
        assert len(store.get_samples("math")) == 2
