"""Tests for behavioral deviation event pipeline.

Covers the full deviation detection pipeline:
- BehavioralProfiler ENFORCING mode (check -> store -> publish events)
- BehavioralDeviationEventHandler (OTLP spans + Prometheus counter)
- Bootstrap wiring (DeviationDetector + EventBus injection)
- Backward compatibility (no detector)
"""

from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from enterprise.behavioral.baseline_store import BaselineStore
from enterprise.behavioral.deviation_detector import DeviationDetector
from enterprise.behavioral.profiler import BehavioralProfiler
from mcp_hangar.application.event_handlers.behavioral_deviation_handler import (
    BehavioralDeviationEventHandler,
)
from mcp_hangar.domain.events import BehavioralDeviationDetected, DomainEvent
from mcp_hangar.domain.value_objects.behavioral import (
    BehavioralMode,
    NetworkObservation,
)


def _make_observation(
    provider_id: str = "test-provider",
    host: str = "10.0.0.1",
    port: int = 443,
    protocol: str = "tcp",
) -> NetworkObservation:
    """Helper to create a NetworkObservation with sensible defaults."""
    import time

    return NetworkObservation(
        timestamp=time.time(),
        provider_id=provider_id,
        destination_host=host,
        destination_port=port,
        protocol=protocol,
        direction="outbound",
    )


class TestProfilerEnforcingMode:
    """Tests for BehavioralProfiler ENFORCING mode deviation detection."""

    def test_enforcing_mode_detects_new_destination_and_publishes_event(self) -> None:
        """ENFORCING mode with new destination publishes BehavioralDeviationDetected."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.ENFORCING)
        detector = DeviationDetector(baseline_store=store)
        mock_event_bus = MagicMock()

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
            event_bus=mock_event_bus,
        )

        obs = _make_observation()
        result = profiler.record_observation(obs)

        # Should return deviations
        assert len(result) == 1
        assert result[0]["deviation_type"] == "new_destination"
        assert result[0]["severity"] == "critical"

        # Should publish event via event_bus
        mock_event_bus.publish.assert_called_once()
        published_event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(published_event, BehavioralDeviationDetected)
        assert published_event.provider_id == "test-provider"
        assert published_event.deviation_type == "new_destination"
        assert published_event.severity == "critical"

    def test_enforcing_mode_checks_before_storing(self) -> None:
        """Verify detector.check_observation() is called BEFORE store.record_observation()."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.ENFORCING)
        detector = DeviationDetector(baseline_store=store)

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
        )

        # With empty baseline, first observation should be NEW_DESTINATION
        obs = _make_observation()
        result = profiler.record_observation(obs)

        # The deviation should be NEW_DESTINATION because baseline was empty at check time
        assert len(result) == 1
        assert result[0]["deviation_type"] == "new_destination"

        # After record_observation, the store should now contain the observation
        records = store.get_observations("test-provider")
        assert len(records) >= 1

    def test_profiler_returns_deviation_list(self) -> None:
        """ENFORCING mode returns list of deviation dicts from record_observation()."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.ENFORCING)
        detector = DeviationDetector(baseline_store=store)

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
        )

        obs = _make_observation()
        result = profiler.record_observation(obs)

        assert isinstance(result, list)
        assert len(result) > 0
        assert "deviation_type" in result[0]
        assert "observed" in result[0]
        assert "baseline_expected" in result[0]
        assert "severity" in result[0]

    def test_profiler_without_detector_backward_compat(self) -> None:
        """ENFORCING mode with detector=None stores observation and returns []."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.ENFORCING)

        # No detector, no event_bus -- backward compatible usage
        profiler = BehavioralProfiler(baseline_store=store)

        obs = _make_observation()
        result = profiler.record_observation(obs)

        assert result == []

        # Observation should still be stored
        records = store.get_observations("test-provider")
        assert len(records) >= 1


class TestProfilerOtherModes:
    """Tests for LEARNING and DISABLED modes -- no detection."""

    def test_learning_mode_no_detection(self) -> None:
        """LEARNING mode stores observation, no events published, returns []."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.LEARNING)
        detector = DeviationDetector(baseline_store=store)
        mock_event_bus = MagicMock()

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
            event_bus=mock_event_bus,
        )

        obs = _make_observation()
        result = profiler.record_observation(obs)

        assert result == []
        mock_event_bus.publish.assert_not_called()

        # Observation should be stored in baseline
        records = store.get_observations("test-provider")
        assert len(records) >= 1

    def test_disabled_mode_returns_empty(self) -> None:
        """DISABLED mode returns [] with no detection or storage."""
        store = BaselineStore(":memory:")
        # DISABLED is the default mode
        detector = DeviationDetector(baseline_store=store)
        mock_event_bus = MagicMock()

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
            event_bus=mock_event_bus,
        )

        obs = _make_observation()
        result = profiler.record_observation(obs)

        assert result == []
        mock_event_bus.publish.assert_not_called()


class TestBehavioralDeviationEventHandler:
    """Tests for the event handler that bridges deviations to OTLP + Prometheus."""

    def test_event_handler_increments_metric(self) -> None:
        """Handler calls record_behavioral_deviation() for Prometheus counter."""
        handler = BehavioralDeviationEventHandler()
        event = BehavioralDeviationDetected(
            provider_id="test-provider",
            deviation_type="new_destination",
            observed="10.0.0.1:443/tcp",
            baseline_expected="not in baseline",
            severity="critical",
        )

        with patch(
            "mcp_hangar.application.event_handlers.behavioral_deviation_handler.prometheus_metrics"
        ) as mock_metrics:
            handler.handle(event)
            mock_metrics.record_behavioral_deviation.assert_called_once_with(
                provider="test-provider",
                deviation_type="new_destination",
            )

    def test_event_handler_creates_otlp_span(self) -> None:
        """Handler creates OTLP span with correct governance attributes."""
        handler = BehavioralDeviationEventHandler()
        event = BehavioralDeviationDetected(
            provider_id="test-provider",
            deviation_type="new_destination",
            observed="10.0.0.1:443/tcp",
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

        mock_tracer.start_as_current_span.assert_called_once_with("behavioral.deviation")
        mock_span.set_attribute.assert_any_call("mcp.provider.id", "test-provider")
        mock_span.set_attribute.assert_any_call("mcp.enforcement.violation_type", "behavioral_deviation")
        mock_span.set_attribute.assert_any_call("mcp.behavioral.deviation_type", "new_destination")
        mock_span.set_attribute.assert_any_call("mcp.enforcement.violation_severity", "critical")

    def test_event_handler_ignores_non_deviation_events(self) -> None:
        """Handler ignores events that are not BehavioralDeviationDetected."""
        handler = BehavioralDeviationEventHandler()

        # Create a mock non-matching event
        non_deviation_event = MagicMock(spec=DomainEvent)
        type(non_deviation_event).__class__ = type(DomainEvent)

        with patch(
            "mcp_hangar.application.event_handlers.behavioral_deviation_handler.prometheus_metrics"
        ) as mock_metrics:
            handler.handle(non_deviation_event)
            mock_metrics.record_behavioral_deviation.assert_not_called()


class TestMultipleDeviations:
    """Tests for scenarios with multiple deviation events."""

    def test_multiple_deviations_publish_multiple_events(self) -> None:
        """Multiple observations producing deviations each publish separate events."""
        store = BaselineStore(":memory:")
        store.set_mode("test-provider", BehavioralMode.ENFORCING)
        detector = DeviationDetector(baseline_store=store)
        mock_event_bus = MagicMock()

        profiler = BehavioralProfiler(
            baseline_store=store,
            detector=detector,
            event_bus=mock_event_bus,
        )

        # First observation: new destination
        obs1 = _make_observation(host="10.0.0.1", port=80, protocol="tcp")
        result1 = profiler.record_observation(obs1)
        assert len(result1) == 1

        # Second observation: another new destination
        obs2 = _make_observation(host="10.0.0.2", port=443, protocol="tcp")
        result2 = profiler.record_observation(obs2)
        assert len(result2) == 1

        # 2 events should have been published
        assert mock_event_bus.publish.call_count == 2


class TestBootstrapWiring:
    """Tests for bootstrap_behavioral() detector + event_bus wiring."""

    def test_bootstrap_wires_detector(self) -> None:
        """bootstrap_behavioral() creates profiler with detector attached."""
        from enterprise.behavioral.bootstrap import bootstrap_behavioral

        config = {
            "deviation_detection": {
                "frequency_threshold_multiplier": 5.0,
                "min_observations": 20,
            },
        }
        mock_event_bus = MagicMock()
        profiler = bootstrap_behavioral(
            db_path=":memory:",
            config=config,
            event_bus=mock_event_bus,
        )

        # Verify the profiler has a detector and event_bus attached
        assert hasattr(profiler, "_detector")
        assert profiler._detector is not None
        assert hasattr(profiler, "_event_bus")
        assert profiler._event_bus is mock_event_bus
