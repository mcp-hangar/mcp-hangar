"""Hangar forwards no W3C baggage upstream and extracts none (GHSA-qwq2-7g49-jxc6).

Nothing on the wire says who set a baggage entry, and Hangar sets none itself.
The scrub this file used to test kept every outbound entry whose key started
with ``hangar.``, taking the prefix as proof that Hangar had set it. A caller can
write that prefix too, and the stdio transport never ran the scrub at all. Now
``inject_trace_context`` is the one outbound chokepoint for both transports. It
writes W3C trace context only and removes any ``baggage`` entry from the
carrier, so the rule cannot differ between them.

The forged entries below stand in for baggage that host auto-instrumentation or
an embedding application attached to the context from an inbound request.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.observability.tracing import extract_trace_context, inject_trace_context

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
#: A caller's baggage, one entry wearing Hangar's old "trusted" prefix. The
#: advisory's reproduction: the old scrub kept ``hangar.untrusted``.
FORGED = {"hangar.untrusted": "caller_supplied", "user.id": "alice"}
#: The same with a tenant marker, which made the old scrub drop everything.
FORGED_WITH_TENANT = {**FORGED, "hangar.tenant": "t1"}
AMBIENT = {"prefix-only": FORGED, "with-tenant-marker": FORGED_WITH_TENANT}
#: What a caller could put in ``params._meta`` directly.
CALLER_META_BAGGAGE = "hangar.untrusted=from_meta,user.id=bob"


@contextmanager
def _ambient_baggage(entries: dict[str, str]) -> Iterator[None]:
    """Attach ``entries`` as baggage to the current context, as inbound instrumentation would."""
    from opentelemetry import baggage
    from opentelemetry import context as otel_context

    ctx = otel_context.get_current()
    for key, value in entries.items():
        ctx = baggage.set_baggage(key, value, context=ctx)
    token = otel_context.attach(ctx)
    try:
        yield
    finally:
        otel_context.detach(token)


@pytest.fixture()
def otel() -> Iterator[Any]:
    """Hangar's spans go to a local SDK provider, and its tracing counts as active."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with (
        patch("mcp_hangar.observability.tracing.get_tracer", side_effect=provider.get_tracer),
        patch("mcp_hangar.observability.tracing._initialized", True),
    ):
        yield exporter


def _baggage_keys(carrier: dict[str, Any]) -> list[str]:
    return [k for k in carrier if k.lower() == "baggage"]


class TestInbound:
    @pytest.mark.otel_sdk
    def test_inbound_baggage_is_not_extracted_and_trace_context_still_is(self) -> None:
        from opentelemetry import baggage, trace

        inbound = {"traceparent": TRACEPARENT, "baggage": "hangar.untrusted=caller_supplied,user.id=alice"}

        with patch("mcp_hangar.observability.tracing._initialized", True):
            ctx = extract_trace_context(inbound)

        assert dict(baggage.get_all(ctx)) == {}
        assert f"{trace.get_current_span(ctx).get_span_context().trace_id:032x}" == TRACE_ID


class TestTheChokepoint:
    @pytest.mark.otel_sdk
    def test_forged_hangar_prefixed_ambient_baggage_is_not_injected(self) -> None:
        from opentelemetry import context as otel_context

        with patch("mcp_hangar.observability.tracing._initialized", True):
            ctx = extract_trace_context({"traceparent": TRACEPARENT})
        token = otel_context.attach(ctx)
        try:
            with (
                _ambient_baggage(FORGED),
                patch("mcp_hangar.observability.tracing._initialized", True),
            ):
                carrier: dict[str, Any] = {}
                inject_trace_context(carrier)
        finally:
            otel_context.detach(token)

        assert _baggage_keys(carrier) == [], carrier
        assert TRACE_ID in carrier["traceparent"]

    @pytest.mark.parametrize("otel_available", [True, False], ids=["sdk", "no-sdk"])
    def test_a_carrier_loses_its_baggage_even_with_tracing_off(self, otel_available: bool) -> None:
        carrier = {"traceparent": TRACEPARENT, "baggage": CALLER_META_BAGGAGE, "Baggage": "user.id=carol"}

        with patch("mcp_hangar.observability.tracing.OTEL_AVAILABLE", otel_available):
            inject_trace_context(carrier)

        assert carrier == {"traceparent": TRACEPARENT}


def _http_send(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``HttpClient.call`` with httpx's post patched; return the (body, headers) it posted."""
    from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

    client = HttpClient(endpoint="http://upstream:8080", auth_config=AuthConfig(), http_config=HttpClientConfig())
    client.modern_envelope = True
    posted: list[tuple[dict[str, Any], dict[str, str]]] = []

    def capture_post(url, *, json=None, timeout=None, headers=None, **kwargs):  # noqa: ANN001, ANN202
        posted.append((json, dict(headers or {})))
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"jsonrpc": "2.0", "id": "t", "result": {}}
        return resp

    with patch.object(client._client, "post", side_effect=capture_post):
        client.call("tools/call", params)
    [sent] = posted
    return sent


def _stdio_send(params: dict[str, Any]) -> dict[str, Any]:
    """``StdioClient.call`` over a fake process; return the request it wrote."""
    from mcp_hangar.stdio_client import StdioClient

    process = MagicMock(spec=subprocess.Popen)
    process.pid = 4242
    process.stdin = MagicMock()
    process.stdout = MagicMock()
    process.poll.return_value = None
    with patch("mcp_hangar.stdio_client.threading.Thread"):
        client = StdioClient(process)
    written: list[dict[str, Any]] = []

    def write(line: str) -> None:
        request = json.loads(line)
        written.append(request)
        with client.pending_lock:
            pending = client.pending.pop(request["id"])
        pending.result_queue.put({"jsonrpc": "2.0", "id": request["id"], "result": {}})

    process.stdin.write.side_effect = write
    client.call("tools/call", params, timeout=1.0)
    [request] = written
    return request


PARAMS = {"name": "t", "arguments": {}}
CALLER_PARAMS = {**PARAMS, "_meta": {"baggage": CALLER_META_BAGGAGE}}


@pytest.mark.otel_sdk
class TestHttpTransport:
    @pytest.mark.parametrize("ambient", AMBIENT)
    @pytest.mark.parametrize("params", [PARAMS, CALLER_PARAMS], ids=["ambient", "ambient+caller-meta"])
    def test_neither_carrier_forwards_baggage(self, otel: Any, params: dict[str, Any], ambient: str) -> None:
        with _ambient_baggage(AMBIENT[ambient]):
            body, headers = _http_send(params)

        meta = body["params"]["_meta"]
        assert _baggage_keys(headers) == [], headers
        assert _baggage_keys(meta) == [], meta
        assert headers["traceparent"] == meta["traceparent"]


@pytest.mark.otel_sdk
class TestStdioTransport:
    @pytest.mark.parametrize("ambient", AMBIENT)
    @pytest.mark.parametrize("params", [PARAMS, CALLER_PARAMS], ids=["ambient", "ambient+caller-meta"])
    def test_meta_forwards_no_baggage(self, otel: Any, params: dict[str, Any], ambient: str) -> None:
        with _ambient_baggage(AMBIENT[ambient]):
            request = _stdio_send(params)

        meta = request["params"]["_meta"]
        assert _baggage_keys(meta) == [], meta
        assert meta["traceparent"].startswith("00-")
