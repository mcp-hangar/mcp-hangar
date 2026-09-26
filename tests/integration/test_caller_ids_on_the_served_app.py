"""Caller ids reach the call's span only when the operator opts in (#1580).

Two tenants' API keys call over the real streamable-HTTP transport
(``_front_door_harness``), on both entry points: the front door's flat
``tools/call`` and ``hangar_call`` in egress. The identity the span is built
from is the one the key authenticated, re-bound per request on this transport.
Nothing on the call path is patched except the executor's tracer, which hands
out spans from an in-memory SDK provider.

By default the ``batch.call.<tool>`` span carries the caller's type, tenant and
correlation id, and none of ``mcp.caller.id``, ``mcp.user.id``,
``mcp.agent.id`` or ``mcp.session.id`` (#1276 decision 1). With
``observability.tracing.caller_ids`` on, the principal comes back.

Naming: neutral placeholders only (store, read_item, tenant:a).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.observability import tracing
from mcp_hangar.observability.conventions import MCP, Caller
from tests.integration._front_door_harness import SERVER, TENANT_A, FrontDoor, front_door, jsonrpc

pytestmark = pytest.mark.otel_sdk

READ = "read_item"
PRINCIPAL = "agent-a"  # the harness's key for TENANT_A
CALLER_ID_KEYS = (Caller.ID, MCP.USER_ID, MCP.AGENT_ID, MCP.SESSION_ID)


@pytest.fixture
def exporter() -> Iterator[Any]:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=provider.get_tracer("test")):
        yield memory


@pytest.fixture(params=["front_door", "egress"])
def gateway(request: pytest.FixtureRequest) -> Iterator[tuple[FrontDoor, str]]:
    with front_door((READ,), topology=request.param) as door:
        yield door, request.param


@pytest.fixture
def opted_in() -> Iterator[None]:
    tracing.set_caller_ids_on_spans(True)
    yield
    tracing.set_caller_ids_on_spans(False)


def _call_span_attributes(gateway: tuple[FrontDoor, str], exporter: Any) -> dict[str, Any]:
    door, topology = gateway
    if topology == "front_door":
        jsonrpc(door.call(TENANT_A, READ, {"x": "1"}))
    else:
        calls = [{"mcp_server": SERVER, "tool": READ, "arguments": {"x": "1"}}]
        jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    spans = [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{READ}"]
    assert len(spans) == 1, [s.name for s in exporter.get_finished_spans()]
    return dict(spans[0].attributes)


def test_by_default_the_span_carries_the_tenant_and_no_caller_id(gateway, exporter):
    assert tracing.caller_ids_on_spans() is False

    attributes = _call_span_attributes(gateway, exporter)

    assert attributes[Caller.TENANT] == TENANT_A
    assert attributes[Caller.TYPE]
    assert [key for key in CALLER_ID_KEYS if key in attributes] == []
    assert [key for key, value in attributes.items() if value == PRINCIPAL] == []


def test_opted_in_the_span_carries_the_principal(gateway, exporter, opted_in):
    attributes = _call_span_attributes(gateway, exporter)

    assert attributes[Caller.TENANT] == TENANT_A
    assert attributes[Caller.ID] == PRINCIPAL
    assert attributes[MCP.USER_ID] == PRINCIPAL
