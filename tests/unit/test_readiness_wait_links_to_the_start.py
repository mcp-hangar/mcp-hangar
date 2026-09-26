"""A caller waiting on the aggregate's readiness event links to the start it waits on (#1583).

Single flight hands its waiters the leader's `mcp_server.cold_start` as an
origin, but it only catches callers that pass the cold check before the leader
moves the server to INITIALIZING. Every later caller waits on the aggregate's
readiness event, and that wait had no link at all -- in a real burst, that was
every follower.

These tests drive a real `McpServer`, real threads and a real OpenTelemetry SDK.
Only `_start` is replaced, by one that holds the start open until every waiter
is inside its wait, so each waiter deterministically takes the readiness-event
path and never single flight.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.domain.contracts.startup_observer import set_startup_observer
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.observability.startup_spans import (
    MECHANISM,
    StartupSpanAdapter,
    aggregate_observer,
)
from mcp_hangar.infrastructure.single_flight import SingleFlight

pytestmark = pytest.mark.otel_sdk

WAITERS = 3


class _Counted:
    """The aggregate's observer, counting the waiters that are inside their wait."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.entered = threading.Semaphore(0)

    def starting(self, mcp_server_id: str) -> Any:
        return self._inner.starting(mcp_server_id)

    @contextmanager
    def waiting(self, mcp_server_id: str) -> Iterator[None]:
        with self._inner.waiting(mcp_server_id):
            self.entered.release()
            yield


@pytest.fixture
def otel() -> Iterator[tuple[Any, Any]]:
    """A local provider and exporter, never registered globally."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # inject_trace_context is gated on Hangar's own init having run.
    with patch("mcp_hangar.observability.tracing._initialized", True):
        yield provider.get_tracer("test"), exporter


@pytest.fixture
def world(otel, monkeypatch) -> Iterator[Any]:
    tracer, exporter = otel
    observer = _Counted(aggregate_observer(StartupSpanAdapter(tracer)))
    set_startup_observer(observer)
    server = McpServer(mcp_server_id="math", mode="subprocess", command=["true"])
    # Its own adapter instance, as the batch executor builds it.
    single_flight = SingleFlight(cache_results=False, observer=StartupSpanAdapter(tracer))

    def blocking_start() -> None:
        def waiter() -> None:
            with tracer.start_as_current_span("batch.call.add"):
                server.ensure_ready()

        threads = [threading.Thread(target=waiter) for _ in range(WAITERS)]
        for thread in threads:
            thread.start()
        for _ in threads:
            assert observer.entered.acquire(timeout=5), "a waiter never reached its wait"
        with server._lock:
            server._state = McpServerState.READY
            server._ready_event.set()
        for thread in threads:
            thread.join(timeout=5)

    monkeypatch.setattr(server, "_start", blocking_start)

    class World:
        pass

    w = World()
    w.tracer, w.exporter, w.server, w.single_flight = tracer, exporter, server, single_flight

    def lead_via_single_flight() -> None:
        """The executor's shape: `mcp_server.cold_start` > single flight > command handler."""
        with tracer.start_as_current_span("mcp_server.cold_start"):
            single_flight.do("math", lead_in_handler)

    def lead_in_handler() -> None:
        with tracer.start_as_current_span("handler.StartMcpServerCommand"):
            server.ensure_ready()

    def cool_down() -> None:
        with server._lock:
            server._state = McpServerState.COLD
        exporter.clear()

    w.lead_via_single_flight, w.lead_in_handler, w.cool_down = lead_via_single_flight, lead_in_handler, cool_down
    yield w
    set_startup_observer(None)


def _waits(exporter: Any) -> list[Any]:
    waits = [s for s in exporter.get_finished_spans() if s.name == "mcp_server.startup_wait"]
    assert len(waits) == WAITERS, [s.name for s in exporter.get_finished_spans()]
    assert {w.attributes[MECHANISM] for w in waits} == {"ensure_ready"}, "a wait took single flight"
    return waits


def _span(exporter: Any, name: str) -> Any:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == name]
    return span


def _linked(wait: Any) -> list[tuple[int, int]]:
    return [(link.context.trace_id, link.context.span_id) for link in wait.links]


def test_a_readiness_event_wait_links_to_the_leaders_cold_start(world) -> None:
    world.lead_via_single_flight()

    cold_start = _span(world.exporter, "mcp_server.cold_start")
    by_id = {s.context.span_id: s for s in world.exporter.get_finished_spans()}
    for wait in _waits(world.exporter):
        assert _linked(wait) == [(cold_start.context.trace_id, cold_start.context.span_id)]
        parent = by_id[wait.parent.span_id]
        assert parent.name == "batch.call.add" and parent.context.trace_id == wait.context.trace_id
        assert wait.context.trace_id != cold_start.context.trace_id, "a link, never a shared parent"


def test_a_wait_on_a_start_with_no_known_context_has_no_link(world) -> None:
    world.server.ensure_ready()  # the leader runs in no span, so it publishes nothing

    assert all(_linked(wait) == [] for wait in _waits(world.exporter))


def test_a_later_start_never_links_to_an_earlier_one(world) -> None:
    world.lead_via_single_flight()
    world.cool_down()

    world.server.ensure_ready()  # a second start, with no context of its own

    assert all(_linked(wait) == [] for wait in _waits(world.exporter))


def test_a_single_flight_origin_left_on_the_thread_is_not_reused(world) -> None:
    """A caller led single flight but found the server ready, so it never started it."""
    with world.server._lock:
        world.server._state = McpServerState.READY
    world.server._client = type("Alive", (), {"is_alive": lambda self: True})()
    world.lead_via_single_flight()
    world.cool_down()

    world.lead_in_handler()  # a start on the same thread, in another trace, outside single flight

    handler = _span(world.exporter, "handler.StartMcpServerCommand")
    for wait in _waits(world.exporter):
        assert _linked(wait) == [(handler.context.trace_id, handler.context.span_id)]
