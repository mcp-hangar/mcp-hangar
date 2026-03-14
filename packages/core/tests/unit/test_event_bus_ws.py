"""Tests for EventBus.unsubscribe_from_all WebSocket lifecycle support.

Covers the unsubscribe_from_all method added to EventBus for WebSocket
connection lifecycle management (subscribe on connect, unsubscribe on disconnect).
"""

from dataclasses import dataclass, field
from datetime import datetime, UTC


from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.infrastructure.event_bus import EventBus


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class TestEvent(DomainEvent):
    """Minimal DomainEvent subclass used only in tests."""

    message: str = "test"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "event_type": "TestEvent",
            "message": self.message,
        }


def make_bus() -> EventBus:
    """Create a fresh EventBus for each test."""
    return EventBus()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_subscribe_to_all_then_publish_handler_fires_once():
    """subscribe_to_all followed by publish causes handler to fire exactly once."""
    bus = make_bus()
    calls: list[DomainEvent] = []

    bus.subscribe_to_all(calls.append)
    bus.publish(TestEvent(message="hello"))

    assert len(calls) == 1
    assert calls[0].to_dict()["message"] == "hello"


def test_unsubscribe_from_all_prevents_handler_from_firing():
    """After unsubscribe_from_all, the handler no longer receives events."""
    bus = make_bus()
    calls: list[DomainEvent] = []

    bus.subscribe_to_all(calls.append)
    bus.unsubscribe_from_all(calls.append)
    bus.publish(TestEvent(message="ignored"))

    assert len(calls) == 0


def test_unsubscribe_from_all_unregistered_handler_is_silent():
    """unsubscribe_from_all on a never-registered handler raises no exception."""
    bus = make_bus()
    unrelated_handler = lambda event: None  # noqa: E731

    # Should not raise
    bus.unsubscribe_from_all(unrelated_handler)


def test_unsubscribe_one_of_two_handlers_only_remaining_fires():
    """Two handlers subscribed; unsubscribing one leaves only the other active."""
    bus = make_bus()
    calls_a: list[DomainEvent] = []
    calls_b: list[DomainEvent] = []

    bus.subscribe_to_all(calls_a.append)
    bus.subscribe_to_all(calls_b.append)
    bus.unsubscribe_from_all(calls_a.append)
    bus.publish(TestEvent(message="only_b"))

    assert len(calls_a) == 0
    assert len(calls_b) == 1


def test_resubscribe_after_unsubscribe_works():
    """subscribe -> unsubscribe -> subscribe again; handler fires once on publish."""
    bus = make_bus()
    calls: list[DomainEvent] = []

    bus.subscribe_to_all(calls.append)
    bus.unsubscribe_from_all(calls.append)
    bus.subscribe_to_all(calls.append)
    bus.publish(TestEvent(message="once"))

    assert len(calls) == 1
