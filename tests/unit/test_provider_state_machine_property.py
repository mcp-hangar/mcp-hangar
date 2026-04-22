"""Property-based tests for Provider state machine using Hypothesis.

Uses RuleBasedStateMachine to generate random sequences of state transitions
and verify invariants hold for ALL possible transition orderings.
"""

import pytest
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from hypothesis import strategies as st
from unittest.mock import Mock

from mcp_hangar.domain.model.provider import McpServer, VALID_TRANSITIONS
from mcp_hangar.domain.value_objects.provider import ProviderState
from mcp_hangar.domain.exceptions import InvalidStateTransitionError
from mcp_hangar.domain.events import McpServerStateChanged


# -- Hypothesis profiles for CI reproducibility --

settings.register_profile(
    "ci",
    max_examples=200,
    stateful_step_count=50,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
    database=None,
)
settings.register_profile(
    "dev",
    max_examples=50,
    stateful_step_count=30,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "debug",
    max_examples=10,
    stateful_step_count=10,
    suppress_health_check=[HealthCheck.too_slow],
)


def _make_provider() -> McpServer:
    """Create a minimal Provider for state machine testing."""
    return McpServer(mcp_server_id="test-prop", mode="subprocess",
    command=["echo", "test"],
    metrics_publisher=Mock(),)


class ProviderStateMachine(RuleBasedStateMachine):
    """State machine that exercises Provider state transitions randomly.

    The model tracks the expected state independently, and invariants
    verify the Provider agrees with the model after each step.
    """

    def __init__(self):
        super().__init__()
        self.provider = _make_provider()
        self.expected_state = ProviderState.COLD
        self.transition_count = 0

    @invariant()
    def state_is_valid_enum(self):
        """Provider state is always a valid ProviderState enum value."""
        assert self.provider.state in ProviderState

    @invariant()
    def state_matches_model(self):
        """Provider state matches our expected model state."""
        assert self.provider.state == self.expected_state

    # --- Valid transition rules ---

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.COLD)
    def cold_to_initializing(self):
        """COLD -> INITIALIZING"""
        self.provider._transition_to(ProviderState.INITIALIZING)
        self.expected_state = ProviderState.INITIALIZING
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.INITIALIZING)
    def initializing_to_ready(self):
        """INITIALIZING -> READY"""
        self.provider._transition_to(ProviderState.READY)
        self.expected_state = ProviderState.READY
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.INITIALIZING)
    def initializing_to_dead(self):
        """INITIALIZING -> DEAD"""
        self.provider._transition_to(ProviderState.DEAD)
        self.expected_state = ProviderState.DEAD
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.INITIALIZING)
    def initializing_to_degraded(self):
        """INITIALIZING -> DEGRADED"""
        self.provider._transition_to(ProviderState.DEGRADED)
        self.expected_state = ProviderState.DEGRADED
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.READY)
    def ready_to_cold(self):
        """READY -> COLD"""
        self.provider._transition_to(ProviderState.COLD)
        self.expected_state = ProviderState.COLD
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.READY)
    def ready_to_dead(self):
        """READY -> DEAD"""
        self.provider._transition_to(ProviderState.DEAD)
        self.expected_state = ProviderState.DEAD
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.READY)
    def ready_to_degraded(self):
        """READY -> DEGRADED"""
        self.provider._transition_to(ProviderState.DEGRADED)
        self.expected_state = ProviderState.DEGRADED
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.DEGRADED)
    def degraded_to_initializing(self):
        """DEGRADED -> INITIALIZING"""
        self.provider._transition_to(ProviderState.INITIALIZING)
        self.expected_state = ProviderState.INITIALIZING
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.DEGRADED)
    def degraded_to_cold(self):
        """DEGRADED -> COLD"""
        self.provider._transition_to(ProviderState.COLD)
        self.expected_state = ProviderState.COLD
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.DEAD)
    def dead_to_initializing(self):
        """DEAD -> INITIALIZING"""
        self.provider._transition_to(ProviderState.INITIALIZING)
        self.expected_state = ProviderState.INITIALIZING
        self.transition_count += 1
        self._verify_state_changed_event()

    @rule()
    @precondition(lambda self: self.expected_state == ProviderState.DEAD)
    def dead_to_degraded(self):
        """DEAD -> DEGRADED"""
        self.provider._transition_to(ProviderState.DEGRADED)
        self.expected_state = ProviderState.DEGRADED
        self.transition_count += 1
        self._verify_state_changed_event()

    # --- Invalid transition rules (should always raise) ---

    @rule(target_state=st.sampled_from(list(ProviderState)))
    def attempt_invalid_transition(self, target_state):
        """Attempt a transition; verify it raises if invalid."""
        valid_targets = VALID_TRANSITIONS.get(self.expected_state, set())
        if target_state in valid_targets or target_state == self.expected_state:
            return  # Skip -- this would be valid (or a no-op)

        with pytest.raises(InvalidStateTransitionError):
            self.provider._transition_to(target_state)

        # State must not have changed
        assert self.provider.state == self.expected_state

    # --- Helpers ---

    def _verify_state_changed_event(self):
        """Verify the last event batch contains McpServerStateChanged."""
        events = self.provider.collect_events()
        state_changed_events = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert len(state_changed_events) >= 1, f"Expected McpServerStateChanged event after transition, got: {events}"


# Create the test class that Hypothesis will run
TestProviderStateMachine = ProviderStateMachine.TestCase
TestProviderStateMachine.settings = settings(
    max_examples=100,
    stateful_step_count=40,
    suppress_health_check=[HealthCheck.too_slow],
)


# -- Exhaustive parametric tests for every transition pair --


class TestExhaustiveTransitions:
    """Verify every valid and invalid transition explicitly."""

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            (from_s, to_s)
            for from_s in ProviderState
            for to_s in ProviderState
            if to_s in VALID_TRANSITIONS.get(from_s, set())
        ],
    )
    def test_valid_transition(self, from_state, to_state):
        """Every transition in VALID_TRANSITIONS succeeds."""
        provider = _make_provider()
        # Force provider into from_state (bypass transition validation)
        provider._state = from_state
        provider._transition_to(to_state)
        assert provider.state == to_state

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            (from_s, to_s)
            for from_s in ProviderState
            for to_s in ProviderState
            if to_s not in VALID_TRANSITIONS.get(from_s, set()) and from_s != to_s
        ],
    )
    def test_invalid_transition_raises(self, from_state, to_state):
        """Every transition NOT in VALID_TRANSITIONS raises."""
        provider = _make_provider()
        provider._state = from_state
        with pytest.raises(InvalidStateTransitionError):
            provider._transition_to(to_state)

    @pytest.mark.parametrize("state", list(ProviderState))
    def test_same_state_transition_is_noop(self, state):
        """Transitioning to the same state is a no-op (no event, no error)."""
        provider = _make_provider()
        provider._state = state
        # Drain any pre-existing events
        provider.collect_events()

        # Same-state transition should not raise and not emit events
        provider._transition_to(state)
        assert provider.state == state
        events = provider.collect_events()
        state_changed = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert len(state_changed) == 0, f"Same-state transition {state}->{state} should not emit events"


class TestProviderStartsInCold:
    """Verify initial state invariants."""

    def test_provider_starts_cold(self):
        """New provider always starts in COLD state."""
        provider = _make_provider()
        assert provider.state == ProviderState.COLD

    def test_cold_provider_can_only_go_to_initializing(self):
        """From COLD, the only valid target is INITIALIZING."""
        valid = VALID_TRANSITIONS[ProviderState.COLD]
        assert valid == {ProviderState.INITIALIZING}

    def test_degraded_to_ready_is_never_valid(self):
        """DEGRADED -> READY is NOT a valid transition (must reinitialize)."""
        provider = _make_provider()
        provider._state = ProviderState.DEGRADED
        with pytest.raises(InvalidStateTransitionError):
            provider._transition_to(ProviderState.READY)


class TestEventEmission:
    """Verify event emission for state transitions."""

    def test_valid_transition_emits_exactly_one_event(self):
        """A single valid transition emits exactly one McpServerStateChanged."""
        provider = _make_provider()
        provider.collect_events()  # Drain initial events

        provider._transition_to(ProviderState.INITIALIZING)
        events = provider.collect_events()
        state_changed = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert len(state_changed) == 1

    def test_event_has_correct_states(self):
        """McpServerStateChanged event captures old and new state."""
        provider = _make_provider()
        provider.collect_events()

        provider._transition_to(ProviderState.INITIALIZING)
        events = provider.collect_events()
        event = [e for e in events if isinstance(e, McpServerStateChanged)][0]
        assert event.old_state == "cold"
        assert event.new_state == "initializing"

    def test_multiple_transitions_emit_multiple_events(self):
        """Each transition in a sequence emits its own event."""
        provider = _make_provider()
        provider.collect_events()

        provider._transition_to(ProviderState.INITIALIZING)
        provider._transition_to(ProviderState.READY)
        provider._transition_to(ProviderState.DEGRADED)

        events = provider.collect_events()
        state_changed = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert len(state_changed) == 3

        assert state_changed[0].old_state == "cold"
        assert state_changed[0].new_state == "initializing"
        assert state_changed[1].old_state == "initializing"
        assert state_changed[1].new_state == "ready"
        assert state_changed[2].old_state == "ready"
        assert state_changed[2].new_state == "degraded"

    def test_invalid_transition_emits_no_event(self):
        """Failed transitions must not emit any events."""
        provider = _make_provider()
        provider.collect_events()

        with pytest.raises(InvalidStateTransitionError):
            provider._transition_to(ProviderState.READY)  # COLD -> READY is invalid

        events = provider.collect_events()
        assert len(events) == 0
