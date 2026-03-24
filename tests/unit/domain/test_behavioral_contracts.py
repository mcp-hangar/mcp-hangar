"""Tests for behavioral profiling contracts, value objects, and domain event.

Verifies:
    - BehavioralMode enum has LEARNING, ENFORCING, DISABLED values
    - NetworkObservation frozen dataclass validates inputs
    - BehavioralModeChanged domain event inherits DomainEvent
    - IBehavioralProfiler, IBaselineStore, IDeviationDetector are runtime_checkable
    - NullBehavioralProfiler satisfies IBehavioralProfiler and returns DISABLED
"""

import pytest

from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation
from mcp_hangar.domain.events import BehavioralModeChanged
from mcp_hangar.domain.contracts.behavioral import (
    IBehavioralProfiler,
    IBaselineStore,
    IDeviationDetector,
    NullBehavioralProfiler,
)


# -- BehavioralMode enum --


class TestBehavioralMode:
    def test_learning_value(self) -> None:
        assert BehavioralMode.LEARNING.value == "learning"

    def test_enforcing_value(self) -> None:
        assert BehavioralMode.ENFORCING.value == "enforcing"

    def test_disabled_value(self) -> None:
        assert BehavioralMode.DISABLED.value == "disabled"

    def test_has_exactly_three_members(self) -> None:
        assert len(BehavioralMode) == 3

    def test_str_returns_value(self) -> None:
        assert str(BehavioralMode.LEARNING) == "learning"
        assert str(BehavioralMode.ENFORCING) == "enforcing"
        assert str(BehavioralMode.DISABLED) == "disabled"


# -- NetworkObservation frozen dataclass --


class TestNetworkObservation:
    def test_valid_construction(self) -> None:
        obs = NetworkObservation(
            timestamp=1234567890.0,
            provider_id="math",
            destination_host="api.openai.com",
            destination_port=443,
            protocol="https",
            direction="outbound",
        )
        assert obs.timestamp == 1234567890.0
        assert obs.provider_id == "math"
        assert obs.destination_host == "api.openai.com"
        assert obs.destination_port == 443
        assert obs.protocol == "https"
        assert obs.direction == "outbound"

    def test_frozen_immutable(self) -> None:
        obs = NetworkObservation(
            timestamp=1234567890.0,
            provider_id="math",
            destination_host="api.openai.com",
            destination_port=443,
            protocol="https",
            direction="outbound",
        )
        with pytest.raises(AttributeError):
            obs.provider_id = "other"  # type: ignore[misc]

    def test_rejects_empty_provider_id(self) -> None:
        with pytest.raises(ValueError, match="provider_id"):
            NetworkObservation(
                timestamp=1234567890.0,
                provider_id="",
                destination_host="api.openai.com",
                destination_port=443,
                protocol="https",
                direction="outbound",
            )

    def test_rejects_empty_destination_host(self) -> None:
        with pytest.raises(ValueError, match="destination_host"):
            NetworkObservation(
                timestamp=1234567890.0,
                provider_id="math",
                destination_host="",
                destination_port=443,
                protocol="https",
                direction="outbound",
            )

    def test_rejects_port_below_zero(self) -> None:
        with pytest.raises(ValueError, match="destination_port"):
            NetworkObservation(
                timestamp=1234567890.0,
                provider_id="math",
                destination_host="api.openai.com",
                destination_port=-1,
                protocol="https",
                direction="outbound",
            )

    def test_rejects_port_above_65535(self) -> None:
        with pytest.raises(ValueError, match="destination_port"):
            NetworkObservation(
                timestamp=1234567890.0,
                provider_id="math",
                destination_host="api.openai.com",
                destination_port=65536,
                protocol="https",
                direction="outbound",
            )

    def test_accepts_port_zero(self) -> None:
        obs = NetworkObservation(
            timestamp=1234567890.0,
            provider_id="math",
            destination_host="api.openai.com",
            destination_port=0,
            protocol="tcp",
            direction="outbound",
        )
        assert obs.destination_port == 0

    def test_accepts_port_65535(self) -> None:
        obs = NetworkObservation(
            timestamp=1234567890.0,
            provider_id="math",
            destination_host="api.openai.com",
            destination_port=65535,
            protocol="tcp",
            direction="outbound",
        )
        assert obs.destination_port == 65535


# -- BehavioralModeChanged domain event --


class TestBehavioralModeChanged:
    def test_has_event_id_and_occurred_at(self) -> None:
        event = BehavioralModeChanged(
            provider_id="math",
            old_mode="disabled",
            new_mode="learning",
        )
        assert hasattr(event, "event_id")
        assert hasattr(event, "occurred_at")
        assert event.event_id is not None
        assert event.occurred_at > 0

    def test_fields_correct(self) -> None:
        event = BehavioralModeChanged(
            provider_id="math",
            old_mode="disabled",
            new_mode="learning",
        )
        assert event.provider_id == "math"
        assert event.old_mode == "disabled"
        assert event.new_mode == "learning"
        assert event.schema_version == 1

    def test_schema_version_default(self) -> None:
        event = BehavioralModeChanged(
            provider_id="math",
            old_mode="learning",
            new_mode="enforcing",
        )
        assert event.schema_version == 1

    def test_is_domain_event(self) -> None:
        from mcp_hangar.domain.events import DomainEvent

        event = BehavioralModeChanged(
            provider_id="math",
            old_mode="disabled",
            new_mode="learning",
        )
        assert isinstance(event, DomainEvent)


# -- IBehavioralProfiler Protocol --


class TestIBehavioralProfiler:
    def test_is_runtime_checkable(self) -> None:
        assert hasattr(IBehavioralProfiler, "__protocol_attrs__") or hasattr(IBehavioralProfiler, "__abstractmethods__")
        # runtime_checkable allows isinstance checks
        assert isinstance(NullBehavioralProfiler(), IBehavioralProfiler)


# -- IBaselineStore Protocol --


class TestIBaselineStore:
    def test_is_runtime_checkable(self) -> None:
        # Verify Protocol is defined and runtime_checkable
        assert hasattr(IBaselineStore, "__protocol_attrs__") or hasattr(IBaselineStore, "__abstractmethods__")


# -- IDeviationDetector Protocol --


class TestIDeviationDetector:
    def test_is_runtime_checkable(self) -> None:
        # Verify Protocol is defined and runtime_checkable
        assert hasattr(IDeviationDetector, "__protocol_attrs__") or hasattr(IDeviationDetector, "__abstractmethods__")


# -- NullBehavioralProfiler --


class TestNullBehavioralProfiler:
    def test_satisfies_ibehavioralprofiler_protocol(self) -> None:
        profiler = NullBehavioralProfiler()
        assert isinstance(profiler, IBehavioralProfiler)

    def test_get_mode_returns_disabled_for_any_provider(self) -> None:
        profiler = NullBehavioralProfiler()
        assert profiler.get_mode("math") == BehavioralMode.DISABLED
        assert profiler.get_mode("weather") == BehavioralMode.DISABLED
        assert profiler.get_mode("") == BehavioralMode.DISABLED

    def test_set_mode_is_noop(self) -> None:
        profiler = NullBehavioralProfiler()
        # Should not raise
        profiler.set_mode("math", BehavioralMode.LEARNING)
        # Still returns DISABLED after set
        assert profiler.get_mode("math") == BehavioralMode.DISABLED

    def test_record_observation_is_noop(self) -> None:
        profiler = NullBehavioralProfiler()
        obs = NetworkObservation(
            timestamp=1234567890.0,
            provider_id="math",
            destination_host="api.openai.com",
            destination_port=443,
            protocol="https",
            direction="outbound",
        )
        # Should not raise
        profiler.record_observation(obs)


# -- Contracts importable from mcp_hangar.domain.contracts --


class TestBehavioralContractReExports:
    def test_all_contracts_importable_from_domain_contracts(self) -> None:
        from mcp_hangar.domain.contracts import (
            IBehavioralProfiler as ReExportedProfiler,
            IBaselineStore as ReExportedStore,
            IDeviationDetector as ReExportedDetector,
            NullBehavioralProfiler as ReExportedNull,
        )

        assert ReExportedProfiler is IBehavioralProfiler
        assert ReExportedStore is IBaselineStore
        assert ReExportedDetector is IDeviationDetector
        assert ReExportedNull is NullBehavioralProfiler

    def test_value_objects_importable_from_domain_value_objects(self) -> None:
        from mcp_hangar.domain.value_objects import (
            BehavioralMode as ReExportedMode,
            NetworkObservation as ReExportedObs,
        )

        assert ReExportedMode is BehavioralMode
        assert ReExportedObs is NetworkObservation
