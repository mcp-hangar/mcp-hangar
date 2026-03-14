"""Property-based fuzz tests for EventSerializer and UpcasterChain.

Tests:
1. deserialize() returns DomainEvent (or raises EventSerializationError) for valid type names
2. deserialize() never leaks raw exceptions on arbitrary byte input
3. UpcasterChain.upcast() passthrough contract for unregistered types
4. Round-trip serialize -> deserialize for all 17 EVENT_TYPE_MAP types
"""

import json

from hypothesis import HealthCheck, given, settings, strategies as st

from mcp_hangar.domain.events import (
    CircuitBreakerStateChanged,
    DomainEvent,
    DiscoveryCycleCompleted,
    DiscoverySourceHealthChanged,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderApproved,
    ProviderDegraded,
    ProviderDiscovered,
    ProviderDiscoveryConfigChanged,
    ProviderDiscoveryLost,
    ProviderIdleDetected,
    ProviderQuarantined,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
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
    "ProviderStarted": ProviderStarted(provider_id="p1", mode="subprocess", tools_count=0, startup_duration_ms=0.0),
    "ProviderStopped": ProviderStopped(provider_id="p1", reason="shutdown"),
    "ProviderDegraded": ProviderDegraded(provider_id="p1", consecutive_failures=1, total_failures=1, reason="health"),
    "ProviderStateChanged": ProviderStateChanged(provider_id="p1", old_state="COLD", new_state="INITIALIZING"),
    "ProviderIdleDetected": ProviderIdleDetected(provider_id="p1", idle_duration_s=0.0, last_used_at=0.0),
    "ToolInvocationRequested": ToolInvocationRequested(provider_id="p1", tool_name="t", correlation_id="c1"),
    "ToolInvocationCompleted": ToolInvocationCompleted(
        provider_id="p1", tool_name="t", correlation_id="c1", duration_ms=0.0
    ),
    "ToolInvocationFailed": ToolInvocationFailed(
        provider_id="p1",
        tool_name="t",
        correlation_id="c1",
        error_message="e",
        error_type="RuntimeError",
    ),
    "HealthCheckPassed": HealthCheckPassed(provider_id="p1", duration_ms=0.0),
    "HealthCheckFailed": HealthCheckFailed(provider_id="p1", consecutive_failures=1, error_message="e"),
    "ProviderDiscovered": ProviderDiscovered(
        provider_name="p1", source_type="filesystem", mode="subprocess", fingerprint="abc"
    ),
    "ProviderDiscoveryLost": ProviderDiscoveryLost(provider_name="p1", source_type="filesystem", reason="ttl_expired"),
    "ProviderDiscoveryConfigChanged": ProviderDiscoveryConfigChanged(
        provider_name="p1",
        source_type="filesystem",
        old_fingerprint="a",
        new_fingerprint="b",
    ),
    "ProviderQuarantined": ProviderQuarantined(
        provider_name="p1",
        source_type="filesystem",
        reason="unknown_mode",
        validation_result="fail",
    ),
    "ProviderApproved": ProviderApproved(provider_name="p1", source_type="filesystem", approved_by="auto"),
    "DiscoveryCycleCompleted": DiscoveryCycleCompleted(
        discovered_count=0,
        registered_count=0,
        deregistered_count=0,
        quarantined_count=0,
        error_count=0,
        duration_ms=0.0,
    ),
    "DiscoverySourceHealthChanged": DiscoverySourceHealthChanged(source_type="filesystem", is_healthy=True),
    "CircuitBreakerStateChanged": CircuitBreakerStateChanged(provider_id="p1", old_state="closed", new_state="open"),
}


def _make_minimal_event(event_type: str) -> DomainEvent:
    return _MINIMAL_EVENTS[event_type]


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
            serializer.deserialize("ProviderStarted", text)
        except EventSerializationError:
            pass  # Correct wrapped error
        # If no exception: deserialize succeeded, which is fine
        # All other exceptions (json.JSONDecodeError, KeyError, etc.) will cause the test to fail

    @given(
        event_type=st.sampled_from(list(EVENT_TYPE_MAP.keys())),
    )
    @settings(max_examples=17)
    def test_round_trip_all_event_types(self, event_type: str) -> None:
        """serialize -> deserialize must produce an object of the same type with a valid
        event_id for every type registered in EVENT_TYPE_MAP."""
        serializer = EventSerializer()
        event = _make_minimal_event(event_type)
        type_name, json_data = serializer.serialize(event)
        restored = serializer.deserialize(type_name, json_data)
        assert type(restored) is type(event)
        assert restored.event_id is not None


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
