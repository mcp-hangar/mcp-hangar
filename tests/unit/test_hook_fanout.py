"""Unit tests for EventBus hook fan-out."""

from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase
from mcp_hangar.infrastructure.event_bus import EventBus


class _StubEvent(DomainEvent):
    def __init__(self) -> None:
        super().__init__()


class _RecordingSubscriber:
    """Captures hooks for assertion."""

    def __init__(self) -> None:
        self.hooks: list[Hook] = []

    def on_hook(self, hook: Hook) -> None:
        self.hooks.append(hook)


class _FailingSubscriber:
    def on_hook(self, hook: Hook) -> None:
        raise RuntimeError("boom")


class TestEventBusHookFanOut:

    def setup_method(self) -> None:
        self.bus = EventBus()

    def test_hook_delivered_on_publish(self):
        sub = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub)
        evt = _StubEvent()
        self.bus.publish(evt)

        assert len(sub.hooks) == 1
        assert sub.hooks[0].event is evt
        assert sub.hooks[0].phase is HookPhase.OBSERVE
        assert sub.hooks[0].sequence_number == 0

    def test_sequence_increments(self):
        sub = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub)
        self.bus.publish(_StubEvent())
        self.bus.publish(_StubEvent())
        self.bus.publish(_StubEvent())

        assert [h.sequence_number for h in sub.hooks] == [0, 1, 2]

    def test_multiple_subscribers_all_receive(self):
        s1 = _RecordingSubscriber()
        s2 = _RecordingSubscriber()
        self.bus.subscribe_hooks(s1)
        self.bus.subscribe_hooks(s2)
        self.bus.publish(_StubEvent())

        assert len(s1.hooks) == 1
        assert len(s2.hooks) == 1

    def test_unsubscribe_stops_delivery(self):
        sub = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub)
        self.bus.publish(_StubEvent())
        assert len(sub.hooks) == 1

        self.bus.unsubscribe_hooks(sub)
        self.bus.publish(_StubEvent())
        assert len(sub.hooks) == 1

    def test_unsubscribe_unknown_is_silent(self):
        sub = _RecordingSubscriber()
        self.bus.unsubscribe_hooks(sub)

    def test_failing_subscriber_does_not_break_others(self):
        s1 = _RecordingSubscriber()
        fail = _FailingSubscriber()
        s2 = _RecordingSubscriber()
        self.bus.subscribe_hooks(s1)
        self.bus.subscribe_hooks(fail)
        self.bus.subscribe_hooks(s2)
        self.bus.publish(_StubEvent())

        assert len(s1.hooks) == 1
        assert len(s2.hooks) == 1

    def test_flat_handlers_still_work_alongside_hooks(self):
        flat_events: list[DomainEvent] = []
        self.bus.subscribe(_StubEvent, lambda e: flat_events.append(e))
        sub = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub)

        evt = _StubEvent()
        self.bus.publish(evt)

        assert len(flat_events) == 1
        assert flat_events[0] is evt
        assert len(sub.hooks) == 1
        assert sub.hooks[0].event is evt

    def test_no_hook_subscribers_no_error(self):
        self.bus.publish(_StubEvent())

    def test_clear_resets_hook_subscribers_and_sequence(self):
        sub = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub)
        self.bus.publish(_StubEvent())
        assert sub.hooks[0].sequence_number == 0

        self.bus.clear()
        sub2 = _RecordingSubscriber()
        self.bus.subscribe_hooks(sub2)
        self.bus.publish(_StubEvent())
        assert sub2.hooks[0].sequence_number == 0
