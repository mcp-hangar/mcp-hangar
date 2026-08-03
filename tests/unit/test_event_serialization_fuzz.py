# pyright: reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

"""Property-based fuzz tests for EventSerializer and UpcasterChain.

Tests:
1. deserialize() returns DomainEvent (or raises EventSerializationError) for valid type names
2. deserialize() never leaks raw exceptions on arbitrary byte input
3. UpcasterChain.upcast() passthrough contract for unregistered types
4. Round-trip serialize -> deserialize for all 18 EVENT_TYPE_MAP types
"""

import json
from datetime import UTC, datetime

import pytest

from hypothesis import HealthCheck, given, settings, strategies as st

import dataclasses

from mcp_hangar.domain.events import (
    CapabilityViolationDetected,
    CircuitBreakerStateChanged,
    DomainEvent,
    DiscoveryCycleCompleted,
    DiscoverySourceHealthChanged,
    EgressBlocked,
    EgressPolicyCleared,
    EgressPolicySet,
    EgressPolicyViolationObserved,
    HealthCheckFailed,
    HealthCheckPassed,
    PolicyPushRejected,
    McpServerApproved,
    McpServerCapabilityQuarantined,
    McpServerCapabilityQuarantineReleased,
    McpServerDegraded,
    McpServerDiscovered,
    McpServerDiscoveryConfigChanged,
    McpServerDiscoveryLost,
    McpServerIdleDetected,
    McpServerQuarantined,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.infrastructure.persistence.event_serializer import (
    EVENT_TYPE_MAP,
    EventSerializationError,
    EventSerializer,
)
from mcp_hangar.infrastructure.persistence.event_upcaster import (
    UpcasterChain,
)

# ---------------------------------------------------------------------------
# Minimal event factory
# ---------------------------------------------------------------------------

_MINIMAL_EVENTS: dict[str, DomainEvent] = {
    "McpServerStarted": McpServerStarted(mcp_server_id="p1", mode="subprocess", tools_count=0, startup_duration_ms=0.0),
    "McpServerStopped": McpServerStopped(mcp_server_id="p1", reason="shutdown"),
    "McpServerDegraded": McpServerDegraded(
        mcp_server_id="p1",
        consecutive_failures=1,
        total_failures=1,
        reason="health",
    ),
    "McpServerStateChanged": McpServerStateChanged(mcp_server_id="p1", old_state="COLD", new_state="INITIALIZING"),
    "McpServerIdleDetected": McpServerIdleDetected(mcp_server_id="p1", idle_duration_s=0.0, last_used_at=0.0),
    "ToolInvocationRequested": ToolInvocationRequested(mcp_server_id="p1", tool_name="t", correlation_id="c1"),
    "ToolInvocationCompleted": ToolInvocationCompleted(
        mcp_server_id="p1",
        tool_name="t",
        correlation_id="c1",
        duration_ms=0.0,
        result_size_bytes=0,
    ),
    "ToolInvocationFailed": ToolInvocationFailed(
        mcp_server_id="p1",
        tool_name="t",
        correlation_id="c1",
        duration_ms=0.0,
        error_message="e",
        error_type="RuntimeError",
    ),
    "HealthCheckPassed": HealthCheckPassed(mcp_server_id="p1", duration_ms=0.0),
    "HealthCheckFailed": HealthCheckFailed(mcp_server_id="p1", consecutive_failures=1, error_message="e"),
    "McpServerDiscovered": McpServerDiscovered(
        mcp_server_name="p1", source_type="filesystem", mode="subprocess", fingerprint="abc"
    ),
    "McpServerDiscoveryLost": McpServerDiscoveryLost(
        mcp_server_name="p1",
        source_type="filesystem",
        reason="ttl_expired",
    ),
    "McpServerDiscoveryConfigChanged": McpServerDiscoveryConfigChanged(
        mcp_server_name="p1",
        source_type="filesystem",
        old_fingerprint="a",
        new_fingerprint="b",
    ),
    "McpServerQuarantined": McpServerQuarantined(
        mcp_server_name="p1",
        source_type="filesystem",
        reason="unknown_mode",
        validation_result="invalid",
    ),
    "McpServerApproved": McpServerApproved(mcp_server_name="p1", source_type="filesystem", approved_by="auto"),
    "DiscoveryCycleCompleted": DiscoveryCycleCompleted(
        discovered_count=0,
        registered_count=0,
        deregistered_count=0,
        quarantined_count=0,
        error_count=0,
        duration_ms=0.0,
    ),
    "DiscoverySourceHealthChanged": DiscoverySourceHealthChanged(source_type="filesystem", is_healthy=True),
    "CircuitBreakerStateChanged": CircuitBreakerStateChanged(mcp_server_id="p1", old_state="closed", new_state="open"),
    # Capability enforcement
    "CapabilityViolationDetected": CapabilityViolationDetected(
        mcp_server_id="p1",
        violation_type="egress_undeclared",
        violation_detail="Connection to 192.168.1.100:9200",
        enforcement_action="alert",
    ),
    "EgressBlocked": EgressBlocked(
        mcp_server_id="p1",
        destination_host="evil.example.com",
        destination_port=443,
        protocol="https",
    ),
    "EgressPolicyViolationObserved": EgressPolicyViolationObserved(
        mcp_server_id="p1",
        tool_name="t",
        would_be_action="deny",
        reasons=["denied by egress policy"],
    ),
    "EgressPolicySet": EgressPolicySet(
        mcp_server_id="p1",
        source="operator",
        mode="Enforce",
        default_action="deny",
        allow_rules=1,
        deny_rules=1,
        require_approval_rules=0,
        secret_pattern_groups=["jwt"],
        max_payload_bytes=262144,
    ),
    "EgressPolicyCleared": EgressPolicyCleared(
        mcp_server_id="p1",
        source="api",
    ),
    "McpServerCapabilityQuarantined": McpServerCapabilityQuarantined(
        mcp_server_id="p1",
        reason="3 violations in 60s",
        violation_count=3,
    ),
    "McpServerCapabilityQuarantineReleased": McpServerCapabilityQuarantineReleased(
        mcp_server_id="p1",
        released_by="ops@example.com",
    ),
    "PolicyPushRejected": PolicyPushRejected(
        principal_id="anonymous",
        reason="authentication_required",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    ),
    # Approval gate
    "ToolApprovalRequested": ToolApprovalRequested(
        approval_id="a1",
        mcp_server_id="p1",
        tool_name="t",
        arguments_hash="abc",
        channel="dashboard",
        expires_at="2025-01-01T00:00:00+00:00",
        correlation_id="c1",
    ),
    "ToolApprovalGranted": ToolApprovalGranted(
        approval_id="a1",
        mcp_server_id="p1",
        tool_name="t",
        decided_by="user@example.com",
        decided_at="2025-01-01T00:00:00+00:00",
    ),
    "ToolApprovalDenied": ToolApprovalDenied(
        approval_id="a1",
        mcp_server_id="p1",
        tool_name="t",
        decided_by="user@example.com",
        decided_at="2025-01-01T00:00:00+00:00",
        reason="too risky",
    ),
    "ToolApprovalExpired": ToolApprovalExpired(
        approval_id="a1",
        mcp_server_id="p1",
        tool_name="t",
        expired_at="2025-01-01T00:00:00+00:00",
    ),
}


def _sample_value(name: str, annotation: object) -> object:
    """A plausible value for a field, chosen from its annotation."""
    text = str(annotation)
    if "bool" in text:
        return True
    if "int" in text and "float" not in text:
        return 7
    if "float" in text:
        return 7.5
    if "datetime" in text:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    if "list" in text:
        return ["a"]
    if "dict" in text:
        return {"k": "v"}
    return f"sample-{name}"


def _build_event(event_type: str) -> DomainEvent:
    """Construct an event of the given type with every field populated.

    The hand-written samples in `_MINIMAL_EVENTS` are more realistic and are
    preferred where they exist. This fallback exists so that "every registered
    type round-trips" can actually mean every type: the registry holds 100+
    entries now that it is derived from the class hierarchy rather than curated,
    and hand-writing a sample per type is the kind of list that goes stale --
    which is the very failure this suite is here to catch.
    """
    cls = EVENT_TYPE_MAP[event_type]
    kwargs = {field.name: _sample_value(field.name, field.type) for field in dataclasses.fields(cls)}
    return cls(**kwargs)


def _make_minimal_event(event_type: str) -> DomainEvent:
    sample = _MINIMAL_EVENTS.get(event_type)
    return sample if sample is not None else _build_event(event_type)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEventSerializerFuzz:
    """Property-based tests for EventSerializer.deserialize()."""

    @given(
        event_type=st.sampled_from(list(EVENT_TYPE_MAP.keys())),
        payload=st.fixed_dictionaries({}),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_deserialize_valid_type_returns_domain_event_or_raises_serialization_error(
        self, event_type: str, payload: dict
    ) -> None:
        """deserialize() on a known event type either returns a DomainEvent or raises
        EventSerializationError. It must never propagate raw json/TypeError/KeyError etc."""
        serializer = EventSerializer()
        json_data = json.dumps(payload)
        try:
            result = serializer.deserialize(event_type, json_data)
            assert isinstance(result, DomainEvent)
        except EventSerializationError:
            pass  # Expected for minimal/empty payloads missing required fields

    @given(data=st.binary())
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_deserialize_arbitrary_bytes_never_leaks_raw_exception(self, data: bytes) -> None:
        """Any bytes decoded as UTF-8 must produce only EventSerializationError, never
        a raw json.JSONDecodeError, KeyError, TypeError, or AttributeError."""
        serializer = EventSerializer()
        text = data.decode("utf-8", errors="replace")
        try:
            serializer.deserialize("McpServerStarted", text)
        except EventSerializationError:
            pass  # Correct wrapped error
        # If no exception: deserialize succeeded, which is fine
        # All other exceptions (json.JSONDecodeError, KeyError, etc.) will cause the test to fail

    @pytest.mark.parametrize("event_type", sorted(EVENT_TYPE_MAP))
    def test_round_trip_all_event_types(self, event_type: str) -> None:
        """serialize -> deserialize must produce an object of the same type with a valid
        event_id for every type registered in EVENT_TYPE_MAP.

        Parametrized rather than hypothesis-sampled, because "every type" has to
        mean every type. This was ``@given(sampled_from(EVENT_TYPE_MAP))`` with
        ``max_examples=17`` against a map that now holds 42 entries: a single run
        could cover at most 17 of them, and which 17 depended on the seed. A type
        registered without a sample here therefore failed on some runs and passed
        on others -- it passed locally and failed in CI for exactly that reason.
        Parametrizing makes the claim in this docstring true and the result
        reproducible.
        """
        serializer = EventSerializer()
        event = _make_minimal_event(event_type)
        type_name, json_data = serializer.serialize(event)
        restored = serializer.deserialize(type_name, json_data)
        assert type(restored) is type(event)
        assert restored.event_id is not None

    @pytest.mark.parametrize("event_type", sorted(EVENT_TYPE_MAP))
    def test_round_trip_preserves_every_field(self, event_type: str) -> None:
        """Same type is not enough -- the values have to come back too.

        `PolicyPushRejected.timestamp` did not: JSON has no datetime, the encoder
        wrote an ISO string, and nothing parsed it back, so a `datetime` went
        into the store and a `str` came out. Silently, and only on replay.
        """
        serializer = EventSerializer()
        event = _make_minimal_event(event_type)
        restored = serializer.deserialize(*serializer.serialize(event))

        for field in dataclasses.fields(type(event)):
            original, roundtripped = getattr(event, field.name), getattr(restored, field.name)
            assert type(roundtripped) is type(original), (
                f"{event_type}.{field.name} came back as {type(roundtripped).__name__}, not {type(original).__name__}"
            )
            assert roundtripped == original, f"{event_type}.{field.name} changed value across a round trip"

    def test_the_identity_survives_a_round_trip(self) -> None:
        """Replay must not mint a new id or re-date the event."""
        serializer = EventSerializer()
        event = _make_minimal_event("McpServerStarted")
        restored = serializer.deserialize(*serializer.serialize(event))
        assert restored.event_id == event.event_id
        assert restored.occurred_at == event.occurred_at


class TestUpcasterChainFuzz:
    """Property-based tests for UpcasterChain.upcast()."""

    @given(
        event_type=st.text(min_size=1, max_size=50),
        version=st.integers(min_value=1, max_value=100),
        data=st.dictionaries(
            st.text(max_size=20),
            st.one_of(st.integers(), st.text(max_size=50), st.none()),
            max_size=10,
        ),
        current_version=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_upcaster_chain_without_registered_upcasters_is_passthrough(
        self,
        event_type: str,
        version: int,
        data: dict,
        current_version: int,
    ) -> None:
        """An empty UpcasterChain must always return (int, dict) and never raise
        UpcastingError regardless of inputs."""
        chain = UpcasterChain()
        returned_version, returned_data = chain.upcast(event_type, version, data, current_version=current_version)
        assert isinstance(returned_version, int)
        assert isinstance(returned_data, dict)
