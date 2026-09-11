"""Tracer provider ownership and lifecycle, one interpreter per case (#1283).

OpenTelemetry registers the global tracer provider once per process and offers no
way to undo it, so a case that registers one decides the outcome of every case
after it. Each case therefore runs in a fresh subprocess with the real SDK, prints
one JSON line of observations, and the parent asserts on that line and on the
child's raw stdout and stderr (where Hangar's logs go).
"""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.otel_sdk

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

_PRELUDE = """
import json
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from mcp_hangar.observability import tracing as t

built = []

def use_in_memory_exporters():
    # Hangar's OTLP exporter becomes an in-memory one, exported synchronously:
    # a span is observable the moment it ends and nothing leaves the process.
    def factory(**kwargs):
        built.append(InMemorySpanExporter())
        return built[-1]
    t.OTLP_AVAILABLE = True
    t.OTLPSpanExporter = factory
    t.BatchSpanProcessor = SimpleSpanProcessor

def span_names(exporter):
    return [s.name for s in exporter.get_finished_spans()]

def emit(**observed):
    print(json.dumps(observed))
"""


def _run(body: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_"))}
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc


def _observed(proc: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_provider_registered_first_is_used_and_never_claimed() -> None:
    """Through the bootstrap, the way the server starts: Hangar builds nothing, logs no init, shuts nothing down."""
    proc = _run("""
use_in_memory_exporters()
host_spans = InMemorySpanExporter()
host = TracerProvider()
host.add_span_processor(SimpleSpanProcessor(host_spans))
# The detection contract Hangar relies on: the API's global is its proxy until registration.
proxy_before = isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)
trace.set_tracer_provider(host)
added = []
host.add_span_processor = added.append  # anything attached from here on is recorded instead

from mcp_hangar.server.bootstrap.observability import TracingConfig, init_tracing, shutdown_observability

initialized = init_tracing(TracingConfig(console_export=True))
carrier = {}
with t.get_tracer("case").start_as_current_span("hangar-span"):
    t.inject_trace_context(carrier)
    ids = [t.get_current_trace_id(), t.get_current_span_id()]
extracted = trace.get_current_span(t.extract_trace_context(carrier)).get_span_context()
shutdown_observability(None)
with trace.get_tracer("host").start_as_current_span("after-hangar-shutdown"):
    pass
emit(
    proxy_before=proxy_before,
    initialized=initialized,
    built=len(built),
    added=len(added),
    spans=span_names(host_spans),
    traceparent=carrier.get("traceparent"),
    ids=ids,
    extracted=[format(extracted.trace_id, "032x"), format(extracted.span_id, "016x")],
)
""")
    seen = _observed(proc)

    assert seen["proxy_before"] is True
    assert seen["initialized"] is False
    assert seen["built"] == 0 and seen["added"] == 0
    # Hangar's span reached the host's provider, and the host's provider still
    # exports after Hangar shut down: it was never shut down.
    assert seen["spans"] == ["hangar-span", "after-hangar-shutdown"]
    trace_id, span_id = seen["ids"]
    assert seen["traceparent"].split("-")[1:3] == [trace_id, span_id]
    assert seen["extracted"] == [trace_id, span_id]
    assert "tracing_external_provider_in_use" in proc.stderr
    for claim in ("tracing_initialized", "tracing_shutdown_complete", "Overriding of current TracerProvider"):
        assert claim not in proc.stderr, claim


@pytest.mark.parametrize(
    ("route", "env"),
    [("config_file", {}), ("env", {"MCP_TRACING_ENABLED": "false"})],
)
def test_tracing_disabled_keeps_hangars_spans_off_a_host_provider(route: str, env: dict[str, str]) -> None:
    """An operator's "tracing disabled" holds whoever owns the provider."""
    proc = _run(
        f"ROUTE = {route!r}\n"
        + """
host_spans = InMemorySpanExporter()
host = TracerProvider()
host.add_span_processor(SimpleSpanProcessor(host_spans))
trace.set_tracer_provider(host)

from mcp_hangar.server.bootstrap.observability import TracingConfig, _parse_observability_config, init_tracing

config = TracingConfig(enabled=False) if ROUTE == "config_file" else _parse_observability_config({}).tracing
assert not config.enabled
initialized = init_tracing(config)
carrier = {}
with trace.get_tracer("host").start_as_current_span("host-span"):
    tracer = t.get_tracer("case")
    with tracer.start_as_current_span("hangar-span"):
        t.inject_trace_context(carrier)
emit(initialized=initialized, noop=isinstance(tracer, t.NoOpTracer), carrier=carrier, spans=span_names(host_spans))
""",
        **env,
    )
    seen = _observed(proc)

    assert seen["initialized"] is False
    assert seen["noop"] is True
    assert seen["carrier"] == {}
    assert seen["spans"] == ["host-span"]
    assert "tracing_disabled_by_config" in proc.stderr


def test_a_second_init_is_a_noop() -> None:
    proc = _run("""
use_in_memory_exporters()
first = t.init_tracing()
second = t.init_tracing()
with t.get_tracer("case").start_as_current_span("owned-span"):
    pass
emit(first=first, second=second, built=len(built), spans=span_names(built[0]))
""")
    seen = _observed(proc)

    assert seen["first"] is True and seen["second"] is True
    assert seen["built"] == 1
    assert seen["spans"] == ["owned-span"]
    assert proc.stderr.count("tracing_initialized") == 1


def test_a_second_shutdown_is_safe() -> None:
    proc = _run("""
use_in_memory_exporters()
assert t.init_tracing()
with t.get_tracer("case").start_as_current_span("before-shutdown"):
    pass
t.shutdown_tracing()
t.shutdown_tracing()
emit(spans=span_names(built[0]), noop=isinstance(t.get_tracer("case"), t.NoOpTracer))
""")
    seen = _observed(proc)

    assert seen["spans"] == ["before-shutdown"]
    assert seen["noop"] is True
    assert proc.stderr.count("tracing_shutdown_complete") == 1
    assert "tracing_shutdown_error" not in proc.stderr


def test_init_after_shutdown_is_refused_and_builds_nothing() -> None:
    """The shut-down provider stays the process global; a new one could never be registered."""
    proc = _run("""
use_in_memory_exporters()
assert t.init_tracing()
t.shutdown_tracing()
again = t.init_tracing()
carrier = {}
with trace.get_tracer("host").start_as_current_span("on-the-shut-down-provider"):
    t.inject_trace_context(carrier)
emit(again=again, built=len(built), noop=isinstance(t.get_tracer("case"), t.NoOpTracer), carrier=carrier)
""")
    seen = _observed(proc)

    assert seen["again"] is False
    assert seen["built"] == 1
    assert seen["noop"] is True
    assert seen["carrier"] == {}
    assert "tracing_init_refused" in proc.stderr and "already_shut_down" in proc.stderr
    assert "Overriding of current TracerProvider" not in proc.stderr


def test_losing_the_registration_race_leaves_no_orphan_provider() -> None:
    """Another component registers between Hangar's check and its registration."""
    proc = _run("""
use_in_memory_exporters()
host = TracerProvider()
register = trace.set_tracer_provider

def racing_register(provider):
    register(host)
    register(provider)

trace.set_tracer_provider = racing_register
initialized = t.init_tracing()
emit(
    initialized=initialized,
    own_exporter_shut_down=built[0].export(()) is SpanExportResult.FAILURE,
    uses_host=not isinstance(t.get_tracer("case"), t.NoOpTracer),
)
""")
    seen = _observed(proc)

    assert seen["initialized"] is False
    assert seen["own_exporter_shut_down"] is True
    assert seen["uses_host"] is True
    assert "provider_registered_concurrently" in proc.stderr
    assert "tracing_initialized" not in proc.stderr


def test_shutdown_is_bounded_against_an_unreachable_collector() -> None:
    proc = _run("""
import socket
import time

# Listens, so the kernel completes the TCP handshake, and never answers: every
# export waits out the full OTLP deadline, as against a hung collector.
sink = socket.socket()
sink.bind(("127.0.0.1", 0))
sink.listen()
assert t.init_tracing(otlp_endpoint=f"http://127.0.0.1:{sink.getsockname()[1]}")
for i in range(3):
    with t.get_tracer("case").start_as_current_span(f"pending-{i}"):
        pass
start = time.monotonic()
t.shutdown_tracing()
emit(elapsed=time.monotonic() - start, bound=t.TRACING_SHUTDOWN_TIMEOUT_S)
""")
    seen = _observed(proc)

    assert seen["bound"] == 5.0
    assert seen["elapsed"] < seen["bound"] + 1.0, seen
    # The flush really was stuck, so the bound -- not a fast failure -- ended it.
    assert "tracing_shutdown_timed_out" in proc.stderr


def test_console_export_keeps_stdout_clean() -> None:
    """On the stdio transport stdout is the JSON-RPC stream; spans go to stderr."""
    proc = _run(
        """
t.OTLP_AVAILABLE = False  # console only: no collector for shutdown to wait on
from mcp_hangar.server.bootstrap.observability import _parse_observability_config, init_tracing, shutdown_observability

config = _parse_observability_config({})
assert config.tracing.console_export
assert init_tracing(config.tracing)
with t.get_tracer("case").start_as_current_span("console-span"):
    pass
shutdown_observability(None)
""",
        MCP_TRACING_CONSOLE="true",
    )

    assert proc.stdout == ""
    assert '"name": "console-span"' in proc.stderr
