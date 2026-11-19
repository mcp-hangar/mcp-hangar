"""A span another library starts on Hangar's provider keeps the contract's R2 too (#2285).

The MCP SDK wraps every request in a SERVER span (``mcp.server._otel``) and, on
an ``MCPError``, copies the JSON-RPC message into the status description. The
front door answers a made-up tool name with "Tool '<name>' not found", so the
caller's own text reached the operator's traces. ``_ForeignSpanBounds``, the
first processor on the provider Hangar builds, keeps a description only when
it is the span's ``error.type``, and cuts a span name to the attribute bound.

These drive the SDK's own middleware, so a release of it that changes how it
sets the status turns them red. Only the module's tracer is replaced, with one
from a provider built the way Hangar builds its own.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("opentelemetry.sdk")

from mcp.server._otel import OpenTelemetryMiddleware  # noqa: E402
from mcp.shared import _otel as sdk_otel  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from opentelemetry.sdk.trace import SpanLimits, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import Status, StatusCode  # noqa: E402

from mcp_hangar.observability import tracing  # noqa: E402
from mcp_hangar.observability.tracing import SPAN_ATTRIBUTE_LENGTH_LIMIT, _ForeignSpanBounds  # noqa: E402

LIMIT = SPAN_ATTRIBUTE_LENGTH_LIMIT


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Hangar's provider in miniature: the attribute bound, the bounds first, then an exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(span_limits=SpanLimits(max_attribute_length=LIMIT))
    provider.add_span_processor(_ForeignSpanBounds(LIMIT))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _sdk_tools_call(provider: TracerProvider, name: str, error: Exception) -> Any:
    """One ``tools/call`` of ``name`` through the SDK's middleware, whose handler raises ``error``."""
    ctx = SimpleNamespace(
        method="tools/call", params={"name": name}, protocol_version="2026-07-28", request_id=1, meta=None
    )

    async def call_next(_ctx: Any) -> Any:
        raise error

    with patch.object(sdk_otel, "_tracer", provider.get_tracer("mcp-python-sdk")), pytest.raises(type(error)):
        asyncio.run(OpenTelemetryMiddleware()(ctx, call_next))


def test_the_sdk_span_of_a_refused_call_keeps_no_message() -> None:
    provider, exporter = _provider()

    _sdk_tools_call(provider, "made-up", MCPError(-32601, "Tool 'made-up' not found"))

    [span] = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None, span.status.description
    # The code the contract allows is still there.
    assert span.attributes["error.type"] == "-32601"


def test_a_long_made_up_name_is_cut_to_the_bound() -> None:
    provider, exporter = _provider()
    name = "made-up-" + "x" * 5000

    _sdk_tools_call(provider, name, MCPError(-32601, f"Tool '{name}' not found"))

    [span] = exporter.get_finished_spans()
    assert len(span.name) == LIMIT, len(span.name)
    assert len(span.attributes["gen_ai.tool.name"]) == LIMIT
    assert span.status.description is None


@pytest.mark.parametrize("form", ["status", "code_and_description"])
def test_a_description_survives_only_as_the_error_type(form: str) -> None:
    provider, exporter = _provider()
    tracer = provider.get_tracer("another-library")

    for described in ("E_CODE", "free text"):
        with tracer.start_as_current_span(described) as span:
            span.set_attribute("error.type", "E_CODE")
            if form == "status":
                span.set_status(Status(StatusCode.ERROR, described))
            else:
                span.set_status(StatusCode.ERROR, described)

    kept = {s.name: s.status.description for s in exporter.get_finished_spans()}
    assert kept == {"E_CODE": "E_CODE", "free text": None}


def test_an_ok_status_and_a_renamed_span_still_work() -> None:
    provider, exporter = _provider()

    with provider.get_tracer("another-library").start_as_current_span("first") as span:
        span.update_name("renamed-" + "y" * 5000)
        span.set_status(Status(StatusCode.OK))

    [finished] = exporter.get_finished_spans()
    assert finished.status.status_code is StatusCode.OK
    assert finished.name.startswith("renamed-") and len(finished.name) == LIMIT


def test_a_span_it_cannot_wrap_is_left_alone_and_logged_once() -> None:
    bounds = _ForeignSpanBounds(LIMIT)
    with patch.object(tracing, "logger") as log:
        bounds.on_start(SimpleNamespace())
        bounds.on_start(SimpleNamespace())

    [warning] = log.warning.call_args_list
    assert warning.args == ("tracing_span_bounds_not_applied",)


def test_hangars_provider_installs_the_bounds_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real ``init_tracing`` puts the bounds ahead of every exporter it adds.

    The registration is refused, so ``init_tracing`` shuts the provider down
    and leaves the module's state as it found it.
    """
    monkeypatch.setattr(tracing, "_initialized", False)
    monkeypatch.setattr(tracing, "_shut_down", False)
    added: list[Any] = []

    class Recording(TracerProvider):
        def add_span_processor(self, span_processor: Any) -> None:
            added.append(span_processor)
            super().add_span_processor(span_processor)

    with (
        patch.object(tracing, "TracerProvider", Recording),
        patch.object(tracing, "_provider_registered_elsewhere", return_value=False),
        patch.object(tracing.trace, "set_tracer_provider"),
    ):
        tracing.init_tracing(service_name="bounds-test", otlp_endpoint="http://127.0.0.1:1", console_export=True)

    assert len(added) > 1 and isinstance(added[0], _ForeignSpanBounds), [type(p).__name__ for p in added]
