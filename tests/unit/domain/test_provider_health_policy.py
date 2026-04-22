"""Unit tests for the provider health classification policy.

Covers all public functions and the private _normalize_state helper:
- _normalize_state: loose state normalization to ProviderState
- classify_provider_health: core classification logic
- classify_provider_health_from_provider: convenience wrapper
- to_health_status_string: legacy string helper
- ProviderHealthClassification.to_dict: result serialization
"""

import pytest

from mcp_hangar.domain.value_objects import HealthStatus, ProviderState
from mcp_hangar.domain.policies.provider_health import (
    ProviderHealthClassification,
    _normalize_state,
    classify_provider_health,
    classify_provider_health_from_provider,
    to_health_status_string,
)


class _FakeHealth:
    def __init__(self, consecutive_failures: int = 0):
        self._failures = consecutive_failures

    @property
    def consecutive_failures(self) -> int:
        return self._failures


class _FakeProvider:
    def __init__(self, state: ProviderState, consecutive_failures: int = 0):
        self._state = state
        self._health = _FakeHealth(consecutive_failures)

    @property
    def state(self) -> ProviderState:
        return self._state

    @property
    def health(self) -> _FakeHealth:
        return self._health


class _FakeStateWithValue:
    def __init__(self, value: str):
        self.value = value


class TestNormalizeState:
    """_normalize_state converts loose state representations to ProviderState."""

    def test_mcp_server_state_enum_passes_through_unchanged(self):
        for state in ProviderState:
            result = _normalize_state(state)
            assert result is state

    def test_string_ready_normalizes_to_ready(self):
        result = _normalize_state("ready")
        assert result == ProviderState.READY

    def test_string_dead_normalizes_to_dead(self):
        result = _normalize_state("dead")
        assert result == ProviderState.DEAD

    def test_string_cold_normalizes_to_cold(self):
        result = _normalize_state("cold")
        assert result == ProviderState.COLD

    def test_string_initializing_normalizes_to_initializing(self):
        result = _normalize_state("initializing")
        assert result == ProviderState.INITIALIZING

    def test_string_degraded_normalizes_to_degraded(self):
        result = _normalize_state("degraded")
        assert result == ProviderState.DEGRADED

    def test_unknown_string_normalizes_to_dead_conservatively(self):
        result = _normalize_state("completely_unknown_state")
        assert result == ProviderState.DEAD

    def test_object_with_value_attribute_normalizes_correctly(self):
        fake = _FakeStateWithValue("ready")
        result = _normalize_state(fake)
        assert result == ProviderState.READY

    def test_object_with_uppercase_value_attribute_normalizes_correctly(self):
        fake = _FakeStateWithValue("DEAD")
        result = _normalize_state(fake)
        assert result == ProviderState.DEAD


class TestClassifyProviderHealthReadyState:
    """READY state classification depends on consecutive failure count."""

    def test_ready_zero_failures_returns_healthy(self):
        result = classify_provider_health(state=ProviderState.READY, consecutive_failures=0)
        assert result.status == HealthStatus.HEALTHY
        assert result.reason == "ready_no_failures"
        assert result.consecutive_failures == 0

    def test_ready_one_failure_returns_degraded(self):
        result = classify_provider_health(state=ProviderState.READY, consecutive_failures=1)
        assert result.status == HealthStatus.DEGRADED
        assert result.reason == "ready_with_failures"
        assert result.consecutive_failures == 1

    def test_ready_many_failures_returns_degraded(self):
        result = classify_provider_health(state=ProviderState.READY, consecutive_failures=10)
        assert result.status == HealthStatus.DEGRADED
        assert result.reason == "ready_with_failures"
        assert result.consecutive_failures == 10

    def test_ready_negative_failures_treated_as_zero_returns_healthy(self):
        result = classify_provider_health(state=ProviderState.READY, consecutive_failures=-3)
        assert result.status == HealthStatus.HEALTHY
        assert result.reason == "ready_no_failures"

    def test_ready_none_failures_treated_as_zero_returns_healthy(self):
        result = classify_provider_health(state=ProviderState.READY, consecutive_failures=None)
        assert result.status == HealthStatus.HEALTHY
        assert result.reason == "ready_no_failures"


class TestClassifyProviderHealthOtherStates:
    """Non-READY states map to fixed health outcomes regardless of failures."""

    def test_degraded_state_returns_health_degraded(self):
        result = classify_provider_health(state=ProviderState.DEGRADED, consecutive_failures=0)
        assert result.status == HealthStatus.DEGRADED
        assert result.reason == "mcp_server_state_degraded"

    def test_dead_state_returns_unhealthy(self):
        result = classify_provider_health(state=ProviderState.DEAD, consecutive_failures=0)
        assert result.status == HealthStatus.UNHEALTHY
        assert result.reason == "mcp_server_state_dead"

    def test_cold_state_returns_unknown(self):
        result = classify_provider_health(state=ProviderState.COLD, consecutive_failures=0)
        assert result.status == HealthStatus.UNKNOWN
        assert result.reason == "mcp_server_state_cold"

    def test_initializing_state_returns_unknown(self):
        result = classify_provider_health(state=ProviderState.INITIALIZING, consecutive_failures=0)
        assert result.status == HealthStatus.UNKNOWN
        assert result.reason == "mcp_server_state_initializing"

    def test_string_state_ready_works(self):
        result = classify_provider_health(state="ready", consecutive_failures=0)
        assert result.status == HealthStatus.HEALTHY

    def test_string_state_dead_works(self):
        result = classify_provider_health(state="dead", consecutive_failures=2)
        assert result.status == HealthStatus.UNHEALTHY
        assert result.reason == "mcp_server_state_dead"


class TestClassifyProviderHealthFromProvider:
    """Convenience wrapper delegates correctly to classify_provider_health."""

    def test_ready_provider_with_no_failures_returns_healthy(self):
        provider = _FakeProvider(state=ProviderState.READY, consecutive_failures=0)
        result = classify_provider_health_from_provider(provider)
        assert result.status == HealthStatus.HEALTHY
        assert result.reason == "ready_no_failures"

    def test_ready_provider_with_failures_returns_degraded(self):
        provider = _FakeProvider(state=ProviderState.READY, consecutive_failures=3)
        result = classify_provider_health_from_provider(provider)
        assert result.status == HealthStatus.DEGRADED
        assert result.consecutive_failures == 3

    def test_dead_provider_returns_unhealthy(self):
        provider = _FakeProvider(state=ProviderState.DEAD, consecutive_failures=0)
        result = classify_provider_health_from_provider(provider)
        assert result.status == HealthStatus.UNHEALTHY


class TestToHealthStatusString:
    """to_health_status_string returns the HealthStatus string value."""

    def test_ready_zero_failures_returns_healthy_string(self):
        result = to_health_status_string(state=ProviderState.READY, consecutive_failures=0)
        assert result == "healthy"

    def test_ready_with_failures_returns_degraded_string(self):
        result = to_health_status_string(state=ProviderState.READY, consecutive_failures=2)
        assert result == "degraded"

    def test_dead_state_returns_unhealthy_string(self):
        result = to_health_status_string(state=ProviderState.DEAD)
        assert result == "unhealthy"

    def test_cold_state_returns_unknown_string(self):
        result = to_health_status_string(state=ProviderState.COLD)
        assert result == "unknown"


class TestProviderHealthClassification:
    """ProviderHealthClassification dataclass structure and serialization."""

    def test_to_dict_returns_correct_keys_and_values(self):
        classification = ProviderHealthClassification(
            status=HealthStatus.HEALTHY,
            reason="ready_no_failures",
            consecutive_failures=0,
        )
        result = classification.to_dict()
        assert result == {
            "status": "healthy",
            "reason": "ready_no_failures",
            "consecutive_failures": 0,
        }

    def test_to_dict_degraded_result_serializes_correctly(self):
        classification = ProviderHealthClassification(
            status=HealthStatus.DEGRADED,
            reason="ready_with_failures",
            consecutive_failures=5,
        )
        result = classification.to_dict()
        assert result["status"] == "degraded"
        assert result["reason"] == "ready_with_failures"
        assert result["consecutive_failures"] == 5

    def test_classification_is_frozen_and_immutable(self):
        classification = ProviderHealthClassification(
            status=HealthStatus.UNKNOWN,
            reason="mcp_server_state_cold",
            consecutive_failures=0,
        )
        with pytest.raises(Exception):
            classification.status = HealthStatus.HEALTHY
