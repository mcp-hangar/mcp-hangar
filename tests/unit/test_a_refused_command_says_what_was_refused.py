"""A dispatch leaves a span naming what was dispatched, refused or not (#1297).

`CommandBus.send` opened `handler.{Command}` inside its innermost step, so a
command the rate-limit middleware refused never reached it: the trace held one
`rate_limit.check` span with `allowed=false` and nothing saying which command
had been refused, by whom, or that a dispatch had happened at all.
`QueryBus.execute` opened no span, so every management read was a gap between
the request arriving and the response leaving.

The tests use the real `RateLimitMiddleware` -- the class the bootstrap
registers -- on a real bus, and drive a real management tool through a real
`QueryBus` with its real handlers. A mock bus would prove the attribute names
and nothing about whether the span exists where the refusal happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from mcp_hangar.domain.exceptions import RateLimitExceeded
from mcp_hangar.infrastructure.command_bus import CommandBus, RateLimitMiddleware
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.observability.conventions import Dispatch

pytestmark = pytest.mark.otel_sdk

ERROR_TYPE = "error.type"


@dataclass
class _Command:
    value: int = 1


@dataclass
class _Query:
    value: int = 1


class _Handler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def handle(self, message: Any) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return "done"


@dataclass
class _Spent:
    """What a shared limiter returns when the bucket is empty."""

    allowed: bool = False
    limit: int = 1
    retry_after: float = 1.0


class _AlwaysRefuses:
    """A shared limiter with nothing left, shaped as `_SharedLimiter` declares."""

    def consume(self, key: str) -> _Spent:
        return _Spent()


@pytest.fixture
def spans(monkeypatch):
    """A real provider whose spans land in memory, for both buses."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    monkeypatch.setattr("mcp_hangar.infrastructure.command_bus.get_tracer", lambda *_a, **_k: tracer)
    monkeypatch.setattr("mcp_hangar.infrastructure.query_bus.get_tracer", lambda *_a, **_k: tracer)
    return exporter, provider


def _by_name(exporter, name: str) -> list:
    return [span for span in exporter.get_finished_spans() if span.name == name]


def test_a_dispatched_command_names_itself_and_its_outcome(spans) -> None:
    exporter, _ = spans
    bus = CommandBus()
    bus.register(_Command, _Handler())

    assert bus.send(_Command()) == "done"

    [dispatch] = _by_name(exporter, "dispatch._Command")
    assert dispatch.attributes[Dispatch.OPERATION] == "_Command"
    assert dispatch.attributes[Dispatch.OUTCOME] == Dispatch.SUCCESS


def test_the_handler_span_is_inside_the_dispatch_span(spans) -> None:
    """The dispatch has to cover the handler, or it measures the wrong thing."""
    exporter, _ = spans
    bus = CommandBus()
    bus.register(_Command, _Handler())

    bus.send(_Command())

    [dispatch] = _by_name(exporter, "dispatch._Command")
    [handler] = _by_name(exporter, "handler._Command")
    assert handler.parent is not None
    assert handler.parent.span_id == dispatch.context.span_id


def test_a_rate_limited_command_leaves_a_span_saying_what_was_refused(spans) -> None:
    """The failure this issue is about, with the middleware the bootstrap registers.

    Before, this produced one `rate_limit.check` with `allowed=false` and
    nothing naming the command: a refusal whose subject was missing.
    """
    exporter, _ = spans
    handler = _Handler()
    bus = CommandBus()
    bus.register(_Command, handler)
    bus.add_middleware(RateLimitMiddleware(_AlwaysRefuses()))

    with pytest.raises(RateLimitExceeded):
        bus.send(_Command())

    assert handler.calls == 0, "the handler must not have run"

    [dispatch] = _by_name(exporter, "dispatch._Command")
    assert dispatch.attributes[Dispatch.OPERATION] == "_Command"
    assert dispatch.attributes[Dispatch.OUTCOME] == Dispatch.REJECTED
    assert dispatch.attributes[ERROR_TYPE] == "RateLimitExceeded"

    # The middleware's own span keeps its name, its attributes and its place --
    # now under something that says what it refused.
    checks = _by_name(exporter, "rate_limit.check")
    assert checks, "the middleware's span must still be emitted"
    assert all(check.parent.span_id == dispatch.context.span_id for check in checks)


def test_a_refusal_is_not_an_error(spans) -> None:
    """`rejected` and `error` are different answers to "what happened".

    A rate limit answered the question it exists to answer; a handler that threw
    did not. ADR-029 s5 keeps them apart, and an operator counting failures
    should not be counting refusals.
    """
    exporter, _ = spans
    bus = CommandBus()
    bus.register(_Command, _Handler(error=RuntimeError("handler broke")))

    with pytest.raises(RuntimeError):
        bus.send(_Command())

    [dispatch] = _by_name(exporter, "dispatch._Command")
    assert dispatch.attributes[Dispatch.OUTCOME] == Dispatch.ERROR
    assert dispatch.attributes[ERROR_TYPE] == "RuntimeError"


def test_a_query_dispatch_is_no_longer_invisible(spans) -> None:
    exporter, _ = spans
    bus = QueryBus()
    bus.register(_Query, _Handler())

    assert bus.execute(_Query()) == "done"

    [dispatch] = _by_name(exporter, "dispatch._Query")
    assert dispatch.attributes[Dispatch.OPERATION] == "_Query"
    assert dispatch.attributes[Dispatch.OUTCOME] == Dispatch.SUCCESS


def test_the_dispatch_span_nests_and_adds_no_new_root(spans) -> None:
    """Every entry point already has a span; this must join it, not start a trace."""
    exporter, provider = spans
    bus = CommandBus()
    bus.register(_Command, _Handler())

    with provider.get_tracer("test").start_as_current_span("entry_point") as parent:
        bus.send(_Command())
        parent_id = parent.get_span_context().span_id

    [dispatch] = _by_name(exporter, "dispatch._Command")
    assert dispatch.parent is not None, "the dispatch span started its own trace"
    assert dispatch.parent.span_id == parent_id


def test_a_real_management_tool_produces_a_dispatch_span(spans) -> None:
    """`hangar_list` through a real QueryBus with the real handlers registered.

    The entry point matters: management tools call the buses directly rather
    than through the executor, and that path had no span of its own at all.
    """
    exporter, _ = spans

    from mcp_hangar.application.queries import register_all_handlers
    from mcp_hangar.application.queries.queries import ListMcpServersQuery
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.domain.repository import InMemoryMcpServerRepository
    from mcp_hangar.infrastructure.persistence import InMemoryEventStore
    from mcp_hangar.server.context import init_context, reset_context

    runtime = create_runtime(repository=InMemoryMcpServerRepository())
    init_context(runtime)
    try:
        query_bus = QueryBus()
        register_all_handlers(query_bus, runtime.repository, event_store=InMemoryEventStore())
        query_bus.execute(ListMcpServersQuery())
    finally:
        reset_context()

    [dispatch] = _by_name(exporter, "dispatch.ListMcpServersQuery")
    assert dispatch.attributes[Dispatch.OUTCOME] == Dispatch.SUCCESS
