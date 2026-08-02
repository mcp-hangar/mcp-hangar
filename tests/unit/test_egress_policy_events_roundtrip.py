"""The egress-policy audit events must survive a round trip through the store.

``EgressPolicySet`` and ``EgressPolicyCleared`` were added so that changing what
the enforcement plane blocks leaves an audit trail. An event the serializer
cannot read back is not an audit trail: ``EventSerializer.deserialize`` raises
``EventSerializationError`` on a type absent from ``_EVENT_CLASS_BY_TYPE``, so
the record would be written and then be unreadable.

Their immediate siblings -- ``EgressBlocked`` and
``EgressPolicyViolationObserved`` -- were already registered; these two were
not.
"""

import pytest

from mcp_hangar.domain.events import EgressPolicyCleared, EgressPolicySet
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer


@pytest.fixture
def serializer():
    return EventSerializer()


class TestEgressPolicySetRoundTrip:
    def test_survives_serialize_deserialize(self, serializer):
        event = EgressPolicySet(
            mcp_server_id="payments",
            source="operator",
            mode="Enforce",
            default_action="deny",
            allow_rules=2,
            deny_rules=1,
            require_approval_rules=1,
            secret_pattern_groups=["jwt"],
            max_payload_bytes=262144,
        )

        restored = serializer.deserialize(*serializer.serialize(event))

        assert isinstance(restored, EgressPolicySet)
        assert restored.mcp_server_id == "payments"
        assert restored.source == "operator"
        assert restored.mode == "Enforce"
        assert restored.default_action == "deny"
        assert restored.secret_pattern_groups == ["jwt"]
        assert restored.max_payload_bytes == 262144

    def test_audit_grade_fields_are_not_lost(self, serializer):
        """Rule counts are what tell an auditor which way enforcement moved."""
        event = EgressPolicySet(
            mcp_server_id="srv",
            source="api",
            mode="Audit",
            default_action="allow",
            allow_rules=7,
            deny_rules=3,
            require_approval_rules=2,
        )

        restored = serializer.deserialize(*serializer.serialize(event))

        assert (restored.allow_rules, restored.deny_rules, restored.require_approval_rules) == (7, 3, 2)


class TestEgressPolicyClearedRoundTrip:
    def test_survives_serialize_deserialize(self, serializer):
        event = EgressPolicyCleared(mcp_server_id="payments", source="api")

        restored = serializer.deserialize(*serializer.serialize(event))

        assert isinstance(restored, EgressPolicyCleared)
        assert restored.mcp_server_id == "payments"
        assert restored.source == "api"
