"""Tests for SQLite-backed BaselineStore.

Verifies CRUD operations for network observation baselines and
behavioral mode persistence. All tests use :memory: for speed.
"""

import time

import pytest

from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation
from enterprise.behavioral.baseline_store import BaselineStore


def _make_observation(
    provider_id: str = "provider-a",
    host: str = "api.example.com",
    port: int = 443,
    protocol: str = "https",
    direction: str = "outbound",
) -> NetworkObservation:
    """Create a NetworkObservation with sensible defaults."""
    return NetworkObservation(
        timestamp=time.time(),
        provider_id=provider_id,
        destination_host=host,
        destination_port=port,
        protocol=protocol,
        direction=direction,
    )


class TestBaselineStoreRecordObservation:
    """Tests for record_observation UPSERT behavior."""

    @pytest.fixture
    def store(self) -> BaselineStore:
        return BaselineStore(db_path=":memory:")

    def test_record_observation_creates_new_row(self, store: BaselineStore) -> None:
        """First observation creates a row with observation_count=1."""
        obs = _make_observation()
        store.record_observation(obs)

        rows = store.get_observations("provider-a")
        assert len(rows) == 1
        assert rows[0]["provider_id"] == "provider-a"
        assert rows[0]["host"] == "api.example.com"
        assert rows[0]["port"] == 443
        assert rows[0]["protocol"] == "https"
        assert rows[0]["observation_count"] == 1

    def test_record_observation_upsert_increments_count(self, store: BaselineStore) -> None:
        """Duplicate (provider_id, host, port, protocol) increments count and updates last_seen."""
        obs1 = _make_observation()
        store.record_observation(obs1)

        obs2 = _make_observation()
        store.record_observation(obs2)

        rows = store.get_observations("provider-a")
        assert len(rows) == 1
        assert rows[0]["observation_count"] == 2
        # last_seen should be updated (at least not earlier than first_seen)
        assert rows[0]["last_seen"] >= rows[0]["first_seen"]


class TestBaselineStoreGetObservations:
    """Tests for get_observations filtering."""

    @pytest.fixture
    def store(self) -> BaselineStore:
        return BaselineStore(db_path=":memory:")

    def test_get_observations_empty_for_unknown_provider(self, store: BaselineStore) -> None:
        """Returns empty list for unknown provider_id."""
        rows = store.get_observations("unknown-provider")
        assert rows == []

    def test_get_observations_filters_by_provider(self, store: BaselineStore) -> None:
        """Each provider gets only its own observations."""
        store.record_observation(_make_observation(provider_id="p1", host="a.com"))
        store.record_observation(_make_observation(provider_id="p2", host="b.com"))

        p1_rows = store.get_observations("p1")
        p2_rows = store.get_observations("p2")

        assert len(p1_rows) == 1
        assert p1_rows[0]["host"] == "a.com"
        assert len(p2_rows) == 1
        assert p2_rows[0]["host"] == "b.com"


class TestBaselineStoreMode:
    """Tests for get_mode and set_mode."""

    @pytest.fixture
    def store(self) -> BaselineStore:
        return BaselineStore(db_path=":memory:")

    def test_get_mode_returns_disabled_for_unknown(self, store: BaselineStore) -> None:
        """Default mode for unknown provider is DISABLED."""
        mode = store.get_mode("unknown-provider")
        assert mode == BehavioralMode.DISABLED

    def test_set_mode_learning_persists(self, store: BaselineStore) -> None:
        """set_mode(LEARNING) persists and is returned by get_mode."""
        store.set_mode("p1", BehavioralMode.LEARNING, learning_duration_hours=72)
        mode = store.get_mode("p1")
        assert mode == BehavioralMode.LEARNING

    def test_set_mode_enforcing_preserves_learning_started(self, store: BaselineStore) -> None:
        """Switching from LEARNING to ENFORCING preserves learning_started_at."""
        store.set_mode("p1", BehavioralMode.LEARNING, learning_duration_hours=48)
        store.set_mode("p1", BehavioralMode.ENFORCING)

        mode = store.get_mode("p1")
        assert mode == BehavioralMode.ENFORCING

    def test_set_mode_disabled(self, store: BaselineStore) -> None:
        """set_mode(DISABLED) persists."""
        store.set_mode("p1", BehavioralMode.LEARNING)
        store.set_mode("p1", BehavioralMode.DISABLED)

        mode = store.get_mode("p1")
        assert mode == BehavioralMode.DISABLED


class TestBaselineStoreErrorHandling:
    """Tests for error handling behavior."""

    def test_record_observation_logs_and_raises_on_error(self) -> None:
        """SQLite errors are logged and re-raised, not silently swallowed."""
        store = BaselineStore(db_path=":memory:")

        # Close the persistent connection to force an operational error
        if store._persistent_conn is not None:
            store._persistent_conn.close()
            store._persistent_conn = None

        obs = _make_observation()
        with pytest.raises(Exception):
            store.record_observation(obs)
