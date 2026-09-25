"""Event delivery and persistence say which handler and which append (#1280).

`event.publish.<Type>` was one span over every handler, and a failing handler
added only an `exception` event: which handler, and of which kind, was not on
the trace. On an append failure the unpersisted delivery ran inside the
`event_store.append` span, so the append's duration included every handler and
the publish span became its child, while on success the two were siblings.

These tests read spans from a real SDK exporter behind `_TextFreeTracer`, the
wrapper `get_tracer()` returns in production, through a real `EventBus` backed by
the SQLite event store and checkpoint. Delivery is synchronous on the caller's
thread, so this is the path production takes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.trace import StatusCode

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.event_store import ConcurrencyError
from mcp_hangar.domain.events import DomainEvent, ToolInvocationCompleted
from mcp_hangar.infrastructure import event_bus as event_bus_module
from mcp_hangar.infrastructure.event_bus import APPEND_AT_END, EventBus
from mcp_hangar.infrastructure.persistence import SqliteDispatchCheckpoint
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

SERVER = "delivery-probe"
STREAM = stream_id_for(MCP_SERVER, SERVER)
#: A value only the event's payload carries; it must never reach a span.
PAYLOAD_MARKER = "payload-marker-tool"

_HANDLED_KEYS = {
    "hangar.event.handler.name",
    "hangar.event.handler.kind",
    "hangar.event.handler.outcome",
    "error.type",
}


class HandlerBroke(RuntimeError):
    pass


class Projection:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def handle(self, event: DomainEvent) -> None:
        self.seen.append("projection")


class FailingEffect:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def handle(self, event: DomainEvent) -> None:
        self.seen.append("failing_effect")
        raise HandlerBroke("handler text that must not reach a span")


class AuditEffect:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def handle(self, event: DomainEvent) -> None:
        self.seen.append("audit_effect")


class BrokenStore(SQLiteEventStore):
    """The SQLite store whose append fails for an infrastructure reason."""

    def append_at_end(self, stream_id: str, events: list[DomainEvent]) -> int:
        raise sqlite3.OperationalError("database is locked")


@pytest.fixture()
def exporter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A local TracerProvider + InMemorySpanExporter, never registered globally."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = _TextFreeTracer(provider.get_tracer("test-1280"))
    monkeypatch.setattr(event_bus_module, "get_tracer", lambda name: tracer)
    exporter.tracer = tracer
    yield exporter
    exporter.clear()


def _wire(tmp_path: Path, store_class: type[SQLiteEventStore] = SQLiteEventStore) -> tuple[EventBus, list[str]]:
    db = tmp_path / "events.db"
    bus = EventBus(event_store=store_class(db), dispatch_checkpoint=SqliteDispatchCheckpoint(db))
    seen: list[str] = []
    bus.subscribe(ToolInvocationCompleted, Projection(seen).handle, kind=HandlerKind.PROJECTION)
    bus.subscribe(ToolInvocationCompleted, FailingEffect(seen).handle, kind=HandlerKind.EFFECT)
    bus.subscribe_to_all(AuditEffect(seen).handle, kind=HandlerKind.EFFECT)
    return bus, seen


def _event() -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id=SERVER, tool_name=PAYLOAD_MARKER, duration_ms=1.0)


def _spans(exporter: Any, name: str) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _handled(span: Any) -> list[dict[str, Any]]:
    return [dict(e.attributes) for e in span.events if e.name == "hangar.event.handled"]


def _all_values(span: Any) -> list[Any]:
    values = list(span.attributes.values())
    for event in span.events:
        values.extend(event.attributes.values())
    return values


class TestEachHandlerIsNamedOnThePublishSpan:
    def test_a_failing_handler_among_successful_peers_is_identifiable(self, exporter: Any, tmp_path: Path) -> None:
        bus, seen = _wire(tmp_path)
        event = _event()

        bus.publish(event)

        assert seen == ["projection", "failing_effect", "audit_effect"], "handler order and the fault barrier hold"
        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        assert _handled(publish) == [
            {
                "hangar.event.handler.name": "Projection.handle",
                "hangar.event.handler.kind": "projection",
                "hangar.event.handler.outcome": "success",
            },
            {
                "hangar.event.handler.name": "FailingEffect.handle",
                "hangar.event.handler.kind": "effect",
                "hangar.event.handler.outcome": "error",
                "error.type": "HandlerBroke",
            },
            {
                "hangar.event.handler.name": "AuditEffect.handle",
                "hangar.event.handler.kind": "effect",
                "hangar.event.handler.outcome": "success",
            },
        ]
        # A caught handler failure is an operational failure (#1272).
        assert publish.status.status_code is StatusCode.ERROR
        assert publish.attributes["error.type"] == "HandlerBroke"

    def test_publish_records_event_id_producer_and_live_mode(self, exporter: Any, tmp_path: Path) -> None:
        bus, _seen = _wire(tmp_path)
        event = _event()

        bus.publish(event)

        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        assert publish.attributes["hangar.event.id"] == event.event_id
        assert publish.attributes["hangar.event.producer"] == event.produced_by
        assert publish.attributes["hangar.event.delivery_mode"] == "live"
        # Existing attributes are kept.
        assert publish.attributes["event.type"] == "ToolInvocationCompleted"
        assert publish.attributes["event.handlers_count"] == 3

    def test_no_payload_or_error_text_reaches_a_span(self, exporter: Any, tmp_path: Path) -> None:
        bus, _seen = _wire(tmp_path)

        bus.publish(_event())

        for span in exporter.get_finished_spans():
            for value in _all_values(span):
                assert PAYLOAD_MARKER not in str(value), span.name
                assert "must not reach" not in str(value), span.name
        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        for handled in _handled(publish):
            assert set(handled) <= _HANDLED_KEYS

    def test_n_events_emit_n_publish_spans_not_n_times_handlers(self, exporter: Any, tmp_path: Path) -> None:
        bus, _seen = _wire(tmp_path)

        bus.publish_to_stream(STREAM, [_event(), _event(), _event()], APPEND_AT_END)

        names = [s.name for s in exporter.get_finished_spans()]
        assert sorted(names) == ["event.publish.ToolInvocationCompleted"] * 3 + ["event_store.append"]
        for publish in _spans(exporter, "event.publish.ToolInvocationCompleted"):
            assert len(_handled(publish)) == 3


class TestTheAppendIsItsOwnOperation:
    def test_a_successful_append_records_stream_count_versions_and_outcome(self, exporter: Any, tmp_path: Path) -> None:
        bus, _seen = _wire(tmp_path)
        with exporter.tracer.start_as_current_span("caller"):
            assert bus.publish_to_stream(STREAM, [_event(), _event()], APPEND_AT_END) == 1

        [append] = _spans(exporter, "event_store.append")
        assert dict(append.attributes) == {
            "event_store.stream_id": STREAM,
            "event_store.events_count": 2,
            "event_store.expected_version": APPEND_AT_END,
            "event_store.new_version": 1,
            "hangar.event_store.append.outcome": "appended",
        }
        assert append.status.status_code is StatusCode.UNSET
        [caller] = _spans(exporter, "caller")
        for publish in _spans(exporter, "event.publish.ToolInvocationCompleted"):
            assert publish.parent.span_id == caller.context.span_id, "delivery is the append's sibling"

    def test_a_failed_append_ends_before_delivery_and_does_not_parent_it(self, exporter: Any, tmp_path: Path) -> None:
        bus, seen = _wire(tmp_path, BrokenStore)
        with exporter.tracer.start_as_current_span("caller"):
            assert bus.publish_to_stream(STREAM, [_event()], APPEND_AT_END) == APPEND_AT_END

        assert seen == ["projection", "failing_effect", "audit_effect"], "delivery still happens"
        [append] = _spans(exporter, "event_store.append")
        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        [caller] = _spans(exporter, "caller")
        assert append.attributes["hangar.event_store.append.outcome"] == "failed"
        assert append.status.status_code is StatusCode.ERROR
        assert append.attributes["error.type"] == "OperationalError"
        assert "event_store.new_version" not in append.attributes
        assert append.end_time <= publish.start_time, "handler time is not in the append"
        assert publish.parent.span_id == caller.context.span_id
        assert publish.attributes["hangar.event.delivery_mode"] == "live"

    def test_a_version_conflict_is_one_append_raised_and_undelivered(self, exporter: Any, tmp_path: Path) -> None:
        bus, seen = _wire(tmp_path)
        bus.publish_to_stream(STREAM, [_event()], -1)
        exporter.clear()
        seen.clear()

        with pytest.raises(ConcurrencyError):
            bus.publish_to_stream(STREAM, [_event()], -1)

        assert seen == []
        [append] = _spans(exporter, "event_store.append")
        assert append.attributes["hangar.event_store.append.outcome"] == "conflict"
        assert append.status.status_code is StatusCode.ERROR
        assert _spans(exporter, "event.publish.ToolInvocationCompleted") == []


class TestDeliveryModes:
    def test_recovered_delivery_is_marked_and_gets_no_invented_parent(self, exporter: Any, tmp_path: Path) -> None:
        bus, seen = _wire(tmp_path)
        # The crash: the append committed, the delivery loop never ran.
        bus.event_store.append(STREAM, [_event()], expected_version=-1)

        assert bus.dispatch_pending() == 1

        assert seen == ["projection", "failing_effect", "audit_effect"]
        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        assert publish.attributes["hangar.event.delivery_mode"] == "recovered"
        assert publish.parent is None
        assert publish.links == ()

    def test_tailed_delivery_is_marked_and_names_only_projections(self, exporter: Any, tmp_path: Path) -> None:
        bus, seen = _wire(tmp_path)

        bus.deliver_tailed(_event())

        assert seen == ["projection"]
        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        assert publish.attributes["hangar.event.delivery_mode"] == "tailed"
        assert [h["hangar.event.handler.kind"] for h in _handled(publish)] == ["projection"]
        assert publish.status.status_code is StatusCode.UNSET

    def test_a_handler_without_a_qualname_is_named_by_its_type(self, exporter: Any) -> None:
        bus = EventBus()

        class CallableHandler:
            def __call__(self, event: DomainEvent) -> None:
                pass

        handler = CallableHandler()
        bus.subscribe_to_all(handler, kind=HandlerKind.LOCAL_VIEW)
        bus.publish_local(_event())

        [publish] = _spans(exporter, "event.publish.ToolInvocationCompleted")
        [handled] = _handled(publish)
        assert handled["hangar.event.handler.name"].endswith("CallableHandler")
        assert handled["hangar.event.handler.kind"] == "local_view"
