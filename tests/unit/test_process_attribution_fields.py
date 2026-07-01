"""Unit tests for optional process-attribution fields on enforcement events.

These fields are additive schema preparation for the Tetragon backend (#331)
and the forensic provenance chain (#333/#334). They default to ``None`` and
carry no behavior; the tests here pin down construction, serialization
round-trips, and backward compatibility with events stored before the fields
existed.
"""

import json

from mcp_hangar.domain.events import CapabilityViolationDetected, EgressBlocked
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

ATTRIBUTION_FIELDS = (
    "process_pid",
    "container_id",
    "pod_name",
    "pod_namespace",
    "node_name",
)


class TestDefaultConstruction:
    def test_capability_violation_defaults_to_none(self) -> None:
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Connection not in capabilities",
            enforcement_action="alert",
        )
        for field in ATTRIBUTION_FIELDS:
            assert getattr(event, field) is None

    def test_egress_blocked_defaults_to_none(self) -> None:
        event = EgressBlocked(
            mcp_server_id="fetch",
            destination_host="evil.example.com",
            destination_port=443,
            protocol="tcp",
        )
        for field in ATTRIBUTION_FIELDS:
            assert getattr(event, field) is None


class TestRoundTrip:
    def test_capability_violation_round_trip(self) -> None:
        serializer = EventSerializer()
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Blocked",
            enforcement_action="block",
            destination="evil.example.com:443",
            process_pid=4242,
            container_id="containerd://abc123",
            pod_name="mcp-math-7d9",
            pod_namespace="hangar-workloads",
            node_name="node-a",
        )

        event_type, data = serializer.serialize(event)
        restored = serializer.deserialize(event_type, data)

        assert isinstance(restored, CapabilityViolationDetected)
        assert restored.process_pid == 4242
        assert restored.container_id == "containerd://abc123"
        assert restored.pod_name == "mcp-math-7d9"
        assert restored.pod_namespace == "hangar-workloads"
        assert restored.node_name == "node-a"

    def test_egress_blocked_round_trip(self) -> None:
        serializer = EventSerializer()
        event = EgressBlocked(
            mcp_server_id="fetch",
            destination_host="10.0.0.5",
            destination_port=9200,
            protocol="tcp",
            process_pid=9001,
            container_id="docker://deadbeef",
            pod_name="mcp-fetch-0",
            pod_namespace="hangar",
            node_name="node-b",
        )

        event_type, data = serializer.serialize(event)
        restored = serializer.deserialize(event_type, data)

        assert isinstance(restored, EgressBlocked)
        assert restored.process_pid == 9001
        assert restored.container_id == "docker://deadbeef"
        assert restored.pod_name == "mcp-fetch-0"
        assert restored.pod_namespace == "hangar"
        assert restored.node_name == "node-b"


class TestBackwardCompatibility:
    def test_capability_violation_serialize_without_attribution(self) -> None:
        serializer = EventSerializer()
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Blocked",
            enforcement_action="alert",
        )

        event_type, data = serializer.serialize(event)
        restored = serializer.deserialize(event_type, data)

        for field in ATTRIBUTION_FIELDS:
            assert getattr(restored, field) is None

    def test_deserialize_legacy_payload_missing_attribution_keys(self) -> None:
        """A payload stored before the fields existed must deserialize with None."""
        serializer = EventSerializer()
        legacy_payload = {
            "_version": 2,
            "mcp_server_id": "math",
            "violation_type": "egress_undeclared",
            "violation_detail": "Blocked",
            "enforcement_action": "alert",
            "destination": None,
            "severity": "high",
            "schema_version": 2,
        }

        restored = serializer.deserialize("CapabilityViolationDetected", json.dumps(legacy_payload))

        assert isinstance(restored, CapabilityViolationDetected)
        assert restored.mcp_server_id == "math"
        for field in ATTRIBUTION_FIELDS:
            assert getattr(restored, field) is None

    def test_deserialize_legacy_egress_payload_missing_attribution_keys(self) -> None:
        serializer = EventSerializer()
        legacy_payload = {
            "_version": 1,
            "mcp_server_id": "fetch",
            "destination_host": "evil.example.com",
            "destination_port": 443,
            "protocol": "tcp",
            "enforcement_source": "networkpolicy",
            "schema_version": 1,
        }

        restored = serializer.deserialize("EgressBlocked", json.dumps(legacy_payload))

        assert isinstance(restored, EgressBlocked)
        assert restored.destination_host == "evil.example.com"
        for field in ATTRIBUTION_FIELDS:
            assert getattr(restored, field) is None
