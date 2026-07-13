"""Unit tests for Hook and HookPhase value objects."""

import pytest

from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase


class _StubEvent(DomainEvent):
    """Minimal concrete event for testing."""

    def __init__(self) -> None:
        super().__init__()


class TestHookPhase:
    def test_all_phases_are_strings(self):
        assert HookPhase.PRE_VALIDATE == "pre_validate"
        assert HookPhase.POST_VALIDATE == "post_validate"
        assert HookPhase.PRE_MUTATE == "pre_mutate"
        assert HookPhase.POST_MUTATE == "post_mutate"
        assert HookPhase.OBSERVE == "observe"

    def test_wire_phases_match_pr2624(self):
        # PR #2624 wire-level phases use exactly these string values.
        assert HookPhase.REQUEST == "request"
        assert HookPhase.RESPONSE == "response"

    def test_phase_count(self):
        assert len(HookPhase) == 7

    def test_phase_from_string(self):
        assert HookPhase("pre_validate") is HookPhase.PRE_VALIDATE

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            HookPhase("nonexistent")


class TestHook:
    def test_valid_hook(self):
        evt = _StubEvent()
        hook = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=0)
        assert hook.event is evt
        assert hook.phase is HookPhase.OBSERVE
        assert hook.sequence_number == 0

    def test_frozen_immutability(self):
        hook = Hook(event=_StubEvent(), phase=HookPhase.OBSERVE, sequence_number=0)
        with pytest.raises(AttributeError):
            hook.sequence_number = 5  # type: ignore[misc]

    def test_rejects_non_domain_event(self):
        with pytest.raises(TypeError, match="event must be a DomainEvent"):
            Hook(event="not_an_event", phase=HookPhase.OBSERVE, sequence_number=0)  # type: ignore[arg-type]

    def test_rejects_non_hook_phase(self):
        with pytest.raises(TypeError, match="phase must be a HookPhase"):
            Hook(event=_StubEvent(), phase="observe", sequence_number=0)  # type: ignore[arg-type]

    def test_rejects_negative_sequence(self):
        with pytest.raises(ValueError, match="sequence_number must be non-negative"):
            Hook(event=_StubEvent(), phase=HookPhase.OBSERVE, sequence_number=-1)

    def test_zero_sequence_is_valid(self):
        hook = Hook(event=_StubEvent(), phase=HookPhase.OBSERVE, sequence_number=0)
        assert hook.sequence_number == 0

    def test_large_sequence_number(self):
        hook = Hook(event=_StubEvent(), phase=HookPhase.PRE_VALIDATE, sequence_number=999_999)
        assert hook.sequence_number == 999_999

    def test_all_phases_accepted(self):
        for phase in HookPhase:
            hook = Hook(event=_StubEvent(), phase=phase, sequence_number=0)
            assert hook.phase is phase

    def test_equality(self):
        evt = _StubEvent()
        h1 = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=1)
        h2 = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=1)
        assert h1 == h2

    def test_different_phase_not_equal(self):
        evt = _StubEvent()
        h1 = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=1)
        h2 = Hook(event=evt, phase=HookPhase.PRE_VALIDATE, sequence_number=1)
        assert h1 != h2

    def test_different_sequence_not_equal(self):
        evt = _StubEvent()
        h1 = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=1)
        h2 = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=2)
        assert h1 != h2

    def test_hook_preserves_event_attributes(self):
        evt = _StubEvent()
        hook = Hook(event=evt, phase=HookPhase.OBSERVE, sequence_number=0)
        assert hasattr(hook.event, "event_id")
        assert hasattr(hook.event, "occurred_at")
