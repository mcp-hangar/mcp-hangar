"""Unit tests for DeviationDetector -- behavioral deviation detection engine.

Tests cover all 3 detection rules:
- NEW_DESTINATION: observation to (host, port) not in baseline
- PROTOCOL_DRIFT: observation to known (host, port) with different protocol
- FREQUENCY_ANOMALY: destination contacted at rate above provider average * threshold

Also tests edge cases: no double-flagging, min_observations guard, custom
thresholds, severity overrides.
"""

import time

from enterprise.behavioral.baseline_store import BaselineStore
from enterprise.behavioral.deviation_detector import DeviationDetector
from mcp_hangar.domain.value_objects.behavioral import DeviationType, NetworkObservation


def _make_observation(
    provider_id: str = "test-provider",
    host: str = "1.2.3.4",
    port: int = 443,
    protocol: str = "tcp",
) -> NetworkObservation:
    """Helper to create a NetworkObservation with sensible defaults."""
    return NetworkObservation(
        timestamp=time.time(),
        provider_id=provider_id,
        destination_host=host,
        destination_port=port,
        protocol=protocol,
        direction="outbound",
    )


def _seed_baseline(store: BaselineStore, provider_id: str, host: str, port: int, protocol: str, count: int) -> None:
    """Insert observation records into the baseline store by calling record_observation repeatedly."""
    obs = _make_observation(provider_id=provider_id, host=host, port=port, protocol=protocol)
    for _ in range(count):
        store.record_observation(obs)


class TestNewDestination:
    """Tests for the NEW_DESTINATION detection rule."""

    def test_new_destination_detected(self) -> None:
        """Observation to unknown (host, port) returns NEW_DESTINATION deviation."""
        store = BaselineStore(":memory:")
        detector = DeviationDetector(baseline_store=store)

        obs = _make_observation(host="10.0.0.1", port=8080)
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.NEW_DESTINATION.value
        assert deviations[0]["severity"] == "critical"

    def test_empty_baseline_returns_new_destination(self) -> None:
        """Provider with no baseline at all flags observation as new destination."""
        store = BaselineStore(":memory:")
        detector = DeviationDetector(baseline_store=store)

        obs = _make_observation(host="api.example.com", port=443)
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.NEW_DESTINATION.value

    def test_new_destination_not_double_flagged_as_protocol_drift(self) -> None:
        """Completely new (host, port) returns only NEW_DESTINATION, not protocol drift."""
        store = BaselineStore(":memory:")
        # Add a baseline for a DIFFERENT host/port
        _seed_baseline(store, "test-provider", "other-host.com", 80, "tcp", 5)

        detector = DeviationDetector(baseline_store=store)
        obs = _make_observation(host="brand-new-host.com", port=9090, protocol="udp")
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.NEW_DESTINATION.value
        # Must NOT contain protocol drift
        deviation_types = [d["deviation_type"] for d in deviations]
        assert DeviationType.PROTOCOL_DRIFT.value not in deviation_types


class TestProtocolDrift:
    """Tests for the PROTOCOL_DRIFT detection rule."""

    def test_protocol_drift_detected(self) -> None:
        """Baseline has (host, port, tcp), observe same (host, port) with udp -> PROTOCOL_DRIFT."""
        store = BaselineStore(":memory:")
        _seed_baseline(store, "test-provider", "db.internal", 5432, "tcp", 5)

        detector = DeviationDetector(baseline_store=store)
        obs = _make_observation(host="db.internal", port=5432, protocol="udp")
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.PROTOCOL_DRIFT.value
        assert deviations[0]["severity"] == "high"


class TestFrequencyAnomaly:
    """Tests for the FREQUENCY_ANOMALY detection rule."""

    def test_frequency_anomaly_detected(self) -> None:
        """Destination with much higher observation count than average triggers anomaly."""
        store = BaselineStore(":memory:")

        # One destination with very high count
        _seed_baseline(store, "test-provider", "high-freq.com", 443, "tcp", 100)
        # Several destinations with low count to bring down the average
        _seed_baseline(store, "test-provider", "low1.com", 80, "tcp", 5)
        _seed_baseline(store, "test-provider", "low2.com", 80, "tcp", 5)
        _seed_baseline(store, "test-provider", "low3.com", 80, "tcp", 5)

        detector = DeviationDetector(baseline_store=store, min_observations=10)
        obs = _make_observation(host="high-freq.com", port=443, protocol="tcp")
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.FREQUENCY_ANOMALY.value
        assert deviations[0]["severity"] == "medium"

    def test_frequency_skipped_below_min_observations(self) -> None:
        """Destination with count below min_observations does not trigger frequency anomaly."""
        store = BaselineStore(":memory:")
        _seed_baseline(store, "test-provider", "low-count.com", 443, "tcp", 5)

        detector = DeviationDetector(baseline_store=store, min_observations=10)
        obs = _make_observation(host="low-count.com", port=443, protocol="tcp")
        deviations = detector.check_observation(obs)

        assert len(deviations) == 0

    def test_custom_threshold(self) -> None:
        """Tighter frequency_threshold_multiplier (1.5) catches less extreme anomalies."""
        store = BaselineStore(":memory:")

        # One destination moderately higher than average
        _seed_baseline(store, "test-provider", "moderate-freq.com", 443, "tcp", 30)
        _seed_baseline(store, "test-provider", "normal1.com", 80, "tcp", 10)
        _seed_baseline(store, "test-provider", "normal2.com", 80, "tcp", 10)

        detector = DeviationDetector(
            baseline_store=store,
            frequency_threshold_multiplier=1.5,
            min_observations=10,
        )
        obs = _make_observation(host="moderate-freq.com", port=443, protocol="tcp")
        deviations = detector.check_observation(obs)

        # With a 1.5x threshold, 30 vs avg ~16.7 should be flagged (30/16.7 = 1.8 > 1.5)
        assert len(deviations) == 1
        assert deviations[0]["deviation_type"] == DeviationType.FREQUENCY_ANOMALY.value


class TestKnownDestinationNoDeviation:
    """Tests for known destinations that do not trigger deviations."""

    def test_known_destination_no_deviation(self) -> None:
        """Destination in baseline with matching protocol and low count returns no deviations."""
        store = BaselineStore(":memory:")
        _seed_baseline(store, "test-provider", "api.example.com", 443, "tcp", 5)

        detector = DeviationDetector(baseline_store=store)
        obs = _make_observation(host="api.example.com", port=443, protocol="tcp")
        deviations = detector.check_observation(obs)

        assert len(deviations) == 0


class TestSeverityOverrides:
    """Tests for custom severity overrides."""

    def test_severity_overrides(self) -> None:
        """Custom severity_overrides override default severity levels."""
        store = BaselineStore(":memory:")
        detector = DeviationDetector(
            baseline_store=store,
            severity_overrides={"new_destination": "low"},
        )

        obs = _make_observation(host="unknown-host.com", port=9999)
        deviations = detector.check_observation(obs)

        assert len(deviations) == 1
        assert deviations[0]["severity"] == "low"
