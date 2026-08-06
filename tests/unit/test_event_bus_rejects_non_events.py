"""`publish()` takes one event, and says so.

`CostAttributionEventHandler` called `self._event_bus.publish([cost_event])`.
The bus took it: dispatch keys on `type(event)`, so the `list` matched no
specific handler, and every `subscribe_to_all` handler received the **list
object itself**. Those handlers are chains of `isinstance` checks, so each one
quietly matched nothing and returned. Meanwhile anything subscribed to
`CostReportGenerated` was never called at all.

Nothing failed. The cost event simply never arrived, and the audit, logging and
metrics handlers each got handed a `list` and shrugged.

The unit test covering that handler asserted `len(published_events) == 1` --
agreeing with the bug, because a `MagicMock` bus never has to route anything.
That is the same shape as the API error-envelope regression: a test written
against the call, not against the delivery.

So the bus now refuses. A publish that silently delivers to no one is worse
than a crash.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import CostReportGenerated, McpServerStarted
from mcp_hangar.infrastructure.event_bus import EventBus


def _event() -> CostReportGenerated:
    return CostReportGenerated(tenant_id="t", period_start="", period_end="", total_cost="1.0", currency="USD")


class TestPublishRefusesNonEvents:
    @pytest.mark.parametrize(
        "payload",
        [[], [_event()], (_event(),), {"event": _event()}, "McpServerStarted", None, 42],
        ids=["empty-list", "list-of-one", "tuple", "dict", "str", "none", "int"],
    )
    def test_it_raises(self, payload):
        with pytest.raises(TypeError):
            EventBus().publish(payload)

    def test_the_message_names_what_was_passed_and_what_to_do(self):
        with pytest.raises(TypeError) as excinfo:
            EventBus().publish([_event()])
        message = str(excinfo.value)
        assert "list" in message and "DomainEvent" in message

    def test_no_handler_is_reached(self):
        """The old behaviour: subscribe-to-all handlers got the list itself."""
        bus, seen = EventBus(), []
        bus.subscribe_to_all(lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        with pytest.raises(TypeError):
            bus.publish([_event()])
        assert seen == []


class TestPublishStillWorks:
    def test_a_single_event_is_delivered(self):
        bus, seen = EventBus(), []
        bus.subscribe(CostReportGenerated, lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        event = _event()
        bus.publish(event)
        assert seen == [event]

    def test_subscribe_to_all_still_gets_it(self):
        bus, seen = EventBus(), []
        bus.subscribe_to_all(lambda event: seen.append(type(event).__name__), kind=HandlerKind.EFFECT)
        bus.publish(McpServerStarted(mcp_server_id="p", mode="subprocess", tools_count=0, startup_duration_ms=0.0))
        assert seen == ["McpServerStarted"]
