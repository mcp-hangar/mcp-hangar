"""Gate decisions reach the call's span on the app ``serve --http`` serves (#1285).

The unit tests drive `BatchExecutor` with a stand-in context and bind the
tenant themselves. Here two tenants call over the real streamable-HTTP
transport (``_front_door_harness``), each with its own API key, so the tenant
the withdrawal and the pin are keyed on is the one the key authenticated,
re-bound per request on this transport and carried into the executor's worker
thread. A per-tenant control that fell open on that path would show here as a
`skip` or an `allow` where the tenant's own rule should have refused.

Nothing on the call path is patched except the executor's tracer, which hands
out spans from an in-memory SDK provider.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.observability.conventions import Gate
from tests.integration._front_door_harness import SERVER, TENANT_A, TENANT_B, FrontDoor, front_door, jsonrpc

pytestmark = pytest.mark.otel_sdk

READ = "read_item"
WRITE = "write_item"
STALE = "c" * 64


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
    """Tenant A has `write_item` withdrawn and `read_item` pinned to a digest it cannot match."""
    with front_door((READ, WRITE), topology=request.param) as door:
        registry = get_tool_projection_registry()
        registry.set_config_withdrawal(SERVER, WRITE, TENANT_A)
        registry.set_config_pin(SERVER, READ, TENANT_A, ToolDigest(tool_name=READ, sha256=STALE))
        yield door, request.param


def _call(gateway: tuple[FrontDoor, str], tenant: str, tool: str) -> None:
    door, topology = gateway
    if topology == "front_door":
        jsonrpc(door.call(tenant, tool, {"x": "1"}))
        return
    calls = [{"mcp_server": SERVER, "tool": tool, "arguments": {"x": "1"}}]
    jsonrpc(door.call(tenant, "hangar_call", {"calls": calls}))


def _decisions(exporter: Any, tool: str) -> tuple[dict[str, tuple[Any, ...]], dict[str, Any]]:
    """The one `batch.call.<tool>` span's decisions by gate name, and its attributes."""
    spans = [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{tool}"]
    assert len(spans) == 1, [s.name for s in exporter.get_finished_spans()]
    events = [e.attributes for e in spans[0].events if e.name == Gate.DECISION_EVENT]
    by_name = {e[Gate.NAME]: (e[Gate.OUTCOME], e.get(Gate.REASON), e.get(Gate.REVISION)) for e in events}
    return by_name, dict(spans[0].attributes)


def test_the_withdrawing_tenant_is_refused_by_the_withdrawal_gate(gateway, exporter):
    _call(gateway, TENANT_A, WRITE)

    if gateway[1] == "front_door":
        # The front door does not project a tool withdrawn for the caller, so
        # the flat call is answered "not found" before the executor, and no
        # gate ran to record anything. Asserted so that changing it is seen.
        assert not [s for s in exporter.get_finished_spans() if s.name.startswith("batch.call.")]
        return
    decisions, attributes = _decisions(exporter, WRITE)
    assert decisions["withdrawal"] == (Gate.DENY, "tool_withdrawn", None)
    assert attributes[Gate.REFUSAL_GATE] == "withdrawal"
    assert attributes[Gate.CALL_OUTCOME] == Gate.DENY
    assert gateway[0].upstream.called == []


def test_another_tenant_passes_the_same_withdrawal(gateway, exporter):
    _call(gateway, TENANT_B, WRITE)

    decisions, attributes = _decisions(exporter, WRITE)
    assert decisions["withdrawal"] == (Gate.ALLOW, None, None)
    assert attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
    assert Gate.REFUSAL_GATE not in attributes


def test_the_pinning_tenant_is_refused_by_the_pin_with_the_digest_it_named(gateway, exporter):
    _call(gateway, TENANT_A, READ)

    decisions, attributes = _decisions(exporter, READ)
    assert decisions["digest_pin"] == (Gate.DENY, "digest_mismatch", STALE)
    assert attributes[Gate.REFUSAL_GATE] == "digest_pin"
    assert gateway[0].upstream.called == []


def test_a_tenant_with_no_pin_is_told_skip(gateway, exporter):
    _call(gateway, TENANT_B, READ)

    decisions, attributes = _decisions(exporter, READ)
    assert decisions["digest_pin"] == (Gate.SKIP, "no_pin", None)
    assert attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
