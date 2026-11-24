"""Tool-call latency and counter metrics carry trace exemplars over OpenMetrics (#1290).

Drives real tool invocations through CommandBus -> InvokeToolHandler -> McpServer
-> EventBus -> MetricsEventHandler under real OpenTelemetry SDK spans (in-memory
exporter) and scrapes ``metrics_endpoint`` with OpenMetrics content negotiation.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON
from prometheus_client.openmetrics.parser import text_string_to_metric_families
from starlette.requests import Request

from mcp_hangar import metrics as m
from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.metrics import CollectorRegistry, Counter, Gauge, Histogram, Info, ScrapedGauge, Summary
from mcp_hangar.server.lifecycle import metrics_endpoint

FORBIDDEN_LABELS = {"trace_id", "user_id", "session_id"}


def _scrape(accept: str | None = "application/openmetrics-text; version=1.0.0"):
    headers = [] if accept is None else [(b"accept", accept.encode())]
    return metrics_endpoint(Request({"type": "http", "method": "GET", "path": "/metrics", "headers": headers}))


def _setup_invocation(reply):
    server = McpServer(mcp_server_id=f"exemplar-{uuid4().hex[:12]}", mode="subprocess", command=["echo"])
    server.ensure_ready = Mock()
    upstream = Mock(side_effect=reply) if isinstance(reply, Exception) else Mock(return_value=reply)
    server._client = MagicMock(call=upstream)
    server._state = McpServerState.READY
    server._tools.update_from_list([{"name": "add"}])

    events = EventBus()
    events.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
    bus = CommandBus()
    bus.register(InvokeToolCommand, InvokeToolHandler(Mock(get=Mock(return_value=server)), events))

    def invoke(tool: str = "add") -> None:
        with contextlib.suppress(Exception):
            bus.send(InvokeToolCommand(mcp_server_id=server.mcp_server_id, tool_name=tool, arguments={}))

    return server.mcp_server_id, invoke


def _parse_openmetrics(response) -> dict:
    text = response.body.decode("utf-8")
    assert text.endswith("# EOF\n")
    return {family.name: family for family in text_string_to_metric_families(text)}


def test_sampled_invocation_emits_trace_exemplars_over_openmetrics() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.exemplars")

    sid, invoke = _setup_invocation({"result": {"ok": True}})
    with tracer.start_as_current_span("command.send.InvokeToolCommand"):
        invoke("add")

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    span_ctx = finished[0].get_span_context()
    expected_labels = {
        "trace_id": format(span_ctx.trace_id, "032x"),
        "span_id": format(span_ctx.span_id, "016x"),
    }

    response = _scrape("application/openmetrics-text; version=1.0.0")
    assert response.headers["content-type"].startswith("application/openmetrics-text; version=1.0.0; charset=utf-8")
    families = _parse_openmetrics(response)

    # No high-cardinality trace/identity labels anywhere in the exposition.
    for family in families.values():
        for sample in family.samples:
            assert FORBIDDEN_LABELS.isdisjoint(sample.labels), f"Forbidden label in {sample.name}: {sample.labels}"

    # Histogram bucket carries the sampled span's trace_id and span_id as exemplar.
    duration_family = families["mcp_hangar_tool_call_duration_seconds"]
    server_buckets = [
        s
        for s in duration_family.samples
        if s.name == "mcp_hangar_tool_call_duration_seconds_bucket"
        and s.labels.get("mcp_server") == sid
        and s.labels.get("tool") == "add"
    ]
    exemplar_buckets = [s for s in server_buckets if s.exemplar is not None]
    assert len(exemplar_buckets) == 1
    bucket_sample = exemplar_buckets[0]
    assert bucket_sample.exemplar.labels == expected_labels
    assert bucket_sample.exemplar.value <= float(bucket_sample.labels["le"])
    assert bucket_sample.exemplar.timestamp is not None

    # Counter carries the same exemplar.
    calls_family = families["mcp_hangar_tool_calls"]
    call_samples = [
        s
        for s in calls_family.samples
        if s.name == "mcp_hangar_tool_calls_total"
        and s.labels.get("mcp_server") == sid
        and s.labels.get("tool") == "add"
        and s.labels.get("status") == "success"
    ]
    assert len(call_samples) == 1
    assert call_samples[0].exemplar is not None
    assert call_samples[0].exemplar.labels == expected_labels

    # Default 0.0.4 scrape stays exemplar-free and has no # EOF.
    default_response = _scrape("text/plain; version=0.0.4")
    assert default_response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    default_text = default_response.body.decode("utf-8")
    assert "# EOF" not in default_text
    assert expected_labels["trace_id"] not in default_text


@pytest.mark.parametrize(
    ("reply", "tool", "expected_error_type"),
    [
        (OSError("boom"), "add", "OSError"),
        ({"result": {}}, "missing", "ToolNotFoundError"),
    ],
)
def test_sampled_failed_invocation_emits_counter_exemplars(reply, tool: str, expected_error_type: str) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.exemplars.error")

    sid, invoke = _setup_invocation(reply)
    with tracer.start_as_current_span("command.send.InvokeToolCommand"):
        invoke(tool)

    span_ctx = exporter.get_finished_spans()[0].get_span_context()
    expected_labels = {
        "trace_id": format(span_ctx.trace_id, "032x"),
        "span_id": format(span_ctx.span_id, "016x"),
    }

    families = _parse_openmetrics(_scrape("application/openmetrics-text; version=1.0.0"))

    error_calls = [
        s
        for s in families["mcp_hangar_tool_calls"].samples
        if s.name == "mcp_hangar_tool_calls_total"
        and s.labels.get("mcp_server") == sid
        and s.labels.get("tool") == tool
        and s.labels.get("status") == "error"
    ]
    assert len(error_calls) == 1
    assert error_calls[0].exemplar is not None
    assert error_calls[0].exemplar.labels == expected_labels

    error_details = [
        s
        for s in families["mcp_hangar_tool_call_errors"].samples
        if s.name == "mcp_hangar_tool_call_errors_total"
        and s.labels.get("mcp_server") == sid
        and s.labels.get("tool") == tool
        and s.labels.get("error_type") == expected_error_type
    ]
    assert len(error_details) == 1
    assert error_details[0].exemplar is not None
    assert error_details[0].exemplar.labels == expected_labels


def test_tracing_off_or_unsampled_span_emits_no_exemplar(monkeypatch) -> None:
    from mcp_hangar.observability import tracing

    init_spy = Mock(side_effect=AssertionError("metrics must never initialize tracing"))
    monkeypatch.setattr(tracing, "init_tracing", init_spy)

    # 1. No active span (tracing off).
    sid_off, invoke_off = _setup_invocation({"result": {}})
    invoke_off("add")

    # 2. Active SDK span that is not sampled (ALWAYS_OFF).
    provider = TracerProvider(sampler=ALWAYS_OFF)
    tracer = provider.get_tracer("test.exemplars.unsampled")
    sid_unsampled, invoke_unsampled = _setup_invocation({"result": {}})
    with tracer.start_as_current_span("command.send.InvokeToolCommand"):
        invoke_unsampled("add")

    families = _parse_openmetrics(_scrape("application/openmetrics-text; version=1.0.0"))
    init_spy.assert_not_called()
    for sid in (sid_off, sid_unsampled):
        buckets = [
            s
            for s in families["mcp_hangar_tool_call_duration_seconds"].samples
            if s.name == "mcp_hangar_tool_call_duration_seconds_bucket" and s.labels.get("mcp_server") == sid
        ]
        assert buckets
        assert all(s.exemplar is None for s in buckets)

        calls = [
            s
            for s in families["mcp_hangar_tool_calls"].samples
            if s.name == "mcp_hangar_tool_calls_total" and s.labels.get("mcp_server") == sid
        ]
        assert calls
        assert all(s.exemplar is None for s in calls)


@pytest.mark.parametrize(
    ("accept", "expect_openmetrics"),
    [
        ("application/openmetrics-text", True),
        ("application/openmetrics-text; version=1.0.0", True),
        ("application/openmetrics-text; version=1.0.0; q=0.5, text/plain; version=0.0.4; q=0.3", True),
        ("text/plain; version=1.0.0", False),
        ("application/openmetrics-text; version=0.0.1", False),
        ("text/plain; version=0.0.4", False),
        (None, False),
    ],
)
def test_accept_header_negotiation_matches_choose_encoder(accept: str | None, expect_openmetrics: bool) -> None:
    response = _scrape(accept)
    content_type = response.headers["content-type"]
    text = response.body.decode("utf-8")

    if expect_openmetrics:
        assert content_type.startswith("application/openmetrics-text; version=1.0.0; charset=utf-8")
        assert text.endswith("# EOF\n")
        families = _parse_openmetrics(response)
        # Private registry: default prometheus_client process/python collectors must not leak in.
        assert "python_info" not in families
        assert "python_gc_objects_collected" not in families
    else:
        assert content_type == "text/plain; version=0.0.4; charset=utf-8"
        assert "# EOF" not in text


def test_openmetrics_adapter_renders_every_collector_type_validly(monkeypatch) -> None:
    registry = CollectorRegistry()
    counter = Counter("demo_calls", "Calls", ["tool"])
    gauge = Gauge("demo_up", "Up", ["server"])
    scraped = ScrapedGauge("demo_held", "Held", ["server"])
    histogram = Histogram("demo_latency_seconds", "Latency", ["server"], buckets=(0.1, 1.0))
    int_hist = Histogram("demo_size_bytes", "Size", buckets=(100, 1000))
    summary = Summary("demo_wait_seconds", "Wait", ["server"])
    info = Info("demo_build", "Build")

    for col in (counter, gauge, scraped, histogram, int_hist, summary, info):
        registry.register(col)

    exemplar = {"trace_id": "a" * 32, "span_id": "b" * 16}
    counter.inc(1.0, exemplar=exemplar, tool="add")
    gauge.set(1, server="s1")
    scraped.read_from(lambda: [{"server": "s2"}])
    histogram.observe(0.05, exemplar=exemplar, server="s1")
    histogram.observe(0.5, server="s2")
    int_hist.observe(50)
    summary.observe(0.25, server="s1")
    info.info(version="1.2.3")

    monkeypatch.setattr(m, "REGISTRY", registry)
    families = _parse_openmetrics(_scrape("application/openmetrics-text; version=1.0.0"))

    assert set(families) == {
        "demo_calls",
        "demo_up",
        "demo_held",
        "demo_latency_seconds",
        "demo_size_bytes",
        "demo_wait_seconds",
        "demo_build",
    }
    assert families["demo_calls"].type == "counter"
    assert families["demo_calls"].samples[0].exemplar.labels == exemplar
    assert families["demo_build"].type == "info"
    assert families["demo_latency_seconds"].type == "histogram"


def test_exemplar_over_128_utf8_characters_is_rejected() -> None:
    counter = Counter("demo_limit_counter", "Limit test")
    histogram = Histogram("demo_limit_hist", "Limit test", buckets=(1.0,))
    oversized = {"trace_id": "a" * 121}  # 8 + 121 = 129 > 128

    with pytest.raises(ValueError, match="128"):
        counter.inc(1.0, exemplar=oversized)
    with pytest.raises(ValueError, match="128"):
        histogram.observe(0.5, exemplar=oversized)
