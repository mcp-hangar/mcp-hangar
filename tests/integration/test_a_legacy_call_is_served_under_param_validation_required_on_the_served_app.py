"""A handshake-era call is served under ``param_validation.required`` on the served app (#1605).

Over the real streamable-HTTP transport (``_front_door_harness``), with
``headers.param_validation.required`` on:

- a legacy-revision front-door call carrying ``Mcp-Param-Region`` is served,
  and a header deny on that very value does not decide its L7 verdict:
  ``hangar.l7.rule_kind`` is ``tool``, not ``header``. Before #1605 it was
  refused with ``HEADER_MISMATCH``;
- a legacy ``hangar_call`` carrying an ``Mcp-Param-*`` header is served, and the
  same call on the modern revision is still refused.

ADR-025 Decision 2: a legacy revision is an era rather than a failure, so its
headers are ignored rather than refused.

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
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.infrastructure.observability.l7_verdicts import ContextL7VerdictObserver
from mcp_hangar.observability.conventions import L7
from mcp_hangar.tasks_wire import HEADER_MISMATCH
from tests.integration._front_door_harness import LEGACY, MODERN, SERVER, TENANT_A, Upstream, front_door, jsonrpc

pytestmark = pytest.mark.otel_sdk

TOOL = "read_item"
REGION = "eu-west-1"
#: Would deny the call if the legacy header were admitted to the selector.
POLICY = L7Policy(
    headers=HeaderRules(deny=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)),
    default_action=ToolAction.ALLOW,
)


class _RegionUpstream(Upstream):
    properties: ClassVar[dict[str, Any]] = {"region": {"type": "string", "x-mcp-header": "Region"}}


@pytest.fixture
def required() -> Iterator[None]:
    before = flat_tool_projection.param_validation_required()
    flat_tool_projection.set_param_validation_required(True)
    yield
    flat_tool_projection.set_param_validation_required(before)


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


def test_a_legacy_front_door_call_is_served_and_its_header_decides_nothing(required, spans) -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        _set(POLICY)
        payload = jsonrpc(
            door.call(TENANT_A, TOOL, {"region": REGION}, era=LEGACY, extra_headers={"Mcp-Param-Region": REGION})
        )
        assert door.upstream.called == [TOOL], payload

    assert "result" in payload, payload
    (span,) = [s for s in spans.get_finished_spans() if s.name == f"batch.call.{TOOL}"]
    assert (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND]) == ("allow", "tool")


@pytest.mark.parametrize(("era", "served"), [(LEGACY, True), (MODERN, False)], ids=["legacy", "modern"])
def test_hangar_call_with_a_param_header_is_served_only_on_a_legacy_revision(required, era, served) -> None:
    calls = [{"mcp_server": SERVER, "tool": TOOL, "arguments": {"region": REGION}}]
    with front_door((TOOL,), topology="egress", upstream_class=_RegionUpstream) as door:
        payload = jsonrpc(
            door.call(TENANT_A, "hangar_call", {"calls": calls}, era=era, extra_headers={"Mcp-Param-Tier": "gold"})
        )
        assert door.upstream.called == ([TOOL] if served else []), payload

    if served:
        assert "result" in payload and not payload["result"].get("isError"), payload
    else:
        assert payload.get("error", {}).get("code") == HEADER_MISMATCH, payload
