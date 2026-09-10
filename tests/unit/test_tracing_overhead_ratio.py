"""SDK recording must stay within a generous multiple of tracing off (#1292).

A ratio, not a time: both arms run in this process, on this runner, over the
same ``hangar_call`` workload (``tests/benchmark/tracing_workload.py``), so
runner speed cancels out. Noise is handled by warming up, interleaving the arms
in alternating order and comparing medians of per-block medians. The bound only
catches a gross regression, such as export or serialization moving onto the
request path. The measured budget is #1303's to set, from
``tests/benchmark/tracing_overhead.py``.

The arms swap ``get_tracer`` in every loaded ``mcp_hangar`` module that
imported it -- batch, executor, command and event bus, and ``tracing`` itself
for the upstream CLIENT span -- for a provider built here or Hangar's
``NoOpTracer``. A span a later task adds through ``get_tracer`` is covered
without editing this test. Nothing global is touched: no global provider, no
``init_tracing``, no ``MCP_TRACING_ENABLED``. So the test does not depend on
how Hangar picks a provider, which #1283 changes, and it cannot be skewed by a
provider another test registered. The SDK is a hard requirement under CI
(``otel_sdk``) because the ``test`` job installs it.
"""

from __future__ import annotations

import statistics
import sys
import time

import pytest

pytestmark = pytest.mark.otel_sdk

BOUND = 3.0
CALLS, ITERATIONS, ROUNDS, WARMUP_ROUNDS = 10, 8, 10, 2


def test_sdk_recording_stays_within_three_times_tracing_off(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import mcp_hangar.server.context as server_context
    from mcp_hangar.observability import tracing
    from tests.benchmark.tracing_workload import TracingWorkload

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    noop = tracing.NoOpTracer()
    arm = ["off"]

    def routed(name: str = "") -> object:
        return provider.get_tracer(name) if arm[0] == "sdk" else noop

    workload = TracingWorkload()
    monkeypatch.setattr(server_context, "_context", workload.ctx)
    workload.run(CALLS)  # the call path imports some modules lazily
    original = tracing.get_tracer
    for module in [m for name, m in sys.modules.items() if name.startswith("mcp_hangar")]:
        if getattr(module, "get_tracer", None) is original:
            monkeypatch.setattr(module, "get_tracer", routed)

    def block(which: str) -> float:
        arm[0] = which
        samples = []
        for _ in range(ITERATIONS):
            start = time.perf_counter_ns()
            workload.run(CALLS)
            samples.append(time.perf_counter_ns() - start)
        provider.force_flush()
        exporter.clear()
        return statistics.median(samples)

    try:
        block("off")
        provider.force_flush()
        assert exporter.get_finished_spans() == (), "the off arm must record nothing"
        arm[0] = "sdk"
        workload.run(CALLS)
        provider.force_flush()
        assert len(exporter.get_finished_spans()) > CALLS, "the sdk arm must record every call"

        medians: dict[str, list[float]] = {"off": [], "sdk": []}
        for i in range(WARMUP_ROUNDS + ROUNDS):
            for which in ("off", "sdk") if i % 2 else ("sdk", "off"):
                medians[which].append(block(which))
        ratio = statistics.median(medians["sdk"][WARMUP_ROUNDS:]) / statistics.median(medians["off"][WARMUP_ROUNDS:])
    finally:
        workload.close()
        provider.shutdown()

    print(f"tracing overhead ratio sdk/off = {ratio:.3f}")
    assert ratio < BOUND, f"SDK recording costs {ratio:.2f}x tracing off (bound {BOUND}x)"
