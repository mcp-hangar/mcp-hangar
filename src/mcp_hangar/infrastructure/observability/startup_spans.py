"""Turns startup role reports into spans (#1279).

Ten concurrent calls to one cold server produce one launch and nine waits. Until
this adapter existed a trace could not tell them apart: every caller got the
same `mcp_server.cold_start` span, only the leader's happened to contain the
start command, and the nine waiters looked like nine slow calls with no
explanation on them.

It implements both reporting ports, because the same question is asked twice on
the way in and the answer has to read the same either way:

- `SingleFlightObserver` -- the batch executor's cold-start gate, which
  deduplicates callers that arrive while the server is still `cold`;
- `StartupObserver` -- the aggregate's own `ensure_ready`, which catches the
  callers that arrive after the leader has moved it to `INITIALIZING` and so
  never reach single flight at all.

**Links, not a shared parent.** ADR-029 s2: many waiters share one cause, and a
shared cause is a link. Making the leader's span the parent of nine other
requests' work would file nine unrelated traces under one call and make the
leader's duration look like the sum of everyone's patience.

The origin travels as a W3C `traceparent` string, never as an SDK object, so
`SingleFlight` stores data it does not have to understand (ADR-029 s8). A
waiter that arrives before the leader publishes one gets `None` and is recorded
with no link, because the alternative is inventing a cause.

**Both mechanisms link to the same span (#1583).** Most followers of a burst
miss single flight -- its window closes the moment the leader moves the server
to `INITIALIZING` -- and wait on the aggregate's event instead. So `starting`
publishes an origin too, per server, for `waiting_for_start` to link to: the
`mcp_server.cold_start` span single flight handed its own waiters when the same
caller led there, else the span the start runs in. The entry lives only while
that start runs.

No lock guards it. Each access is one dict operation, atomic on its own, and the
observer is entered outside every aggregate lock, so a lock here would sit
outside the hierarchy for no gain. The one race it leaves -- a start ending
while the next one begins -- can only drop the next start's entry, which means
a missing link, never a wrong one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ...logging_config import get_logger
from ...observability.conventions import McpServer
from ...observability.tracing import extract_trace_context, get_tracer, inject_trace_context

logger = get_logger(__name__)

#: The role a caller played in a shared startup.
ROLE = "hangar.startup.role"
#: Which of the two waiting mechanisms the caller waited in, so a trace can say
#: whether single flight or the aggregate's event held it.
MECHANISM = "hangar.startup.mechanism"

_LEADER = "leader"
_WAITER = "waiter"

#: What `leading` published on this caller: (server id, traceparent). The same
#: caller then reaches `starting` inside the single-flight work, which reads it.
_LED: ContextVar[tuple[str, str] | None] = ContextVar("hangar_startup_led", default=None)


def _trace_id(origin: str) -> str | None:
    """The trace id field of a traceparent, or None when it has none."""
    fields = origin.split("-")
    return fields[1] if len(fields) == 4 else None


class StartupSpanAdapter:
    """Maps startup role reports to spans, for both waiting mechanisms."""

    def __init__(self, tracer: Any | None = None) -> None:
        self._tracer = tracer or get_tracer(__name__)
        # The origin of each start in progress, by server id (#1583).
        self._starts: dict[str, str] = {}

    # -- SingleFlightObserver -------------------------------------------------

    def leading(self, key: str) -> str | None:
        """Publish the leader's current span as the origin waiters may link to.

        Called on the executing caller, outside the lock, before the start runs.
        The leader's span is already open -- the executor's
        `mcp_server.cold_start` -- so this reads the ambient context rather than
        opening a second one: a start that appeared twice in a trace would be
        the double-counting this epic exists to remove.
        """
        origin = self._ambient_origin()
        _LED.set((key, origin) if origin else None)
        return origin

    @contextmanager
    def waiting(self, key: str, origin: str | None) -> Iterator[None]:
        """Span a caller's wait in single flight, linked to the leader's start."""
        with self._wait_span(key, origin, mechanism="single_flight"):
            yield

    # -- StartupObserver ------------------------------------------------------

    @contextmanager
    def starting(self, mcp_server_id: str) -> Iterator[None]:
        """Mark the ambient span as the one doing the work.

        An attribute rather than a span: the work already has one, and the
        caller that performs a start is the interesting fact about the span it
        is already in.

        It also publishes the start's origin for `waiting_for_start`, and
        withdraws it when the start ends, so a later start's waiters never link
        to this one.
        """
        self._mark(ROLE, _LEADER)
        origin = self._start_origin(mcp_server_id)
        if origin:
            self._starts[mcp_server_id] = origin
        try:
            yield
        finally:
            if origin and self._starts.get(mcp_server_id) is origin:
                self._starts.pop(mcp_server_id, None)

    @contextmanager
    def waiting_for_start(self, mcp_server_id: str) -> Iterator[None]:
        """Span a caller's wait on the aggregate's readiness event.

        These are the callers that arrived after the leader moved the server to
        `INITIALIZING`, so single flight never saw them. Their wait had no span
        at all before this. It links to the origin `starting` published, and to
        nothing when no start of this server has published one.
        """
        origin = self._starts.get(mcp_server_id)
        with self._wait_span(mcp_server_id, origin, mechanism="ensure_ready"):
            yield

    def _start_origin(self, server_id: str) -> str | None:
        """The origin of the start this caller performs.

        The single-flight origin when this caller led the single flight for
        this server, in this trace: the executor's `mcp_server.cold_start`, the
        span single flight's own waiters link to. The trace check drops what an
        earlier call on a reused thread left behind. Otherwise the span the
        start runs in.
        """
        led, ambient = _LED.get(), self._ambient_origin()
        _LED.set(None)
        if led and ambient and led[0] == server_id and _trace_id(led[1]) == _trace_id(ambient):
            return led[1]
        return ambient

    # -- shared ---------------------------------------------------------------

    @staticmethod
    def _ambient_origin() -> str | None:
        """The current span as a traceparent, or None when there is none."""
        carrier: dict[str, Any] = {}
        inject_trace_context(carrier)
        origin = carrier.get("traceparent")
        return origin if isinstance(origin, str) else None

    @contextmanager
    def _wait_span(self, server_id: str, origin: str | None, *, mechanism: str) -> Iterator[None]:
        links = self._links(origin)
        with self._tracer.start_as_current_span("mcp_server.startup_wait", links=links) as span:
            span.set_attribute(McpServer.ID, server_id)
            span.set_attribute(ROLE, _WAITER)
            span.set_attribute(MECHANISM, mechanism)
            yield

    def _links(self, origin: str | None) -> list[Any]:
        """One link to the origin, or none at all when it is absent or malformed."""
        if not origin:
            return []
        try:
            from opentelemetry import trace

            context = extract_trace_context({"traceparent": origin})
            if context is None:
                return []
            span_context = trace.get_current_span(context).get_span_context()
            return [trace.Link(span_context)] if span_context.is_valid else []
        except Exception:  # noqa: BLE001 -- fault barrier: a bad carrier must not break a wait
            logger.debug("startup_wait_link_failed")
            return []

    def _mark(self, key: str, value: str) -> None:
        """Set an attribute on the ambient span, if there is a real one."""
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span.get_span_context().is_valid:
                span.set_attribute(key, value)
        except Exception:  # noqa: BLE001 -- fault barrier: observation must not break a start
            logger.debug("startup_role_mark_failed", key=key)


class _AggregateView:
    """The `StartupObserver` face of the adapter.

    The aggregate's port names its waiting hook `waiting`, and single flight's
    port gives that name a second parameter. Rather than overload one method
    with an optional argument that means different things to its two callers,
    the adapter exposes the aggregate's shape here. One object still owns the
    behaviour, so the two mechanisms cannot drift apart.
    """

    def __init__(self, adapter: StartupSpanAdapter) -> None:
        self._adapter = adapter

    def starting(self, mcp_server_id: str) -> Any:
        return self._adapter.starting(mcp_server_id)

    def waiting(self, mcp_server_id: str) -> Any:
        return self._adapter.waiting_for_start(mcp_server_id)


def aggregate_observer(adapter: StartupSpanAdapter) -> _AggregateView:
    """The adapter, shaped for `domain.contracts.startup_observer.StartupObserver`."""
    return _AggregateView(adapter)
