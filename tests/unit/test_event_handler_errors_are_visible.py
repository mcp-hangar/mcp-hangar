"""A handler that throws is counted, not just logged.

The fault barrier around handler dispatch is right: one bad handler must not
stop the others, and it never has. What was missing is any trace of the
swallowed failure beyond a log line -- no metric, no alert, nothing a dashboard
could show. An audit handler throwing on every single event looked exactly like
one that was working, and audit is the surface this project sells.

`EventBus.on_error` existed for precisely this and was registered by nobody, so
the loop that called those handlers ran zero times on every failure. It is
replaced by the counter the rest of the system already uses.
"""

from __future__ import annotations

from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.metrics import ERRORS_TOTAL


def _event() -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id="math", tool_name="add", duration_ms=1.0)


def _error_count(error_type: str) -> float:
    for sample in ERRORS_TOTAL.collect():
        if sample.labels == {"component": "event_handler", "error_type": error_type}:
            return float(sample.value)
    return 0.0


class TestAFailingHandlerIsCounted:
    def test_the_error_counter_moves(self) -> None:
        bus = EventBus()

        def explodes(_event: object) -> None:
            raise RuntimeError("handler is broken")

        bus.subscribe_to_all(explodes)
        before = _error_count("RuntimeError")

        bus.publish(_event())

        assert _error_count("RuntimeError") == before + 1

    def test_the_other_handlers_still_run(self) -> None:
        # The barrier's actual job, pinned so the counter does not arrive at the
        # cost of the guarantee it was protecting.
        bus = EventBus()
        seen: list = []

        def explodes(_event: object) -> None:
            raise ValueError("first handler is broken")

        bus.subscribe_to_all(explodes)
        bus.subscribe_to_all(seen.append)

        bus.publish(_event())

        assert len(seen) == 1

    def test_publish_does_not_raise(self) -> None:
        bus = EventBus()
        bus.subscribe_to_all(lambda _e: (_ for _ in ()).throw(KeyError("boom")))
        bus.publish(_event())  # must not raise

    def test_each_failure_is_counted_separately(self) -> None:
        bus = EventBus()
        bus.subscribe_to_all(lambda _e: (_ for _ in ()).throw(TimeoutError("slow")))
        before = _error_count("TimeoutError")

        bus.publish(_event())
        bus.publish(_event())

        assert _error_count("TimeoutError") == before + 2


class TestTheDeadHookIsGone:
    def test_on_error_is_no_longer_offered(self) -> None:
        # It was registered by nobody, so the loop that called those handlers
        # ran zero times on every failure -- dead code in the one path that only
        # runs when something is already wrong.
        assert not hasattr(EventBus(), "on_error")

    def test_clear_still_resets_the_bus(self) -> None:
        bus = EventBus()
        seen: list = []
        bus.subscribe_to_all(seen.append)

        bus.clear()
        bus.publish(_event())

        assert seen == []
