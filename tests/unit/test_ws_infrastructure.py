"""Tests for WebSocket infrastructure: connection manager, event queue, and filters.

All tests are synchronous; asyncio event loop is managed explicitly where needed
to avoid pytest-asyncio dependency.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any


from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.server.api.ws.filters import matches_filters, parse_subscription_filters
from mcp_hangar.server.api.ws.manager import (
    EventStreamQueue,
    WebSocketConnectionManager,
    connection_manager,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeEvent(DomainEvent):
    """Minimal DomainEvent for filter/queue tests."""

    event_type: str = "FakeEvent"
    provider_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"event_type": self.event_type}
        if self.provider_id is not None:
            d["provider_id"] = self.provider_id
        return d


# ---------------------------------------------------------------------------
# WebSocketConnectionManager tests
# ---------------------------------------------------------------------------


def test_manager_register_increments_active_count():
    """Registering one connection raises active_count to 1."""
    mgr = WebSocketConnectionManager()
    mgr.register("id1", metadata={"user": "alice"})
    assert mgr.active_count == 1


def test_manager_register_two_connections_active_count_is_two():
    """Registering two distinct connections gives active_count of 2."""
    mgr = WebSocketConnectionManager()
    mgr.register("id1")
    mgr.register("id2")
    assert mgr.active_count == 2


def test_manager_unregister_reduces_count_to_zero():
    """Unregistering the only connection leaves active_count at 0."""
    mgr = WebSocketConnectionManager()
    mgr.register("id1")
    mgr.unregister("id1")
    assert mgr.active_count == 0


def test_manager_unregister_nonexistent_is_silent():
    """unregister on an unknown connection_id raises no exception."""
    mgr = WebSocketConnectionManager()
    mgr.unregister("does-not-exist")  # must not raise


def test_manager_concurrent_register_both_entries_survive():
    """Two threads registering simultaneously -- both entries are present afterwards."""
    mgr = WebSocketConnectionManager()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def register_conn(cid: str) -> None:
        try:
            barrier.wait()
            mgr.register(cid)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=register_conn, args=("conn-a",))
    t2 = threading.Thread(target=register_conn, args=("conn-b",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors
    assert mgr.active_count == 2


# ---------------------------------------------------------------------------
# EventStreamQueue tests
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine on a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_event_queue_put_threadsafe_delivers_via_loop():
    """put_threadsafe schedules put_nowait on the asyncio Queue via the given loop."""

    async def _check():
        esq = EventStreamQueue()
        loop = asyncio.get_event_loop()
        event = _FakeEvent(event_type="ProviderStarted")
        esq.put_threadsafe(event, loop)
        # Yield to let call_soon_threadsafe callback execute.
        await asyncio.sleep(0)
        result = esq.queue.get_nowait()
        assert result is event

    _run(_check())


def test_event_queue_full_queue_does_not_raise():
    """put_threadsafe into a full queue drops oldest and keeps newest event."""

    async def _check():
        esq = EventStreamQueue()
        # Override internal queue with maxsize=1 to trigger QueueFull easily.
        esq._queue = asyncio.Queue(maxsize=1)
        loop = asyncio.get_event_loop()
        event1 = _FakeEvent(event_type="A")
        event2 = _FakeEvent(event_type="B")
        esq.put_threadsafe(event1, loop)
        await asyncio.sleep(0)  # let first event land
        esq.put_threadsafe(event2, loop)  # should evict event1
        await asyncio.sleep(0)
        # Only event2 remains in queue; event1 was dropped.
        assert esq.queue.qsize() == 1
        assert esq.queue.get_nowait() is event2

    _run(_check())


def test_event_queue_full_queue_invokes_drop_callback():
    """Drop callback receives evicted and incoming events on overflow."""

    async def _check():
        drops = []
        esq = EventStreamQueue(maxsize=1, on_drop=lambda dropped, new: drops.append((dropped, new)))
        loop = asyncio.get_event_loop()
        event1 = _FakeEvent(event_type="A")
        event2 = _FakeEvent(event_type="B")

        esq.put_threadsafe(event1, loop)
        await asyncio.sleep(0)
        esq.put_threadsafe(event2, loop)
        await asyncio.sleep(0)

        assert drops == [(event1, event2)]

    _run(_check())


def test_event_queue_three_events_all_retrievable():
    """put_threadsafe with 3 events into maxsize=100 queue -- all 3 retrievable."""

    async def _check():
        esq = EventStreamQueue()
        loop = asyncio.get_event_loop()
        events = [_FakeEvent(event_type=f"E{i}") for i in range(3)]
        for e in events:
            esq.put_threadsafe(e, loop)
        await asyncio.sleep(0)
        retrieved = []
        for _ in range(3):
            retrieved.append(esq.queue.get_nowait())
        assert retrieved == events

    _run(_check())


# ---------------------------------------------------------------------------
# parse_subscription_filters tests
# ---------------------------------------------------------------------------


def test_parse_filters_with_both_fields():
    """Full filter dict is parsed correctly."""
    result = parse_subscription_filters({"event_types": ["ProviderStarted"], "provider_ids": ["math"]})
    assert result == {"event_types": ["ProviderStarted"], "provider_ids": ["math"]}


def test_parse_filters_empty_dict_returns_empty():
    """Empty input dict returns empty filter dict."""
    assert parse_subscription_filters({}) == {}


def test_parse_filters_only_event_types():
    """Only event_types key is parsed; provider_ids absent from result."""
    result = parse_subscription_filters({"event_types": ["X"]})
    assert result == {"event_types": ["X"]}
    assert "provider_ids" not in result


def test_parse_filters_none_input_returns_empty():
    """None input returns empty filter dict."""
    assert parse_subscription_filters(None) == {}


# ---------------------------------------------------------------------------
# matches_filters tests
# ---------------------------------------------------------------------------


def test_matches_filters_no_filters_returns_true():
    """Empty filters -- all events pass."""
    event = _FakeEvent(event_type="ProviderStarted")
    assert matches_filters(event, {}) is True


def test_matches_filters_event_type_match():
    """Event type in filter list -- passes."""
    event = _FakeEvent(event_type="ProviderStarted")
    assert matches_filters(event, {"event_types": ["ProviderStarted"]}) is True


def test_matches_filters_event_type_mismatch():
    """Event type not in filter list -- filtered out."""
    event = _FakeEvent(event_type="HealthCheckPassed")
    assert matches_filters(event, {"event_types": ["ProviderStarted"]}) is False


def test_matches_filters_provider_id_match():
    """provider_id in filter list -- passes."""
    event = _FakeEvent(event_type="E", provider_id="math")
    assert matches_filters(event, {"provider_ids": ["math"]}) is True


def test_matches_filters_provider_id_mismatch():
    """provider_id not in filter list -- filtered out."""
    event = _FakeEvent(event_type="E", provider_id="other")
    assert matches_filters(event, {"provider_ids": ["math"]}) is False


def test_matches_filters_both_match():
    """Both event_type and provider_id match -- passes."""
    event = _FakeEvent(event_type="ProviderStarted", provider_id="math")
    assert matches_filters(event, {"event_types": ["ProviderStarted"], "provider_ids": ["math"]}) is True


def test_matches_filters_event_type_match_provider_id_mismatch():
    """event_type matches but provider_id does not -- filtered out (AND semantics)."""
    event = _FakeEvent(event_type="ProviderStarted", provider_id="other")
    assert matches_filters(event, {"event_types": ["ProviderStarted"], "provider_ids": ["math"]}) is False


# ---------------------------------------------------------------------------
# Singleton smoke test
# ---------------------------------------------------------------------------


def test_connection_manager_singleton_is_importable():
    """The module-level connection_manager singleton is a WebSocketConnectionManager."""
    assert isinstance(connection_manager, WebSocketConnectionManager)
