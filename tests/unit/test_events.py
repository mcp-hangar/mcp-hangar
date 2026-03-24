"""Tests for domain events and event bus."""

from mcp_hangar.application.event_handlers import LoggingEventHandler, MetricsEventHandler
from mcp_hangar.domain.events import ProviderStarted, ProviderStopped, ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import EventBus, get_event_bus, reset_event_bus


def test_event_to_dict():
    """Test that events can be serialized to dict."""
    event = ProviderStarted(
        provider_id="test_provider",
        mode="subprocess",
        tools_count=5,
        startup_duration_ms=123.45,
    )

    event_dict = event.to_dict()

    assert event_dict["event_type"] == "ProviderStarted"
    assert event_dict["provider_id"] == "test_provider"
    assert event_dict["mode"] == "subprocess"
    assert event_dict["tools_count"] == 5
    assert event_dict["startup_duration_ms"] == 123.45
    assert "event_id" in event_dict
    assert "occurred_at" in event_dict


def test_event_bus_subscribe_and_publish():
    """Test basic subscribe and publish functionality."""
    bus = EventBus()
    received_events = []

    def handler(event):
        received_events.append(event)

    # Subscribe to specific event type
    bus.subscribe(ProviderStarted, handler)

    # Publish event
    event = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    bus.publish(event)

    # Verify handler was called
    assert len(received_events) == 1
    assert received_events[0] == event


def test_event_bus_multiple_subscribers():
    """Test that multiple subscribers receive events."""
    bus = EventBus()
    handler1_events = []
    handler2_events = []

    def handler1(event):
        handler1_events.append(event)

    def handler2(event):
        handler2_events.append(event)

    # Both subscribe to same event type
    bus.subscribe(ProviderStarted, handler1)
    bus.subscribe(ProviderStarted, handler2)

    # Publish event
    event = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    bus.publish(event)

    # Both handlers should have received it
    assert len(handler1_events) == 1
    assert len(handler2_events) == 1


def test_event_bus_subscribe_to_all():
    """Test subscribing to all event types."""
    bus = EventBus()
    received_events = []

    def handler(event):
        received_events.append(event)

    # Subscribe to all events
    bus.subscribe_to_all(handler)

    # Publish different event types
    event1 = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    event2 = ProviderStopped(provider_id="test", reason="shutdown")

    bus.publish(event1)
    bus.publish(event2)

    # Handler should have received both
    assert len(received_events) == 2
    assert isinstance(received_events[0], ProviderStarted)
    assert isinstance(received_events[1], ProviderStopped)


def test_event_bus_error_handling():
    """Test that errors in handlers don't break the bus."""
    bus = EventBus()
    handler1_called = []
    handler2_called = []

    def failing_handler(event):
        handler1_called.append(True)
        raise Exception("Handler failed!")

    def working_handler(event):
        handler2_called.append(True)

    # Subscribe both handlers
    bus.subscribe(ProviderStarted, failing_handler)
    bus.subscribe(ProviderStarted, working_handler)

    # Publish event
    event = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    bus.publish(event)

    # Both should have been called despite the error
    assert len(handler1_called) == 1
    assert len(handler2_called) == 1


def test_logging_event_handler():
    """Test that logging handler processes events."""
    handler = LoggingEventHandler()

    # Should not raise errors
    event = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    handler.handle(event)

    event = ToolInvocationCompleted(provider_id="test", tool_name="add", correlation_id="abc123", duration_ms=50.0)
    handler.handle(event)


def test_metrics_event_handler():
    """Test that metrics handler collects metrics."""
    handler = MetricsEventHandler()

    # Simulate tool invocation completion
    event = ToolInvocationCompleted(
        provider_id="test_provider",
        tool_name="add",
        correlation_id="abc123",
        duration_ms=50.0,
        result_size_bytes=100,
    )
    handler.handle(event)

    # Verify metrics were collected
    metrics = handler.get_metrics("test_provider")
    assert metrics is not None
    assert metrics.total_invocations == 1
    assert metrics.successful_invocations == 1
    assert metrics.average_latency_ms == 50.0


def test_metrics_handler_multiple_invocations():
    """Test metrics aggregation over multiple invocations."""
    handler = MetricsEventHandler()

    # Simulate multiple invocations
    for i in range(10):
        event = ToolInvocationCompleted(
            provider_id="test_provider",
            tool_name="add",
            correlation_id=f"corr_{i}",
            duration_ms=float(i * 10),
            result_size_bytes=100,
        )
        handler.handle(event)

    metrics = handler.get_metrics("test_provider")
    assert metrics.total_invocations == 10
    assert metrics.successful_invocations == 10
    assert metrics.failed_invocations == 0
    assert metrics.average_latency_ms == 45.0  # Average of 0, 10, 20, ..., 90


def test_global_event_bus_singleton():
    """Test that global event bus is a singleton."""
    reset_event_bus()

    bus1 = get_event_bus()
    bus2 = get_event_bus()

    assert bus1 is bus2


def test_event_bus_unsubscribe():
    """Test unsubscribing from events."""
    bus = EventBus()
    received_events = []

    def handler(event):
        received_events.append(event)

    # Subscribe and publish
    bus.subscribe(ProviderStarted, handler)
    event1 = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    bus.publish(event1)

    assert len(received_events) == 1

    # Unsubscribe and publish again
    bus.unsubscribe(ProviderStarted, handler)
    event2 = ProviderStarted(provider_id="test2", mode="subprocess", tools_count=5, startup_duration_ms=200.0)
    bus.publish(event2)

    # Should still be 1 (handler not called second time)
    assert len(received_events) == 1


def test_event_bus_clear():
    """Test clearing all subscriptions."""
    bus = EventBus()
    received_events = []

    def handler(event):
        received_events.append(event)

    bus.subscribe(ProviderStarted, handler)
    bus.clear()

    # Publish after clear
    event = ProviderStarted(provider_id="test", mode="subprocess", tools_count=3, startup_duration_ms=100.0)
    bus.publish(event)

    # Handler should not have been called
    assert len(received_events) == 0
