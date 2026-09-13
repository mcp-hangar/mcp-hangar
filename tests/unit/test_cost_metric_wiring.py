"""An attributed cost must reach the Prometheus scrape output.

That property comes from an observability audit and is unchanged. What changed
is the route: the cost handler used to call `record_cost` itself -- the one
place in the application layer that wrote a metric directly, and an entry in the
import-contract ledger. It now publishes the cost on the bus and
`MetricsEventHandler` records it, like every other counter.

So this test wires the real path rather than asserting on the handler alone.
Asserting "the handler publishes an event" would have been the easy rewrite and
would have quietly dropped the guarantee the audit added: an event published to
a bus nobody subscribed to satisfies that assertion and produces no metric.
"""

from __future__ import annotations

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar import metrics as m
from mcp_hangar.application.event_handlers.cost_handler import CostAttributionEventHandler
from mcp_hangar.domain.contracts.cost import InvocationContext
from mcp_hangar.domain.events import CostReportGenerated, ToolInvocationCompleted
from mcp_hangar.domain.value_objects.cost import CostRecord
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler


class _FixedAttributor:
    """Returns a fixed non-zero cost for any invocation."""

    def compute_cost(self, context: InvocationContext) -> CostRecord:
        return CostRecord(
            mcp_server_id=context.mcp_server_id,
            tool_name=context.tool_name,
            duration_ms=context.duration_ms,
            cost_cents=200,
        )


def _wired_bus() -> EventBus:
    """The production wiring: metrics subscribed to everything."""
    bus = EventBus()
    bus.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
    return bus


def _invocation(mcp_server: str, tool: str) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(
        mcp_server_id=mcp_server,
        tool_name=tool,
        correlation_id="c1",
        duration_ms=12.0,
        result_size_bytes=0,
    )


def test_an_attributed_cost_reaches_the_scrape_output() -> None:
    handler = CostAttributionEventHandler(cost_attributor=_FixedAttributor(), event_bus=_wired_bus())

    handler.handle(_invocation("srv-cost-iso", "get-cost-iso"))

    out = m.get_metrics()
    assert 'mcp_hangar_cost_cents_total{cost_model="duration",mcp_server="srv-cost-iso",tool="get-cost-iso"}' in out
    assert 'mcp_hangar_cost_attributions_total{mcp_server="srv-cost-iso",tool="get-cost-iso"}' in out


def test_the_cost_is_carried_in_cents_not_rederived() -> None:
    """`total_cost` is a string of whole units; hundredths must not come from it.

    Reconstructing cents by parsing that string is a float round-trip, and the
    metric is a counter -- drift accumulates rather than cancelling out.
    """
    published: list[object] = []
    bus = EventBus()
    bus.subscribe(CostReportGenerated, published.append, kind=HandlerKind.EFFECT)
    handler = CostAttributionEventHandler(cost_attributor=_FixedAttributor(), event_bus=bus)

    handler.handle(_invocation("srv-cents", "tool-cents"))

    assert len(published) == 1
    event = published[0]
    assert isinstance(event, CostReportGenerated)
    assert event.cost_cents == 200
    assert event.mcp_server_id == "srv-cents"
    assert event.tool_name == "tool-cents"
    assert event.cost_model


def test_the_application_layer_no_longer_writes_the_metric() -> None:
    """The point of the move: the ledger entry is gone and must stay gone."""
    import pathlib

    source = pathlib.Path("src/mcp_hangar/application/event_handlers/cost_handler.py").read_text(encoding="utf-8")
    assert "record_cost" not in source, (
        "cost_handler writes a Prometheus counter directly again; that is an "
        "application -> metrics edge and the import-contract ledger no longer allows it"
    )


def test_a_v1_row_replays_without_inventing_a_metric() -> None:
    """Stored rows predate the dimensions, so they must not produce a labelled series.

    Passthrough (no upcaster is registered, deliberately) leaves the new fields
    empty. Recording those would put `mcp_server=""` in the scrape output.
    """
    metrics_handler = MetricsEventHandler()
    legacy = CostReportGenerated(tenant_id="t", period_start="", period_end="", total_cost="1.0", currency="USD")

    metrics_handler.handle(legacy)

    assert 'mcp_hangar_cost_cents_total{cost_model="",mcp_server="",tool=""}' not in m.get_metrics()


class TestTheDispatchTableStillMatchesSubclasses:
    """The isinstance chain it replaced matched subclasses for free; a dict does not.

    `MetricsEventHandler.handle` was a 19-branch `isinstance` chain sitting at
    the complexity ceiling with a "split before extending" note, so adding the
    cost branch meant replacing it with a lookup table. `isinstance` matches a
    subclass; `dict[type(event)]` does not -- which is why dispatch walks the
    MRO.

    Nothing pinned that. Probing found it: degrading the walk to an exact-type
    lookup left the ENTIRE unit suite green, while silently dropping metrics for
    four live event types.
    """

    ALIASES_DISPATCHING_VIA_A_BASE = [
        ("ProviderStarted", "McpServerStarted"),
        ("ProviderStopped", "McpServerStopped"),
        ("ProviderStateChanged", "McpServerStateChanged"),
        ("ProviderDegraded", "McpServerDegraded"),
    ]

    def test_a_subclass_event_reaches_its_base_handler(self):
        from mcp_hangar.domain.events import ProviderStarted

        handler = MetricsEventHandler()
        handler.handle(ProviderStarted("srv-alias", "subprocess", 2, 5.0))

        assert 'mcp_hangar_mcp_server_starts_total{mcp_server="srv-alias"' in m.get_metrics()

    def test_the_set_of_events_relying_on_the_walk_is_known(self):
        """If a fifth appears, this says so rather than it silently going unmetered."""
        import inspect

        from mcp_hangar.domain import events as domain_events

        via_ancestor = set()
        for name in dir(domain_events):
            obj = getattr(domain_events, name)
            if not (inspect.isclass(obj) and issubclass(obj, domain_events.DomainEvent)):
                continue
            if obj in MetricsEventHandler._DISPATCH:
                continue
            base = next((k for k in obj.__mro__[1:] if k in MetricsEventHandler._DISPATCH), None)
            if base is not None:
                via_ancestor.add((name, base.__name__))

        assert via_ancestor == set(self.ALIASES_DISPATCHING_VIA_A_BASE), (
            f"the events dispatching through an ancestor changed: {sorted(via_ancestor)}"
        )

    def test_every_table_entry_names_a_real_method(self):
        """Values are method names, so a typo would fail only at dispatch time."""
        handler = MetricsEventHandler()
        missing = [
            f"{klass.__name__} -> {method}"
            for klass, method in MetricsEventHandler._DISPATCH.items()
            if not callable(getattr(handler, method, None))
        ]
        assert missing == [], f"dispatch table points at methods that do not exist: {missing}"
