"""An ``Mcp-Param-*`` header nothing checked does not decide an L7 verdict on the served app (#1597).

The SDK compares an ``Mcp-Param-*`` header with the body only when the called
tool declares it with ``x-mcp-header``. ``hangar_call`` declares none, so a
header sent to it is unchecked, and ADR-025 says a selector must not match it.
Before #1597 it did, and a header ``allow`` rule outranked the tool-name
default-deny.

Over the real streamable-HTTP transport (``_front_door_harness``), with the
policy's tool rules denying ``read_item`` by default and a header rule allowing
any ``Mcp-Param-Tier``:

- on ``hangar_call`` the tier header is not consulted: the call is denied, and
  the span reads ``rule_kind=tool``;
- on the front door, a tier header beside the declared ``Mcp-Param-Region``
  is not consulted either: the SDK checked only the region.

The declared and checked header still decides on the front door; that is
``test_l7_header_verdict_on_the_served_app``.

Naming: neutral placeholders only (store, read_item, tenant:a).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from mcp_hangar.domain.contracts.l7_verdict_observer import (
    get_default_l7_verdict_observer,
    set_default_l7_verdict_observer,
)
from mcp_hangar.domain.policies.egress_l7 import (
    HEADER_RULES_NOT_CONSULTED,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
)
from mcp_hangar.infrastructure.observability.l7_verdicts import ContextL7VerdictObserver
from mcp_hangar.observability.conventions import L7
from tests.integration._front_door_harness import MODERN, SERVER, TENANT_A, Upstream, front_door, jsonrpc

pytestmark = pytest.mark.otel_sdk

TOOL = "read_item"
REGION = "eu-west-1"
#: The allow rule the unchecked header would satisfy, and the tool-name default-deny it must not outrank.
POLICY = L7Policy(
    tools=ToolRules(allow=("other_tool",)),
    headers=HeaderRules(allow=(HeaderMatch(name="Mcp-Param-Tier", values=("*",)),)),
    default_action=ToolAction.DENY,
)


class _RegionUpstream(Upstream):
    properties: ClassVar[dict[str, Any]] = {"region": {"type": "string", "x-mcp-header": "Region"}}


@pytest.fixture
def spans() -> Iterator[Any]:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = _TextFreeTracer(provider.get_tracer("test"))
    previous = get_default_l7_verdict_observer()
    set_default_l7_verdict_observer(ContextL7VerdictObserver())
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=tracer),
        patch("mcp_hangar.infrastructure.command_bus.get_tracer", return_value=tracer),
    ):
        yield memory
    set_default_l7_verdict_observer(previous)


def _set(policy: L7Policy) -> None:
    from mcp_hangar.server.context import get_context

    get_context().get_mcp_server(SERVER).set_l7_policy(policy)


def _call_span(spans: Any) -> Any:
    (span,) = [s for s in spans.get_finished_spans() if s.name == f"batch.call.{TOOL}"]
    return span


def _enforced() -> list[Any]:
    """Every ``EgressPolicyEnforced`` the gateway publishes from here on: the record that carries the reasons."""
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import EgressPolicyEnforced
    from mcp_hangar.server.context import get_context

    seen: list[Any] = []
    get_context().event_bus.subscribe(EgressPolicyEnforced, seen.append, kind=HandlerKind.PROJECTION)
    return seen


def test_hangar_call_does_not_let_an_unchecked_header_outrank_the_tool_rules(spans) -> None:
    with front_door((TOOL,), topology="egress", upstream_class=_RegionUpstream) as door:
        _set(POLICY)
        enforced = _enforced()
        calls = [{"mcp_server": SERVER, "tool": TOOL, "arguments": {"region": REGION}}]
        response = door.call(
            TENANT_A, "hangar_call", {"calls": calls}, era=MODERN, extra_headers={"Mcp-Param-Tier": "gold"}
        )
        assert "result" in jsonrpc(response), response.text[:300]
        assert door.upstream.called == [], "a call the tool rules deny reached its upstream"

    span = _call_span(spans)
    assert (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND]) == ("deny", "tool")
    (event,) = enforced
    assert event.rule_kind == "tool"
    assert HEADER_RULES_NOT_CONSULTED in event.reasons


def test_the_front_door_does_not_let_an_undeclared_header_ride_on_a_declared_one(spans) -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        _set(POLICY)
        door.call(
            TENANT_A,
            TOOL,
            {"region": REGION},
            era=MODERN,
            extra_headers={"Mcp-Param-Region": REGION, "Mcp-Param-Tier": "gold"},
        )
        assert door.upstream.called == []

    span = _call_span(spans)
    assert (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND]) == ("deny", "tool")
