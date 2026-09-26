"""An L7 header rule's verdict reaches the call's span on the app ``serve --http`` serves (#1295).

A header selector only matches an ``Mcp-Param-*`` value the SDK validated
against the body on a modern protocol revision (ADR-025), so this cannot be
shown with a stand-in request context. Here a front-door call goes over the
real streamable-HTTP transport (``_front_door_harness``) to a tool whose
``region`` argument declares ``x-mcp-header: Region``, and the request mirrors
it in ``Mcp-Param-Region``. The aggregate's verdict is read off
``batch.call.<tool>`` with ``hangar.l7.rule_kind=header``; the header's value
and name are never on the span.

Nothing on the call path is patched except the tracer, which hands out spans
from an in-memory SDK provider.

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
from mcp_hangar.domain.policies.egress_l7 import HeaderMatch, HeaderRules, L7Policy, ToolAction
from mcp_hangar.infrastructure.observability.l7_verdicts import ContextL7VerdictObserver
from mcp_hangar.observability.conventions import L7, Gate
from tests.integration._front_door_harness import LEGACY, MODERN, SERVER, TENANT_A, FrontDoor, Upstream, front_door

pytestmark = pytest.mark.otel_sdk

TOOL = "read_item"
REGION = "eu-west-1"


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


def _gateway(headers: HeaderRules) -> Any:
    return front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream), L7Policy(
        headers=headers, default_action=ToolAction.ALLOW
    )


def _set(policy: L7Policy) -> None:
    from mcp_hangar.server.context import get_context

    get_context().get_mcp_server(SERVER).set_l7_policy(policy)


def _call(door: FrontDoor, era: str) -> None:
    door.call(TENANT_A, TOOL, {"region": REGION}, era=era, extra_headers={"Mcp-Param-Region": REGION})


def _call_span(spans: Any) -> Any:
    (span,) = [s for s in spans.get_finished_spans() if s.name == f"batch.call.{TOOL}"]
    return span


def test_a_header_deny_is_recorded_as_a_header_verdict(spans) -> None:
    gateway, policy = _gateway(HeaderRules(deny=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)))
    with gateway as door:
        _set(policy)
        _call(door, MODERN)
        assert door.upstream.called == [], "a denied call reached its upstream"

    span = _call_span(spans)
    assert {k: v for k, v in span.attributes.items() if k.startswith("hangar.l7.")} == {
        L7.VERDICT: "deny",
        L7.MODE: "enforce",
        L7.RULE_KIND: "header",
        L7.POLICY_ID: policy.policy_id,
    }
    assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY
    assert span.status.status_code.name == "UNSET"
    carried = [str(v) for s in spans.get_finished_spans() for v in s.attributes.values()]
    assert not [v for v in carried if REGION in v or "mcp-param" in v.lower()]


def test_a_header_allow_is_recorded_as_a_header_verdict(spans) -> None:
    gateway, policy = _gateway(HeaderRules(allow=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)))
    with gateway as door:
        _set(policy)
        _call(door, MODERN)
        assert door.upstream.called == [TOOL]

    span = _call_span(spans)
    assert (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND]) == ("allow", "header")


def test_a_legacy_request_is_decided_by_the_tool_rules(spans) -> None:
    """Nothing validated a handshake-era header, so the selector is not consulted: `tool`, not `header`."""
    gateway, policy = _gateway(HeaderRules(deny=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)))
    with gateway as door:
        _set(policy)
        _call(door, LEGACY)

    span = _call_span(spans)
    assert (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND]) == ("allow", "tool")
