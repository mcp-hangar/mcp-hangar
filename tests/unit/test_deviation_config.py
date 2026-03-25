"""End-to-end verification of deviation detection pipeline against SC44-1 through SC44-4.

Tests cover:
- Config parsing: frequency_threshold_multiplier, min_observations, severity_overrides
  propagate from config dict through bootstrap to detector.
- SC44-1: New destination produces BehavioralDeviationDetected event in full pipeline.
- SC44-2: BehavioralDeviationEventHandler sets correct OTLP span attributes.
- SC44-3: mcp_hangar_behavioral_deviations_total counter increments on deviation.
- SC44-4: Different frequency_threshold_multiplier values produce different detection behavior.
"""

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from enterprise.behavioral.baseline_store import BaselineStore
from enterprise.behavioral.bootstrap import bootstrap_behavioral
from enterprise.behavioral.deviation_detector import DeviationDetector
from enterprise.behavioral.profiler import BehavioralProfiler
from mcp_hangar.application.event_handlers.behavioral_deviation_handler import (
    BehavioralDeviationEventHandler,
)
from mcp_hangar.domain.events import BehavioralDeviationDetected
from mcp_hangar.domain.value_objects.behavioral import (
    BehavioralMode,
    DeviationType,
    NetworkObservation,
)


def _make_observation(
    provider_id: str = "test-provider",
    host: str = "10.0.0.1",
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


def _seed_baseline(
    store: BaselineStore,
    provider_id: str,
    host: str,
    port: int,
    protocol: str,
    count: int,
) -> None:
    """Insert observation records into the baseline store."""
    obs = _make_observation(provider_id=provider_id, host=host, port=port, protocol=protocol)
    for _ in range(count):
        store.record_observation(obs)


class TestConfigFrequencyThreshold:
    """Verify frequency_threshold_multiplier propagates from config to detector."""

    def test_config_frequency_threshold_passed_to_detector(self) -> None:
        """bootstrap_behavioral with custom threshold creates detector using that value."""
        profiler = bootstrap_behavioral(
            db_path=":memory:",
            config={"deviation_detection": {"frequency_threshold_multiplier": 5.0}},
        )

        # Access internal detector to verify config propagated
        assert hasattr(profiler, "_detector")
        assert profiler._detector is not None
        assert profiler._detector._frequency_threshold == 5.0


class TestConfigMinObservations:
    """Verify min_observations propagates from config to detector."""

    def test_config_min_observations_passed_to_detector(self) -> None:
        """bootstrap_behavioral with custom min_observations creates detector using that value."""
        profiler = bootstrap_behavioral(
            db_path=":memory:",
            config={"deviation_detection": {"min_observations": 20}},
        )

        assert profiler._detector._min_observations == 20


class TestConfigSeverityOverrides:
    """Verify severity_overrides propagate from config to detector behavior."""

    def test_config_severity_overrides_passed_to_detector(self) -> None:
        """bootstrap_behavioral with severity_overrides changes detection severity."""
        profiler = bootstrap_behavioral(
            db_path=":memory:",
            config={
                "deviation_detection": {
                    "severity_overrides": {"new_destination": "low"},
                },
            },
        )

        # Set mode to ENFORCING and trigger a new_destination deviation
        profiler.set_mode("test-provider", BehavioralMode.ENFORCING)
        obs = _make_observation()
        result = profiler.record_observation(obs)

        assert len(result) == 1
        assert result[0]["deviation_type"] == DeviationType.NEW_DESTINATION.value
        assert result[0]["severity"] == "low"


class TestConfigDefaults:
    """Verify defaults applied when no deviation_detection block present."""

    def test_config_defaults_when_no_deviation_detection_block(self) -> None:
        """bootstrap_behavioral with empty config uses default threshold 3.0."""
        profiler = bootstrap_behavioral(db_path=":memory:", config={})

        assert profiler._detector is not None
        assert profiler._detector._frequency_threshold == 3.0
        assert profiler._detector._min_observations == 10


class TestSC44_1_NewDestinationProducesEvent:
    """SC44-1: New destination observation produces BehavioralDeviationDetected event."""

    def test_sc44_1_new_destination_produces_event(self) -> None:
        """Full pipeline: BaselineStore + DeviationDetector + Profiler + mock EventBus."""
        store = BaselineStore(":memory:")
        detector = DeviationDetector(baseline_store=store)
        mock_event_bus = MagicMock()

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
            event_bus=mock_event_bus,
        )

        # Set provider to ENFORCING mode
        store.set_mode("test-provider", BehavioralMode.ENFORCING)

        # Record observation to an unknown destination
        obs = _make_observation(host="unknown.evil.com", port=8080, protocol="tcp")
        result = profiler.record_observation(obs)

        # Pipeline must produce a deviation
        assert len(result) == 1
        assert result[0]["deviation_type"] == DeviationType.NEW_DESTINATION.value
        assert result[0]["severity"] == "critical"

        # Event bus must have been called with BehavioralDeviationDetected
        mock_event_bus.publish.assert_called_once()
        published_event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(published_event, BehavioralDeviationDetected)
        assert published_event.provider_id == "test-provider"
        assert published_event.deviation_type == "new_destination"
        assert published_event.severity == "critical"


class TestSC44_2_DeviationEventHasOtlpAttributes:
    """SC44-2: BehavioralDeviationEventHandler sets OTLP span attributes."""

    def test_sc44_2_deviation_event_has_otlp_attributes(self) -> None:
        """Event handler creates span with mcp.provider.id, violation_type, deviation_type, severity."""
        handler = BehavioralDeviationEventHandler()
        event = BehavioralDeviationDetected(
            provider_id="my-provider",
            deviation_type="new_destination",
            observed="10.0.0.1:8080/tcp",
            baseline_expected="not in baseline",
            severity="critical",
        )

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mcp_hangar.application.event_handlers.behavioral_deviation_handler.trace") as mock_trace:
            mock_trace.get_tracer.return_value = mock_tracer
            handler.handle(event)

        # Verify all 4 governance attributes are set on the OTLP span
        mock_span.set_attribute.assert_any_call("mcp.provider.id", "my-provider")
        mock_span.set_attribute.assert_any_call("mcp.enforcement.violation_type", "behavioral_deviation")
        mock_span.set_attribute.assert_any_call("mcp.behavioral.deviation_type", "new_destination")
        mock_span.set_attribute.assert_any_call("mcp.enforcement.violation_severity", "critical")


class TestSC44_3_MetricIncrements:
    """SC44-3: BEHAVIORAL_DEVIATIONS_TOTAL counter increments on deviation."""

    def test_sc44_3_metric_increments(self) -> None:
        """Handler calls record_behavioral_deviation to increment Prometheus counter."""
        handler = BehavioralDeviationEventHandler()
        event = BehavioralDeviationDetected(
            provider_id="metric-provider",
            deviation_type="protocol_drift",
            observed="db.internal:5432/udp",
            baseline_expected="db.internal:5432/tcp",
            severity="high",
        )

        with patch(
            "mcp_hangar.application.event_handlers.behavioral_deviation_handler.prometheus_metrics"
        ) as mock_metrics:
            handler.handle(event)
            mock_metrics.record_behavioral_deviation.assert_called_once_with(
                provider="metric-provider",
                deviation_type="protocol_drift",
            )


class TestSC44_4_ThresholdConfigurable:
    """SC44-4: Different threshold values produce different detection behavior."""

    def test_sc44_4_threshold_configurable(self) -> None:
        """Tight threshold (1.5) triggers FREQUENCY_ANOMALY, loose threshold (10.0) does not."""
        # Set up two stores with identical baseline data
        store_tight = BaselineStore(":memory:")
        store_loose = BaselineStore(":memory:")

        for store in (store_tight, store_loose):
            # One moderately high-frequency destination
            _seed_baseline(store, "test-provider", "moderate-freq.com", 443, "tcp", 30)
            # Two normal-frequency destinations (lower count)
            _seed_baseline(store, "test-provider", "normal1.com", 80, "tcp", 10)
            _seed_baseline(store, "test-provider", "normal2.com", 80, "tcp", 10)

        detector_tight = DeviationDetector(
            baseline_store=store_tight,
            frequency_threshold_multiplier=1.5,
            min_observations=10,
        )
        detector_loose = DeviationDetector(
            baseline_store=store_loose,
            frequency_threshold_multiplier=10.0,
            min_observations=10,
        )

        obs = _make_observation(host="moderate-freq.com", port=443, protocol="tcp")

        # Tight threshold (1.5x) should flag: 30 vs avg ~16.7 -> ratio ~1.8 > 1.5
        deviations_tight = detector_tight.check_observation(obs)
        assert len(deviations_tight) == 1
        assert deviations_tight[0]["deviation_type"] == DeviationType.FREQUENCY_ANOMALY.value

        # Loose threshold (10.0x) should NOT flag: ratio ~1.8 < 10.0
        deviations_loose = detector_loose.check_observation(obs)
        assert len(deviations_loose) == 0
